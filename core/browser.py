import logging
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

from core.config import BrowserConfig

_stealth = Stealth(
    navigator_languages_override=("zh-CN", "zh"),
    navigator_platform_override="Win32",
)

logger = logging.getLogger(__name__)

AUTH_DIR = Path(__file__).resolve().parent.parent / "auth"

__all__ = ["BrowserManager", "AUTH_DIR"]

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
]


class BrowserManager:
    def __init__(self, config: BrowserConfig):
        self.config = config
        self._playwright = None
        self._browser: Browser | None = None

    async def launch(self) -> Browser:
        self._playwright = await async_playwright().start()

        launch_kwargs = {
            "headless": self.config.headless,
            "slow_mo": self.config.slow_mo,
            "args": LAUNCH_ARGS,
        }
        if self.config.proxy:
            launch_kwargs["proxy"] = {"server": self.config.proxy}
            # Log proxy host without credentials
            from urllib.parse import urlparse
            parsed = urlparse(self.config.proxy)
            safe = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.hostname else self.config.proxy
            logger.info(f"Using proxy: {safe}")

        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        return self._browser

    async def create_context(
        self,
        storage_state_path: Path | None = None,
    ) -> BrowserContext:
        if self._browser is None:
            await self.launch()

        state = None
        if storage_state_path and storage_state_path.exists():
            logger.info(f"Loading session state from {storage_state_path}")
            state = str(storage_state_path)

        context = await self._browser.new_context(
            storage_state=state,
            viewport=self.config.viewport,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        await _stealth.apply_stealth_async(context)
        return context

    @staticmethod
    async def save_state(context: BrowserContext, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(path))
        logger.info(f"Session state saved to {path}")

    @staticmethod
    async def new_page(context: BrowserContext) -> Page:
        page = await context.new_page()
        return page

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
