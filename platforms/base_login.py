import asyncio
import logging

from playwright.async_api import BrowserContext, Page

from core.browser import BrowserManager, AUTH_DIR

logger = logging.getLogger(__name__)


class BaseLoginHandler:
    """Base class for platform login handlers."""

    login_url: str = ""
    verify_url: str = ""
    platform_name: str = ""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager
        self.auth_path = AUTH_DIR / f"{self.platform_name}_state.json"

    async def login(self) -> BrowserContext:
        logger.info(f"Opening {self.platform_name} login page for manual authentication...")

        context = await self.browser_manager.create_context()
        page = await BrowserManager.new_page(context)

        await page.goto(self.login_url, wait_until="domcontentloaded")
        logger.info("Waiting for manual login... Press Enter in console when done.")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, ">>> Press Enter after you have completed login in the browser...")

        # Verify login
        await page.goto(self.verify_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")

        if "login" in page.url.lower():
            logger.error(f"{self.platform_name} login verification failed")
            raise RuntimeError("Login failed: still on login page after manual attempt")

        logger.info(f"{self.platform_name} login verified! Saving session state...")
        await BrowserManager.save_state(context, self.auth_path)
        logger.info(f"Session state saved to {self.auth_path}")
        return context

    async def check_and_reauth(self, context: BrowserContext) -> bool:
        page = await BrowserManager.new_page(context)
        try:
            await page.goto(self.verify_url, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle")

            if "login" in page.url.lower():
                logger.warning(f"{self.platform_name} session expired, re-authentication needed")
                return False
            logger.info(f"{self.platform_name} session is valid")
            return True
        finally:
            await page.close()
