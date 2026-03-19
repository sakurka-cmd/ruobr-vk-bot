"""
Обработчики для расписания, ДЗ и оценок.
"""
import asyncio
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from vkbottle.bot import Blueprint, Message

from ..config import config
from ..database import UserConfig
from ..services import (
    Child, Lesson, get_children_async, get_timetable_for_children,
    RuobrError
)
from ..utils.formatters import (
    format_lesson, format_homework, format_mark, format_date,
    format_weekday, truncate_text, extract_homework_files,
    clean_html_text, has_meaningful_text
)
from .balance import require_authentication
from .auth import get_main_keyboard, bp

logger = logging.getLogger(__name__)

# Таймаут для сетевых операций (секунды)
NETWORK_TIMEOUT = 30


async def safe_edit_message(status_msg: Message, text: str) -> bool:
    """
    Безопасное редактирование сообщения с обработкой ошибок.

    Returns:
        True если успешно, False если ошибка.
    """
    try:
        await asyncio.wait_for(
            status_msg.edit(text),
            timeout=NETWORK_TIMEOUT
        )
        return True
    except asyncio.TimeoutError:
        logger.warning(f"Timeout editing message")
        return False
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        return False


# ===== Расписание на сегодня =====

@bp.on.message(text="/ttoday")
@bp.on.message(text="📅 Расписание сегодня")
async def cmd_ttoday(message: Message, user_config: Optional[UserConfig] = None):
    """Показать расписание на сегодня."""
    result = await require_authentication(message, user_config)
    if result is None:
        return

    login, password, children = result

    status_msg = await message.answer("🔄 Загрузка расписания...")

    try:
        today = date.today()
        timetable = await asyncio.wait_for(
            get_timetable_for_children(login, password, children, today, today),
            timeout=NETWORK_TIMEOUT
        )

        lines = [f"📅 Расписание на сегодня ({format_date(str(today))}, {format_weekday(today)})"]
        found = False

        for child in children:
            lessons = timetable.get(child.id, [])
            if not lessons:
                continue

            found = True
            lines.append(f"\n👦 {child.full_name} ({child.group}):")

            for lesson in lessons:
                lines.append(format_lesson(lesson, show_details=True))

        if not found:
            await safe_edit_message(status_msg, "ℹ️ На сегодня расписание не найдено.")
        else:
            text = truncate_text("\n".join(lines))
            await safe_edit_message(status_msg, text)

    except asyncio.TimeoutError:
        logger.error(f"Timeout getting timetable for user {message.peer_id}")
        await safe_edit_message(status_msg, "⏱ Превышено время ожидания. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Error getting timetable for user {message.peer_id}: {e}")
        await safe_edit_message(status_msg, f"❌ Ошибка получения расписания: {e}")


# ===== Расписание на завтра =====

@bp.on.message(text="/ttomorrow")
@bp.on.message(text="📅 Расписание завтра")
async def cmd_ttomorrow(message: Message, user_config: Optional[UserConfig] = None):
    """Показать расписание на завтра."""
    result = await require_authentication(message, user_config)
    if result is None:
        return

    login, password, children = result

    status_msg = await message.answer("🔄 Загрузка расписания...")

    try:
        tomorrow = date.today() + timedelta(days=1)
        timetable = await asyncio.wait_for(
            get_timetable_for_children(login, password, children, tomorrow, tomorrow),
            timeout=NETWORK_TIMEOUT
        )

        lines = [f"📅 Расписание на завтра ({format_date(str(tomorrow))}, {format_weekday(tomorrow)})"]
        found = False

        for child in children:
            lessons = timetable.get(child.id, [])
            if not lessons:
                continue

            found = True
            lines.append(f"\n👦 {child.full_name} ({child.group}):")

            for lesson in lessons:
                lines.append(format_lesson(lesson, show_details=True))

        if not found:
            await safe_edit_message(status_msg, "ℹ️ На завтра расписание не найдено.")
        else:
            text = truncate_text("\n".join(lines))
            await safe_edit_message(status_msg, text)

    except asyncio.TimeoutError:
        logger.error(f"Timeout getting timetable for user {message.peer_id}")
        await safe_edit_message(status_msg, "⏱ Превышено время ожидания. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Error getting timetable for user {message.peer_id}: {e}")
        await safe_edit_message(status_msg, f"❌ Ошибка получения расписания: {e}")


# ===== Домашнее задание на завтра =====

