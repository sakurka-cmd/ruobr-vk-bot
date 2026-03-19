"""
Обработчики для баланса питания и информации о питании.
"""
import logging
from datetime import date
from typing import Optional

from vkbottle import Keyboard, KeyboardButtonColor, Text
from vkbottle.bot import Blueprint, Message

from ..config import config
from ..database import (
    get_user, get_child_threshold, set_child_threshold,
    get_all_thresholds_for_peer, UserConfig
)
from ..states import ThresholdStates
from ..services import (
    Child, FoodInfo, get_children_async, get_food_for_children,
    get_timetable_for_children, RuobrError, invalidate_user_cache
)
from ..utils.formatters import (
    format_balance, format_food_visit, format_date, truncate_text
)
from .auth import get_main_keyboard, get_settings_keyboard, bp, set_user_state

logger = logging.getLogger(__name__)


async def require_authentication(
    message: Message,
    user_config: Optional[UserConfig]
) -> Optional[tuple]:
    """
    Проверка аутентификации пользователя.

    Returns:
        Кортеж (login, password, children) или None если не аутентифицирован.
    """
    if user_config is None:
        user_config = await get_user(message.peer_id)

    if not user_config or not user_config.login or not user_config.password:
        await message.answer(
            "❌ Сначала настройте учётные данные командой /set_login",
            keyboard=get_main_keyboard()
        )
        return None

    try:
        children = await get_children_async(user_config.login, user_config.password)
    except RuobrError as e:
        logger.error(f"Ruobr API error for user {message.peer_id}: {e}")
        await message.answer(f"❌ Ошибка доступа к Ruobr: {e}", keyboard=get_main_keyboard())
        return None

    if not children:
        await message.answer("❌ Дети не найдены в аккаунте.", keyboard=get_main_keyboard())
        return None

    return user_config.login, user_config.password, children


# ===== Баланс питания =====

@bp.on.message(text="/balance")
@bp.on.message(text="💰 Баланс питания")
async def cmd_balance(message: Message):
    """Показать баланс питания всех детей."""
    result = await require_authentication(message, None)
    if result is None:
        return

    login, password, children = result

    status_msg = await message.answer("🔄 Загрузка информации о балансе...")

    try:
        food_info = await get_food_for_children(login, password, children)
        thresholds = await get_all_thresholds_for_peer(message.peer_id)

        lines = ["💰 Баланс питания\n"]

        for idx, child in enumerate(children, 1):
            info = food_info.get(child.id)
            threshold = thresholds.get(child.id, config.default_balance_threshold)

            if info and info.has_food:
                balance_str = format_balance(child, info.balance, threshold)
                lines.append(f"{idx}. {balance_str}")
            else:
                lines.append(
                    f"{idx}. {child.full_name} ({child.group}): "
                    f"питание недоступно (порог {threshold:.0f} ₽)"
                )

        lines.append(
            "\n💡 Настройте порог через /set_threshold для уведомлений"
        )

        await status_msg.edit("\n".join(lines))

    except Exception as e:
        logger.error(f"Error getting balance for user {message.peer_id}: {e}")
        await status_msg.edit(
            f"❌ Ошибка получения баланса: {e}"
        )


# ===== Питание сегодня =====

@bp.on.message(text="/foodtoday")
@bp.on.message(text="🍽 Питание сегодня")
async def cmd_foodtoday(message: Message):
    """Показать информацию о питании за сегодня."""
    result = await require_authentication(message, None)
    if result is None:
        return

    login, password, children = result

    status_msg = await message.answer("🔄 Загрузка информации о питании...")

    try:
        today = date.today()
        today_str = today.strftime("%Y-%m-%d")

        food_info = await get_food_for_children(login, password, children)

        lines = [f"🍽 Питание сегодня ({format_date(today_str)})"]
        found = False

        for child in children:
            info = food_info.get(child.id)
            if not info or not info.visits:
                continue

            for visit in info.visits:
                if visit.get("date") != today_str:
                    continue

                if not visit.get("ordered") and visit.get("state") != 30:
                    continue

                found = True
                visit_text = format_food_visit(visit, child.full_name)
                lines.append(visit_text)

        if not found:
            await status_msg.edit(
                f"ℹ️ На сегодня ({format_date(today_str)}) "
                f"подтверждённого питания не найдено."
            )
        else:
            text = truncate_text("\n".join(lines))
            await status_msg.edit(text)

    except Exception as e:
        logger.error(f"Error getting food today for user {message.peer_id}: {e}")
        await status_msg.edit(
            f"❌ Ошибка получения данных о питании: {e}"
        )


# ===== Настройка порога баланса =====

@bp.on.message(text="/set_threshold")
@bp.on.message(text="💰 Порог баланса")
async def cmd_set_threshold(message: Message):
    """Начало настройки порога баланса."""
    result = await require_authentication(message, None)
    if result is None:
        return

    login, password, children = result
    thresholds = await get_all_thresholds_for_peer(message.peer_id)

    lines = ["⚙️ Настройка порога баланса\n"]
    lines.append("Выберите ребёнка для изменения порога:\n")

    for idx, child in enumerate(children, 1):
        threshold = thresholds.get(child.id, config.default_balance_threshold)
        lines.append(
            f"{idx}. {child.full_name} ({child.group}) — "
            f"порог {threshold:.0f} ₽"
        )

    lines.append("\n📝 Ответьте номером ребёнка.")

    # Сохраняем данные детей в состоянии
    await set_user_state(
        message.peer_id,
        ThresholdStates.waiting_for_child_selection,
        {"children": [{"id": c.id, "name": c.full_name, "group": c.group} for c in children]}
    )

    await message.answer("\n".join(lines))
