import logging

from core.browser import BrowserManager
from platforms.base_login import BaseLoginHandler

logger = logging.getLogger(__name__)


class GLMLoginHandler(BaseLoginHandler):
    login_url = "https://open.bigmodel.cn/login"
    verify_url = "https://open.bigmodel.cn/usercenter"
    platform_name = "glm"

    def __init__(self, browser_manager: BrowserManager):
        super().__init__(browser_manager)
