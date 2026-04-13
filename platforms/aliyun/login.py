import logging

from core.browser import BrowserManager
from platforms.base_login import BaseLoginHandler

logger = logging.getLogger(__name__)


class AliyunLoginHandler(BaseLoginHandler):
    login_url = "https://login.aliyun.com/"
    verify_url = "https://account.aliyun.com/"
    platform_name = "aliyun"

    def __init__(self, browser_manager: BrowserManager):
        super().__init__(browser_manager)
