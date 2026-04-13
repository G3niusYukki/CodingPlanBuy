import logging
import sys
from pathlib import Path

from core.config import NotificationConfig

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self, config: NotificationConfig):
        self.config = config
        self._log_file = Path(config.log_file)
        self._ensure_log_dir()

    def _ensure_log_dir(self) -> None:
        self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def notify(self, title: str, message: str, sound: bool = True) -> None:
        logger.info(f"[NOTIFICATION] {title}: {message}")
        if self.config.desktop_enabled:
            self._desktop_notify(title, message)
        if sound and self.config.sound_enabled:
            self._play_sound()

    def _desktop_notify(self, title: str, message: str) -> None:
        try:
            from plyer import notification as plyer_notify
            plyer_notify.notify(
                title=title,
                message=message,
                app_name="SnapBuy",
                timeout=10,
            )
        except Exception as e:
            logger.warning(f"Desktop notification failed: {e}")

    def _play_sound(self) -> None:
        sound_file = self.config.sound_file
        if sound_file and Path(sound_file).exists():
            self._play_wav(sound_file)
        else:
            self._system_beep()

    @staticmethod
    def _play_wav(path: str) -> None:
        try:
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                import subprocess
                subprocess.Popen(["aplay", path])
        except Exception as e:
            logger.warning(f"WAV playback failed: {e}")

    @staticmethod
    def _system_beep() -> None:
        try:
            if sys.platform == "win32":
                import winsound
                winsound.Beep(1000, 500)
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception:
            pass

    def success(self, platform: str, tier: str = "") -> None:
        detail = f" - {tier}" if tier else ""
        self.notify(
            title="Snap Buy SUCCESS",
            message=f"Purchased on {platform}{detail}!",
            sound=True,
        )

    def failure(self, platform: str, reason: str) -> None:
        self.notify(
            title="Snap Buy FAILED",
            message=f"{platform}: {reason}",
            sound=True,
        )

    def info(self, message: str) -> None:
        self.notify(title="Snap Buy", message=message, sound=False)
