import asyncio
import logging

from playwright.async_api import Page

from core.browser import BrowserManager
from core.config import AliyunConfig
from core.notifier import Notifier
from core.retry import RetryConfig
from platforms.base import BaseBuyer, PurchaseResult, PurchaseStatus

logger = logging.getLogger(__name__)


class AliyunBuyer(BaseBuyer):
    platform_name = "aliyun"
    purchase_url = "https://www.aliyun.com/benefit/scene/codingplan"

    # Landing page: entry button to subscription page
    ENTRY_SELECTORS = [
        'a:has-text("马上抢购")',
        'button:has-text("马上抢购")',
    ]

    # Subscription page: actual purchase button
    SUBSCRIBE_SELECTORS = [
        'a:has-text("订阅")',
        'button:has-text("订阅")',
        'button:has-text("立即订阅")',
        'button:has-text("立即购买")',
        'a:has-text("立即订阅")',
        'a:has-text("立即购买")',
        '#J_buy_btn',
        '.buy-btn',
        '[data-spm="buy"]',
        '.commodity-buy',
    ]

    SOLD_OUT_SELECTORS = [
        ':has-text("已售罄")',
        ':has-text("暂不可购买")',
        ':has-text("已售完")',
        ':has-text("售罄")',
    ]

    SUCCESS_SELECTORS = [
        ':has-text("订单创建成功")',
        ':has-text("支付成功")',
        ':has-text("开通成功")',
    ]

    CONFIRM_SELECTORS = [
        'button:has-text("确认")',
        'button:has-text("提交订单")',
        'a:has-text("确认")',
        'a:has-text("提交订单")',
    ]

    def __init__(
        self,
        config: AliyunConfig,
        browser_manager: BrowserManager,
        notifier: Notifier,
        retry_config: RetryConfig | None = None,
    ):
        super().__init__(browser_manager, notifier, retry_config)
        self.config = config
        self.purchase_url = config.url

    async def check_login(self, page: Page) -> bool:
        await page.goto(self.purchase_url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        if "login" in page.url.lower():
            return False
        return True

    async def is_available(self, page: Page) -> bool:
        for selector in self.SOLD_OUT_SELECTORS:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                return False
        for selector in self.ENTRY_SELECTORS:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                return True
        return False

    async def execute_purchase(self, page: Page) -> PurchaseResult:
        context = page.context

        # Clean up extra tabs from previous attempts
        if len(context.pages) > 1:
            for extra_page in context.pages[1:]:
                try:
                    await extra_page.close()
                except Exception:
                    pass

        # Step 1: Navigate to landing page
        if self.purchase_url not in page.url:
            await page.goto(self.purchase_url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

        await self._debug_capture(page, "01_landing")

        # Step 2: Click "马上抢购" entry button (may open new tab)
        pages_before = set(context.pages)

        clicked_entry = False
        for selector in self.ENTRY_SELECTORS:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                await element.click()
                clicked_entry = True
                logger.info(f"Clicked entry button: {selector}")
                break

        if not clicked_entry:
            await self._debug_capture(page, "02_no_entry_button")
            return PurchaseResult(
                status=PurchaseStatus.ERROR,
                platform=self.platform_name,
                message="Could not find entry button (马上抢购)",
                retryable=False,
            )

        # Step 3: Detect new tab or same-page navigation
        await asyncio.sleep(1)
        new_pages = [p for p in context.pages if p not in pages_before]

        if new_pages:
            target_page = new_pages[0]
            await target_page.bring_to_front()
            page = target_page
            logger.info(f"New tab opened, switched to: {page.url}")
        else:
            logger.info(f"Same-page navigation, URL: {page.url}")
            # Wait for potential SPA navigation
            try:
                await page.wait_for_url("**/common-buy.aliyun.com/**", timeout=5000)
                logger.info(f"[aliyun] Same-page navigated to: {page.url}")
            except Exception:
                # URL didn't change — subscribe form may be below the fold
                logger.info("[aliyun] URL unchanged, scrolling to find subscribe form")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)

        # Wait for the subscription page to render (JS-heavy page)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            logger.warning("[aliyun] domcontentloaded timeout, proceeding anyway")

        # Extra wait for JS-rendered content
        await asyncio.sleep(2)
        logger.info(f"Subscription page loaded: {page.url}")

        await self._debug_capture(page, "03_subscribe_page")

        # Step 4: Check sold out BEFORE clicking subscribe
        for selector in self.SOLD_OUT_SELECTORS:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                logger.info("[aliyun] Sold out detected on subscription page")
                return PurchaseResult(
                    status=PurchaseStatus.SOLD_OUT,
                    platform=self.platform_name,
                    message="Product sold out",
                )

        # Step 5: Click subscribe button — use wait_for_selector for JS-rendered content
        clicked_subscribe = False
        for selector in self.SUBSCRIBE_SELECTORS:
            try:
                element = await page.wait_for_selector(selector, state="visible", timeout=3000)
            except Exception:
                continue
            if element:
                is_disabled = await element.get_attribute("disabled")
                if is_disabled:
                    logger.warning(f"[aliyun] Subscribe button disabled: {selector}")
                    continue
                await element.click()
                clicked_subscribe = True
                logger.info(f"Clicked subscribe button: {selector}")
                break

        if not clicked_subscribe:
            await self._debug_capture(page, "04_no_subscribe_button")
            return PurchaseResult(
                status=PurchaseStatus.SOLD_OUT,
                platform=self.platform_name,
                message="Subscribe button not found or disabled",
                retryable=False,
            )

        # Wait for page transition
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(0.5)

        await self._debug_capture(page, "05_after_subscribe")

        # Step 6: Check for confirmation dialog
        for selector in self.CONFIRM_SELECTORS:
            try:
                element = await page.wait_for_selector(selector, state="visible", timeout=3000)
                if element:
                    await element.click()
                    logger.info(f"Clicked confirm button: {selector}")
                    break
            except Exception:
                continue

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(0.5)

        await self._debug_capture(page, "06_after_confirm")

        # Step 7: Check for immediate success
        for selector in self.SUCCESS_SELECTORS:
            element = await page.query_selector(selector)
            if element:
                return PurchaseResult(
                    status=PurchaseStatus.SUCCESS,
                    platform=self.platform_name,
                    message="Purchase completed successfully",
                )

        # Step 8: If on payment page, wait for manual payment
        current_url = page.url.lower()
        if any(kw in current_url for kw in ("pay", "order", "cashier")):
            return await self._wait_for_payment(
                page,
                timeout=self.config.payment_timeout,
                success_indicators=self.SUCCESS_SELECTORS,
            )

        await self._debug_capture(page, "07_un unclear_status")
        return PurchaseResult(
            status=PurchaseStatus.ERROR,
            platform=self.platform_name,
            message="Purchase status unclear - no success indicator found",
        )
