#!/usr/bin/env python3
"""Ruobr VK Bot - Главный файл запуска."""
import asyncio
import logging
import signal
import sys

from vkbottle import Bot

from bot.config import config
from bot.database import db_pool
from bot.handlers import auth, balance, schedule
from bot.services.notifications import NotificationService
from bot.services.cache import periodic_cache_cleanup


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.data_dir / "bot.log", encoding="utf-8")
        ]
    )
    logging.getLogger("vkbottle").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()
    logger.info("Starting Ruobr VK Bot v2.0")

    await db_pool.initialize()
    logger.info("Database initialized")

    bot = Bot(token=config.vk_token)
    bot.labeler.load(auth.bp)
    bot.labeler.load(balance.bp)
    bot.labeler.load(schedule.bp)

    notification_service = NotificationService(bot.api)
    notification_task = asyncio.create_task(notification_service.start())
    cache_task = asyncio.create_task(periodic_cache_cleanup(interval=300))

    logger.info("Bot started. Press Ctrl+C to stop.")

    loop = asyncio.get_event_loop()

    def stop():
        logger.info("Stopping...")
        notification_service.stop()
        notification_task.cancel()
        cache_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop)
        except NotImplementedError:
            pass

    try:
        await bot.run_polling()
    except asyncio.CancelledError:
        pass
    finally:
        notification_task.cancel()
        cache_task.cancel()
        try:
            await notification_task
        except asyncio.CancelledError:
            pass
        try:
            await cache_task
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