@bp.on.message(text="/hwtomorrow")
@bp.on.message(text="📘 ДЗ на завтра")
async def cmd_hwtomorrow(message: Message, user_config: Optional[UserConfig] = None):
    """Показать ДЗ на завтра."""
    result = await require_authentication(message, user_config)
    if result is None:
        return

    login, password, children = result

    status_msg = await message.answer("🔄 Загрузка домашнего задания...")

    try:
        today = date.today()
        tomorrow = today + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")

        # Запрашиваем расписание на неделю, так как ДЗ может быть задано раньше
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)

        timetable = await asyncio.wait_for(
            get_timetable_for_children(login, password, children, monday, sunday),
            timeout=NETWORK_TIMEOUT
        )

        lines = [f"📘 Домашнее задание на завтра ({format_date(tomorrow_str)})"]
        found = False

        # Собираем все файлы для отправки отдельно
        all_files: List[Tuple[str, str, str]] = []  # (file_type, url, subject)

        for child in children:
            lessons = timetable.get(child.id, [])
            child_header_added = False

            for lesson in lessons:
                # Фильтруем по дедлайну
                relevant_hw = []
                for hw in lesson.homework:
                    if hw.get("deadline") == tomorrow_str:
                        relevant_hw.append(hw)

                if not relevant_hw:
                    continue

                found = True
                if not child_header_added:
                    lines.append(f"\n👦 {child.full_name} ({child.group}):")
                    child_header_added = True

                for hw in relevant_hw:
                    title = hw.get("title", "")
                    lines.append(f"  📖 {lesson.subject}: {title}")

                    # Показываем текст ДЗ если есть полезная информация
                    hw_text = hw.get("text", "")
                    if has_meaningful_text(hw_text):
                        clean_text = clean_html_text(hw_text)
                        # Ограничиваем длину текста
                        if len(clean_text) > 500:
                            clean_text = clean_text[:497] + "..."
                        lines.append(f"     📝 {clean_text}")

                    # Собираем файлы для отправки
                    files = extract_homework_files(hw_text)
                    for file_type, file_url in files:
                        all_files.append((file_type, file_url, lesson.subject))

        if not found:
            await safe_edit_message(status_msg, "ℹ️ На завтра домашнее задание не найдено.")
        else:
            text = truncate_text("\n".join(lines))
            await safe_edit_message(status_msg, text)

            # VK не поддерживает отправку файлов напрямую как Telegram
            # Поэтому отправляем ссылки на файлы
            if all_files:
                file_lines = ["📎 Файлы к ДЗ:"]
                for file_type, file_url, subject in all_files:
                    file_lines.append(f"• {subject}: {file_url}")

                await message.answer("\n".join(file_lines))

    except asyncio.TimeoutError:
        logger.error(f"Timeout getting homework for user {message.peer_id}")
        await safe_edit_message(status_msg, "⏱ Превышено время ожидания. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Error getting homework for user {message.peer_id}: {e}")
        await safe_edit_message(status_msg, f"❌ Ошибка получения ДЗ: {e}")


# ===== Оценки за сегодня =====

@bp.on.message(text="/markstoday")
@bp.on.message(text="⭐ Оценки сегодня")
async def cmd_markstoday(message: Message, user_config: Optional[UserConfig] = None):
    """Показать оценки за сегодня."""
    result = await require_authentication(message, user_config)
    if result is None:
        return

    login, password, children = result

    status_msg = await message.answer("🔄 Загрузка оценок...")

    try:
        today = date.today()
        today_str = today.strftime("%Y-%m-%d")

        timetable = await asyncio.wait_for(
            get_timetable_for_children(login, password, children, today, today),
            timeout=NETWORK_TIMEOUT
        )

        lines = [f"⭐ Оценки за сегодня ({format_date(today_str)})"]
        found = False

        for child in children:
            lessons = timetable.get(child.id, [])
            child_header_added = False

            for lesson in lessons:
                if not lesson.marks:
                    continue

                if not child_header_added:
                    lines.append(f"\n👦 {child.full_name} ({child.group}):")
                    child_header_added = True

                for mark in lesson.marks:
                    found = True
                    mark_str = format_mark(mark, lesson.subject)
                    lines.append(f"  {mark_str}")

        if not found:
            await safe_edit_message(status_msg, "ℹ️ За сегодня оценок не найдено.")
        else:
            text = truncate_text("\n".join(lines))
            await safe_edit_message(status_msg, text)

    except asyncio.TimeoutError:
        logger.error(f"Timeout getting marks for user {message.peer_id}")
        await safe_edit_message(status_msg, "⏱ Превышено время ожидания. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Error getting marks for user {message.peer_id}: {e}")
        await safe_edit_message(status_msg, f"❌ Ошибка получения оценок: {e}")
