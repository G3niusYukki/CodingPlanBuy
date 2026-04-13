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
    purchase_url = "https://common-buy.aliyun.com/coding-plan"

    # Selectors - may need updating if the page changes
    SELECTORS = {
        "purchase_button": [
            'button:has-text("立即购买")',
            'button:has-text("马上抢购")',
            'a:has-text("立即购买")',
            'a:has-text("马上抢购")',
            '#J_buy_btn',
            '.buy-btn',
        ],
        "sold_out_indicator": [
            ':has-text("已售罄")',
            ':has-text("暂不可购买")',
            ':has-text("已售完")',
        ],
        "success_indicator": [
            ':has-text("订单创建成功")',
            ':has-text("支付成功")',
            ':has-text("开通成功")',
        ],
        "confirm_button": [
            'button:has-text("确认")',
            'button:has-text("提交订单")',
        ],
    }

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
        await page.wait_for_load_state("networkidle", timeout=15000)
        if "login" in page.url.lower():
            return False
        return True

    async def is_available(self, page: Page) -> bool:
        # Check for "sold out" indicators
        for selector in self.SELECTORS["sold_out_indicator"]:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                return False

        # Check if purchase button exists and is enabled
        for selector in self.SELECTORS["purchase_button"]:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                is_disabled = await element.get_attribute("disabled")
                if not is_disabled:
                    return True
        return False

    async def execute_purchase(self, page: Page) -> PurchaseResult:
        # Ensure we're on the purchase page
        if self.purchase_url not in page.url:
            await page.goto(self.purchase_url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle", timeout=15000)

        # Wait for availability
        available = await self.is_available(page)
        if not available:
            return PurchaseResult(
                status=PurchaseStatus.SOLD_OUT,
                platform=self.platform_name,
                message="Product not available or sold out",
            )

        # Click the purchase button
        clicked = False
        for selector in self.SELECTORS["purchase_button"]:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                await element.click()
                clicked = True
                logger.info(f"Clicked purchase button: {selector}")
                break

        if not clicked:
            return PurchaseResult(
                status=PurchaseStatus.ERROR,
                platform=self.platform_name,
                message="Could not find purchase button",
            )

        # Wait for potential confirmation dialog
        await page.wait_for_load_state("networkidle", timeout=10000)
        await asyncio.sleep(1)

        # Check for confirmation button
        for selector in self.SELECTORS["confirm_button"]:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                await element.click()
                logger.info(f"Clicked confirm button: {selector}")
                break

        # Wait for page transition after confirmation
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(1)

        # Check for immediate success (no payment needed)
        for selector in self.SELECTORS["success_indicator"]:
            element = await page.query_selector(selector)
            if element:
                return PurchaseResult(
                    status=PurchaseStatus.SUCCESS,
                    platform=self.platform_name,
                    message="Purchase completed successfully (no payment needed)",
                )

        # If redirected to payment page, wait for manual payment
        current_url = page.url.lower()
        if any(kw in current_url for kw in ("pay", "order", "cashier")):
            return await self._wait_for_payment(
                page,
                timeout=self.config.payment_timeout,
                success_indicators=self.SELECTORS["success_indicator"],
            )

        return PurchaseResult(
            status=PurchaseStatus.ERROR,
            platform=self.platform_name,
            message="Purchase status unclear - no success indicator found",
        )
