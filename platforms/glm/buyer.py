import asyncio
import logging

from playwright.async_api import Page

from core.browser import BrowserManager
from core.config import GLMConfig
from core.notifier import Notifier
from core.retry import RetryConfig
from platforms.base import BaseBuyer, PurchaseResult, PurchaseStatus

logger = logging.getLogger(__name__)


class GLMBuyer(BaseBuyer):
    platform_name = "glm"
    purchase_url = "https://www.bigmodel.cn/glm-coding"

    TIER_SELECTORS = {
        "Lite": {
            "tab": '[data-tier="lite"], :has-text("Lite")',
            "card": ".plan-card.lite, .tier-card.lite",
        },
        "Pro": {
            "tab": '[data-tier="pro"], :has-text("Pro")',
            "card": ".plan-card.pro, .tier-card.pro",
        },
        "Max": {
            "tab": '[data-tier="max"], :has-text("Max")',
            "card": ".plan-card.max, .tier-card.max",
        },
    }

    PURCHASE_BUTTON_SELECTORS = [
        # Tag + text combinations
        'button:has-text("立即购买")',
        'button:has-text("立即订阅")',
        'button:has-text("马上抢购")',
        'a:has-text("立即购买")',
        'a:has-text("立即订阅")',
        'a:has-text("马上抢购")',
        # Generic text match (may match elements with nested spans)
        ':text("立即购买")',
        ':text("立即订阅")',
        ':text("马上抢购")',
        # Class-based
        ".purchase-btn",
        ".subscribe-btn",
        ".buy-btn",
        ".order-btn",
        # Data-attribute based
        '[data-action="buy"]',
        '[data-action="subscribe"]',
    ]

    SOLD_OUT_SELECTORS = [
        ':has-text("已售罄")',
        ':has-text("暂不可购买")',
        ':has-text("已售完")',
        ':has-text("暂无库存")',
    ]

    SUCCESS_SELECTORS = [
        ':has-text("支付成功")',
        ':has-text("订单创建成功")',
        ':has-text("开通成功")',
        ':has-text("订阅成功")',
    ]

    def __init__(
        self,
        config: GLMConfig,
        browser_manager: BrowserManager,
        notifier: Notifier,
        retry_config: RetryConfig | None = None,
    ):
        super().__init__(browser_manager, notifier, retry_config)
        self.config = config
        self.purchase_url = config.url
        self.priority = config.priority

    async def check_login(self, page: Page) -> bool:
        # First visit the login domain to ensure session cookies are loaded
        await page.goto("https://open.bigmodel.cn/usercenter", wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        if "login" in page.url.lower():
            return False

        # Now navigate to the purchase domain — cookies carry over via same BrowserContext
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
        return True

    async def _select_tier(self, page: Page, tier: str) -> bool:
        tier_data = self.TIER_SELECTORS.get(tier)
        if not tier_data:
            logger.warning(f"Unknown tier: {tier}")
            return False

        for selector_key in ("tab", "card"):
            selector = tier_data[selector_key]
            try:
                element = await page.wait_for_selector(selector, state="visible", timeout=5000)
            except Exception:
                continue
            if element:
                await element.click()
                logger.info(f"Selected {tier} tier via {selector_key} selector")
                await asyncio.sleep(0.5)
                return True

        logger.warning(f"Could not find {tier} tier element on page")
        return False

    async def _is_tier_available(self, page: Page, tier: str) -> bool:
        tier_data = self.TIER_SELECTORS.get(tier)
        if not tier_data:
            return False

        # Check for sold-out within the tier's card
        card_selector = tier_data.get("card", "")
        if card_selector:
            card = await page.query_selector(card_selector)
            if card:
                for sold_out_sel in self.SOLD_OUT_SELECTORS:
                    sold_el = await card.query_selector(sold_out_sel)
                    if sold_el:
                        return False
        return True

    async def execute_purchase(self, page: Page) -> PurchaseResult:
        if self.purchase_url not in page.url:
            await page.goto(self.purchase_url, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

        await self._debug_capture(page, "01_glm_landing")

        # Try tiers in priority order
        for tier in self.priority:
            logger.info(f"[GLM] Attempting tier: {tier}")

            # Select the tier
            selected = await self._select_tier(page, tier)
            if not selected:
                logger.warning(f"[GLM] Tier {tier} selector not found, skipping")
                continue

            # Check if this tier is available
            available = await self._is_tier_available(page, tier)
            if not available:
                logger.info(f"[GLM] Tier {tier} is sold out, trying next")
                continue

            await self._debug_capture(page, f"02_tier_{tier}_selected")

            # Click purchase button — first try page-wide, then within tier card
            clicked = False

            for selector in self.PURCHASE_BUTTON_SELECTORS:
                try:
                    element = await page.wait_for_selector(selector, state="visible", timeout=3000)
                except Exception:
                    continue
                if element:
                    await element.click()
                    clicked = True
                    logger.info(f"Clicked purchase button for {tier}: {selector}")
                    break

            # If page-wide search failed, try within tier card
            if not clicked:
                tier_data = self.TIER_SELECTORS.get(tier)
                if tier_data and tier_data.get("card"):
                    card = await page.query_selector(tier_data["card"])
                    if card:
                        for selector in self.PURCHASE_BUTTON_SELECTORS:
                            element = await card.query_selector(selector)
                            if element and await element.is_visible():
                                await element.click()
                                clicked = True
                                logger.info(f"Clicked purchase button for {tier} (in card): {selector}")
                                break

            if not clicked:
                logger.warning(f"[GLM] No purchase button found for tier {tier}")
                await self._debug_capture(page, f"03_no_button_{tier}")
                continue

            # Wait for page transition
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await asyncio.sleep(1)

            await self._debug_capture(page, f"04_after_click_{tier}")

            # Check for confirmation dialog
            confirm_selectors = [
                'button:has-text("确认")',
                'button:has-text("确定")',
                'button:has-text("提交订单")',
                'a:has-text("确认")',
            ]
            for sel in confirm_selectors:
                try:
                    element = await page.wait_for_selector(sel, state="visible", timeout=3000)
                    if element:
                        await element.click()
                        logger.info("Clicked confirmation button")
                        break
                except Exception:
                    continue

            # Wait for page transition after confirmation
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(1)

            # Check for immediate success
            for sel in self.SUCCESS_SELECTORS:
                element = await page.query_selector(sel)
                if element:
                    return PurchaseResult(
                        status=PurchaseStatus.SUCCESS,
                        platform=self.platform_name,
                        tier=tier,
                        message=f"Successfully purchased {tier} tier",
                    )

            # If redirected to payment page, wait for manual payment
            current_url = page.url.lower()
            if any(kw in current_url for kw in ("pay", "order", "cashier")):
                return await self._wait_for_payment(
                    page,
                    timeout=self.config.payment_timeout,
                    success_indicators=self.SUCCESS_SELECTORS,
                )

            # Purchase flow might have failed for this tier, try next
            logger.info(f"[GLM] Tier {tier} purchase unclear, trying next tier")
            await self._debug_capture(page, f"05_unclear_{tier}")
            # Navigate back if needed
            if self.purchase_url not in page.url:
                await page.goto(self.purchase_url, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

        return PurchaseResult(
            status=PurchaseStatus.SOLD_OUT,
            platform=self.platform_name,
            message=f"All tiers sold out or unavailable. Tried: {', '.join(self.priority)}",
        )
