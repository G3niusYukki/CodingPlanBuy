import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.config import AppConfig

logger = logging.getLogger(__name__)


class PurchaseScheduler:
    def __init__(self, config: AppConfig):
        self.config = config
        self.tz = ZoneInfo(config.scheduler.timezone)
        self._scheduler = AsyncIOScheduler(timezone=self.tz)
        self._notifier = None

    def set_notifier(self, notifier) -> None:
        """Set notifier for error notifications from scheduler."""
        self._notifier = notifier

    def _parse_purchase_time(self, time_str: str) -> tuple[int, int, int]:
        parts = time_str.split(":")
        return int(parts[0]), int(parts[1]), int(parts[2])

    async def align_to_target(self, target: datetime) -> None:
        """Busy-wait for sub-second precision alignment."""
        now = datetime.now(self.tz)
        delta = (target - now).total_seconds()
        if delta > 5:
            await asyncio.sleep(delta - 5)
        while datetime.now(self.tz) < target:
            await asyncio.sleep(0.02)

    def schedule_platform(
        self,
        platform_name: str,
        time_str: str,
        coro_func,
        pre_warm_seconds: int | None = None,
    ) -> None:
        h, m, s = self._parse_purchase_time(time_str)
        warm_sec = pre_warm_seconds or self.config.scheduler.pre_warm_seconds

        async def wrapped():
            target = datetime.now(self.tz).replace(hour=h, minute=m, second=s, microsecond=0)
            warm_target = target - timedelta(seconds=warm_sec)
            logger.info(f"[{platform_name}] Pre-warming at {warm_target.strftime('%H:%M:%S')}")

            try:
                # Pre-warm: launch browser and navigate
                context = await coro_func(pre_warm=True)

                logger.info(f"[{platform_name}] Aligning to target time {target.strftime('%H:%M:%S')}")
                await self.align_to_target(target)

                logger.info(f"[{platform_name}] Triggering purchase at {datetime.now(self.tz).strftime('%H:%M:%S.%f')}")
                result = await coro_func(pre_warm=False, context=context)

                logger.info(f"[{platform_name}] Purchase result: {result}")
            except Exception as e:
                logger.exception(f"[{platform_name}] Scheduler job failed: {e}")
                if self._notifier:
                    self._notifier.failure(platform_name, f"Scheduler error: {e}")

        trigger = CronTrigger(hour=h, minute=m, second=s, timezone=self.tz)
        job_id = f"{platform_name}_purchase"
        self._scheduler.add_job(wrapped, trigger, id=job_id, name=f"{platform_name} purchase")
        logger.info(f"Scheduled {platform_name} at {time_str} ({self.config.scheduler.timezone})")

    async def run_immediate(self, coro_func) -> None:
        """Run a purchase job immediately without scheduling (for --now mode)."""
        logger.info("Running immediate purchase (no scheduling)")
        try:
            context = await coro_func(pre_warm=True)
            result = await coro_func(pre_warm=False, context=context)
            logger.info(f"Immediate purchase result: {result}")
        except Exception as e:
            logger.exception(f"Immediate purchase failed: {e}")
            if self._notifier:
                self._notifier.failure("immediate", f"Error: {e}")

    def start(self) -> None:
        logger.info("Scheduler started")
        self._scheduler.start()

    def stop(self) -> None:
        logger.info("Scheduler stopped")
        self._scheduler.shutdown(wait=False)
