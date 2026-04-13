from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class BrowserConfig(BaseModel):
    headless: bool = False
    slow_mo: int = Field(default=50, ge=0, le=1000)
    viewport_width: int = Field(default=1920, ge=800)
    viewport_height: int = Field(default=1080, ge=600)
    proxy: str | None = None

    @property
    def viewport(self) -> dict:
        return {"width": self.viewport_width, "height": self.viewport_height}


class AliyunConfig(BaseModel):
    enabled: bool = True
    purchase_time: str = "09:30:00"
    url: str = "https://common-buy.aliyun.com/coding-plan"
    max_retries: int = Field(default=5, ge=1, le=20)
    retry_delay_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    payment_timeout: int = Field(default=120, ge=30, le=600)

    @field_validator("purchase_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 3:
            raise ValueError(f"Invalid time format: {v}. Expected HH:MM:SS")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
            raise ValueError(f"Invalid time: {v}")
        return v


class GLMConfig(BaseModel):
    enabled: bool = True
    purchase_time: str = "10:00:00"
    url: str = "https://www.bigmodel.cn/glm-coding"
    max_retries: int = Field(default=5, ge=1, le=20)
    retry_delay_seconds: float = Field(default=1.0, ge=0.1, le=10.0)
    payment_timeout: int = Field(default=120, ge=30, le=600)
    priority: list[str] = Field(default=["Pro", "Lite", "Max"])

    @field_validator("purchase_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 3:
            raise ValueError(f"Invalid time format: {v}. Expected HH:MM:SS")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
            raise ValueError(f"Invalid time: {v}")
        return v

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: list[str]) -> list[str]:
        valid = {"Pro", "Lite", "Max"}
        for tier in v:
            if tier not in valid:
                raise ValueError(f"Invalid tier: {tier}. Must be one of {valid}")
        return v


class NotificationConfig(BaseModel):
    sound_enabled: bool = True
    sound_file: str | None = None
    desktop_enabled: bool = True
    log_file: str = "logs/snap_buy.log"


class SchedulerConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    pre_warm_seconds: int = Field(default=30, ge=5, le=120)


class PlatformsConfig(BaseModel):
    aliyun: AliyunConfig = AliyunConfig()
    glm: GLMConfig = GLMConfig()


class AppConfig(BaseModel):
    scheduler: SchedulerConfig = SchedulerConfig()
    platforms: PlatformsConfig = Field(default_factory=lambda: PlatformsConfig())
    browser: BrowserConfig = BrowserConfig()
    notification: NotificationConfig = NotificationConfig()


def load_config(path: Path | None = None) -> AppConfig:
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ValueError("Config file is empty")
    return AppConfig(**raw)


def generate_example_config(path: Path | None = None) -> Path:
    config_path = path or PROJECT_ROOT / "config.example.yaml"
    example = AppConfig().model_dump()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(example, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return config_path
