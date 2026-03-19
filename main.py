#!/usr/bin/env python3
"""
Ruobr VK Bot - Главный файл запуска.

Улучшенная версия с:
- Модульной архитектурой
- Шифрованием паролей
- Асинхронными вызовами API
- Rate limiting
- Кэшированием
- Персистентным FSM
"""
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from vkbottle import Bot
from vkbottle.api import API
from vkbottle.polling import GroupPolling

from bot.config import config
from bot.database import db_pool
from bot.handlers import auth, balance, schedule
from bot.services.notifications import NotificationService
from bot.services.cache import periodic_cache_cleanup


# Настройка логирования
def setup_logging() -> None:
    """Настройка системы логирования."""
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(
                config.data_dir / "bot.log",
                encoding="utf-8"
            )
        ]
    )

    # Уменьшаем уровень логирования для vkbottle
    logging.getLogger("vkbottle").setLevel(logging.INFO)


logger = logging.getLogger(__name__)


async def main() -> None:
    """Главная функция запуска бота."""
    # Настройка логирования
    setup_logging()
    logger.info("Starting Ruobr VK Bot v2.0")

    # Инициализация базы данных
    await db_pool.initialize()
    logger.info("Database initialized")

    # Создание API и Bot
    api = API(token=config.vk_token)
    bot = Bot(api=api)

    # Регистрация handlers (blueprints)
    bot.labeler.load(auth.bp)
    bot.labeler.load(balance.bp)
    bot.labeler.load(schedule.bp)

    # Сервис уведомлений
    notification_service = NotificationService(api)

    # Запуск фоновых задач
    notification_task = asyncio.create_task(notification_service.start())
    cache_cleanup_task = asyncio.create_task(periodic_cache_cleanup(interval=300))

    logger.info("Bot started. Press Ctrl+C to stop.")

    # Обработка сигналов для корректного завершения
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        notification_service.stop()
        notification_task.cancel()
        cache_cleanup_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        # Запуск polling
        polling = GroupPolling(api, group_id=config.vk_group_id)
        async for event in polling.listen():
            await bot.process_event(event)
    except asyncio.CancelledError:
        logger.info("Polling cancelled")
    finally:
        logger.info("Shutting down...")

        notification_task.cancel()
        cache_cleanup_task.cancel()

        try:
            await notification_task
        except asyncio.CancelledError:
            pass

        try:
            await cache_cleanup_task
        except asyncio.CancelledError:
            pass

        await db_pool.close()

        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.exception(f"Fatal error: {e}")
        sys.exit(1)
