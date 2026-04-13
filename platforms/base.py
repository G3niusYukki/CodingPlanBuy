import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from playwright.async_api import BrowserContext, Page

from core.browser import BrowserManager
from core.notifier import Notifier
from core.retry import RetryConfig, retry_async

logger = logging.getLogger(__name__)


class PurchaseStatus(Enum):
    SUCCESS = "success"
    SOLD_OUT = "sold_out"
    ERROR = "error"


@dataclass
class PurchaseResult:
    status: PurchaseStatus
    platform: str
    tier: str = ""
    message: str = ""
    error: Exception | None = None


class BaseBuyer(ABC):
    platform_name: str = ""
    purchase_url: str = ""

    def __init__(
        self,
        browser_manager: BrowserManager,
        notifier: Notifier,
        retry_config: RetryConfig | None = None,
    ):
        self.browser_manager = browser_manager
        self.notifier = notifier
        self.retry_config = retry_config or RetryConfig()
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @abstractmethod
    async def check_login(self, page: Page) -> bool:
        ...

    @abstractmethod
    async def is_available(self, page: Page) -> bool:
        ...

    @abstractmethod
    async def execute_purchase(self, page: Page) -> PurchaseResult:
        ...

    async def pre_warm(self) -> BrowserContext:
        self._context = await self.browser_manager.create_context(
            storage_state_path=self._auth_path()
        )
        self._page = await BrowserManager.new_page(self._context)
        await self._page.goto(self.purchase_url, wait_until="domcontentloaded")
        logger.info(f"[{self.platform_name}] Pre-warmed, current URL: {self._page.url}")

        # Session health check
        logged_in = await self.check_login(self._page)
        if not logged_in:
            msg = f"[{self.platform_name}] Session expired during pre-warm. Skipping purchase."
            logger.error(msg)
            self.notifier.failure(self.platform_name, "Session expired — run login command first")
            raise RuntimeError(f"Session expired for {self.platform_name}")

        return self._context

    async def run(self, context: BrowserContext | None = None) -> PurchaseResult:
        if context:
            self._context = context
        if not self._context:
            self._context = await self.browser_manager.create_context(
                storage_state_path=self._auth_path()
            )
        self._page = await BrowserManager.new_page(self._context) if not self._page else self._page

        try:
            logged_in = await self.check_login(self._page)
            if not logged_in:
                msg = f"[{self.platform_name}] Not logged in. Run 'snap-buy login {self.platform_name}' first."
                logger.error(msg)
                self.notifier.failure(self.platform_name, "Not logged in")
                return PurchaseResult(status=PurchaseStatus.ERROR, platform=self.platform_name, message=msg)

            available = await self.is_available(self._page)
            if not available:
                logger.warning(f"[{self.platform_name}] Not available yet, retrying...")

            result = await retry_async(self._purchase_with_retry, config=self.retry_config)
            if result.success:
                purchase_result: PurchaseResult = result.value
                self.notifier.success(self.platform_name, purchase_result.tier)
                return purchase_result
            else:
                self.notifier.failure(self.platform_name, str(result.last_error))
                return PurchaseResult(
                    status=PurchaseStatus.ERROR,
                    platform=self.platform_name,
                    error=result.last_error,
                )
        except Exception as e:
            logger.exception(f"[{self.platform_name}] Unexpected error: {e}")
            await self._take_screenshot("unexpected_error")
            self.notifier.failure(self.platform_name, str(e))
            return PurchaseResult(status=PurchaseStatus.ERROR, platform=self.platform_name, error=e)

    async def _purchase_with_retry(self) -> PurchaseResult:
        result = await self.execute_purchase(self._page)
        if result.status != PurchaseStatus.SUCCESS:
            raise RuntimeError(result.message or f"Purchase failed: {result.status.value}")
        return result

    def _auth_path(self) -> Path:
        auth_dir = Path(__file__).resolve().parent.parent / "auth"
        return auth_dir / f"{self.platform_name}_state.json"

    async def _wait_for_payment(
        self,
        page: Page,
        timeout: int = 120,
        success_indicators: list[str] | None = None,
        tier: str = "",
    ) -> PurchaseResult:
        """Wait for user to complete manual payment, polling for success."""
        logger.info(f"[{self.platform_name}] Order created! Waiting for manual payment ({timeout}s)...")
        self.notifier.notify(
            title="Snap Buy - 请支付",
            message=f"{self.platform_name}: 订单已创建，请在浏览器中完成支付！",
            sound=True,
        )

        deadline = asyncio.get_event_loop().time() + timeout
        poll_interval = 2

        while asyncio.get_event_loop().time() < deadline:
            # Check success indicators on the page
            if success_indicators:
                for selector in success_indicators:
                    element = await page.query_selector(selector)
                    if element:
                        logger.info(f"[{self.platform_name}] Payment confirmed!")
                        return PurchaseResult(
                            status=PurchaseStatus.SUCCESS,
                            platform=self.platform_name,
                            tier=tier,
                            message="Payment completed successfully",
                        )

            # Check URL for payment success hints
            current_url = page.url.lower()
            if any(kw in current_url for kw in ("success", "complete", "done", "result")):
                logger.info(f"[{self.platform_name}] Payment page detected success URL: {page.url}")
                return PurchaseResult(
                    status=PurchaseStatus.SUCCESS,
                    platform=self.platform_name,
                    tier=tier,
                    message=f"Payment success (URL): {page.url}",
                )

            remaining = int(deadline - asyncio.get_event_loop().time())
            if remaining > 0 and remaining % 30 == 0:
                logger.info(f"[{self.platform_name}] Still waiting for payment... {remaining}s remaining")

            await asyncio.sleep(poll_interval)

        # Timeout — terminal, do not retry (avoid creating duplicate orders)
        logger.warning(f"[{self.platform_name}] Payment wait timed out after {timeout}s")
        self.notifier.notify(
            title="Snap Buy - 支付超时",
            message=f"{self.platform_name}: 支付等待超时，请手动检查订单状态",
            sound=True,
        )
        raise RuntimeError(f"Payment wait timed out after {timeout}s — sold out")

    async def _take_screenshot(self, name: str) -> None:
        if self._page:
            logs_dir = Path(__file__).resolve().parent.parent / "logs" / "screenshots"
            logs_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = logs_dir / f"{self.platform_name}_{name}_{timestamp}.png"
            await self._page.screenshot(path=str(path))
            logger.info(f"Screenshot saved: {path}")
