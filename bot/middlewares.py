"""
Middleware для бота: rate limiting, аутентификация, логирование.
"""
import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from vkbottle import BaseMiddleware
from vkbottle_types.events import MessageNew

from .config import config
from .database import get_user, UserConfig

logger = logging.getLogger(__name__)


@dataclass
class RateLimitEntry:
    """Запись для отслеживания rate limit."""
    timestamps: list
    blocked_until: float = 0


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware для ограничения частоты запросов.
    Использует алгоритм sliding window.
    """

    def __init__(
        self,
        limit: int = 30,
        window_seconds: int = 60,
        block_duration: int = 30
    ):
        """
        Инициализация middleware.

        Args:
            limit: Максимальное количество запросов в окне.
            window_seconds: Размер окна в секундах.
            block_duration: Длительность блокировки в секундах.
        """
        self._limit = limit
        self._window = window_seconds
        self._block_duration = block_duration
        self._entries: Dict[int, RateLimitEntry] = defaultdict(
            lambda: RateLimitEntry(timestamps=[])
        )
        self._cleanup_interval = 3600  # Очистка каждый час
        self._last_cleanup = time.time()

    async def pre(self, event: MessageNew) -> Optional[dict]:
        """Обработка события с проверкой rate limit."""
        user_id = event.message.from_id

        if user_id == 0:
            return {}

        # Админы не ограничены
        if config.is_admin(user_id):
            return {}

        current_time = time.time()
        entry = self._entries[user_id]

        # Проверка на блокировку
        if entry.blocked_until > current_time:
            remaining = int(entry.blocked_until - current_time)
            try:
                await self.event_api.messages.send(
                    user_id=user_id,
                    message=f"⚠️ Слишком много запросов. Подождите {remaining} сек.",
                    random_id=0
                )
            except Exception:
                pass
            self.block("Rate limit exceeded")
            return None

        # Очистка старых timestamp'ов
        cutoff = current_time - self._window
        entry.timestamps = [ts for ts in entry.timestamps if ts > cutoff]

        # Проверка лимита
        if len(entry.timestamps) >= self._limit:
            entry.blocked_until = current_time + self._block_duration
            logger.warning(
                f"Rate limit exceeded for user {user_id}: "
                f"{len(entry.timestamps)} requests in {self._window}s"
            )
            try:
                await self.event_api.messages.send(
                    user_id=user_id,
                    message=f"⚠️ Превышен лимит запросов. Попробуйте через {self._block_duration} сек.",
                    random_id=0
                )
            except Exception:
                pass
            self.block("Rate limit exceeded")
            return None

        # Добавляем текущий запрос
        entry.timestamps.append(current_time)

        # Периодическая очистка памяти
        if current_time - self._last_cleanup > self._cleanup_interval:
            self._cleanup_old_entries(current_time)
            self._last_cleanup = current_time

        return {}

    def _cleanup_old_entries(self, current_time: float) -> None:
        """Очистка старых записей для экономии памяти."""
        cutoff = current_time - self._window * 2
        to_remove = []

        for user_id, entry in self._entries.items():
            # Удаляем старые timestamp'ы
            entry.timestamps = [ts for ts in entry.timestamps if ts > cutoff]
            # Отмечаем пустые записи для удаления
            if not entry.timestamps and entry.blocked_until < current_time:
                to_remove.append(user_id)

        for user_id in to_remove:
            del self._entries[user_id]

        if to_remove:
            logger.debug(f"Cleaned up {len(to_remove)} rate limit entries")


class AuthMiddleware(BaseMiddleware):
    """
    Middleware для проверки аутентификации пользователя.
    Добавляет user_config в data для использования в handlers.
    """

    async def pre(self, event: MessageNew) -> dict:
        """Добавление конфигурации пользователя в контекст."""
        peer_id = event.message.peer_id

        # Получаем конфигурацию пользователя
        user_config = await get_user(peer_id)

        # Добавляем в data для использования в handlers
        return {
            "user_config": user_config,
            "is_authenticated": (
                user_config is not None
                and user_config.login is not None
                and user_config.password is not None
            )
        }


class LoggingMiddleware(BaseMiddleware):
    """
    Middleware для логирования сообщений.
    """

    async def pre(self, event: MessageNew) -> dict:
        """Логирование входящего сообщения."""
        user_id = event.message.from_id
        peer_id = event.message.peer_id
        text = event.message.text or "<non-text>"

        logger.info(
            f"Message from user {user_id} "
            f"in peer {peer_id}: {text[:100]}"
        )

        return {}


class ThrottlingMiddleware(BaseMiddleware):
    """
    Middleware для предотвращения флуда.
    Блокирует обработку если предыдущий запрос ещё обрабатывается.
    """

    def __init__(self):
        self._processing: Dict[int, bool] = defaultdict(bool)
        self._lock = asyncio.Lock()

    async def pre(self, event: MessageNew) -> Optional[dict]:
        user_id = event.message.from_id

        if user_id == 0:
            return {}

        # Проверяем, обрабатывается ли уже запрос от этого пользователя
        async with self._lock:
            if self._processing[user_id]:
                logger.debug(f"Skipping duplicate request from user {user_id}")
                self.block("Duplicate request")
                return None
            self._processing[user_id] = True

        return {}

    async def post(self, event: MessageNew, data: dict) -> dict:
        """Освобождение блокировки после обработки."""
        user_id = event.message.from_id
        async with self._lock:
            self._processing[user_id] = False
        return data
