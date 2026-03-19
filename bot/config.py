"""Конфигурация бота."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _parse_int_list(value: str) -> List[int]:
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip()]


@dataclass
class Config:
    vk_token: str = field(default_factory=lambda: os.getenv("VK_TOKEN", ""))
    vk_group_id: int = field(default_factory=lambda: int(os.getenv("VK_GROUP_ID", "0")))
    encryption_key: str = field(default_factory=lambda: os.getenv("ENCRYPTION_KEY", ""))
    admin_ids: List[int] = field(default_factory=lambda: _parse_int_list(os.getenv("ADMIN_IDS", "")))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    check_interval_seconds: int = field(default_factory=lambda: int(os.getenv("CHECK_INTERVAL_SECONDS", "300")))
    default_balance_threshold: float = field(default_factory=lambda: float(os.getenv("DEFAULT_BALANCE_THRESHOLD", "300.0")))
    cache_ttl_seconds: int = field(default_factory=lambda: int(os.getenv("CACHE_TTL_SECONDS", "300")))

    base_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    data_dir: Path = field(init=False)

    def __post_init__(self):
        self.data_dir = self.base_dir / "data"
        self.data_dir.mkdir(exist_ok=True)

        if not self.vk_token:
            raise ValueError("VK_TOKEN не указан")
        if not self.encryption_key:
            raise ValueError("ENCRYPTION_KEY не указан")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ruobr_bot.db"

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


config = Config()
