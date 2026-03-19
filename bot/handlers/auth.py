"""
Обработчики аутентификации и базовых команд.
"""
import logging
from typing import Optional

from vkbottle import Keyboard, KeyboardButtonColor, Text
from vkbottle.bot import Blueprint, Message

from ..config import config
from ..database import get_user, create_or_update_user, UserConfig, get_child_threshold, set_child_threshold
from ..states import LoginStates, ThresholdStates
from ..services import get_children_async, AuthenticationError, get_classmates_for_child, get_achievements_for_child, get_guide_for_child

logger = logging.getLogger(__name__)

bp = Blueprint()


# ===== Клавиатуры =====

def get_main_keyboard() -> str:
    """Главная клавиатура."""
    return (
        Keyboard(one_time=False, inline=False)
        .add(Text("📅 Расписание сегодня"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("📅 Расписание завтра"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("📘 ДЗ на завтра"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("⭐ Оценки сегодня"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("💰 Баланс питания"), color=KeyboardButtonColor.POSITIVE)
        .add(Text("🍽 Питание сегодня"), color=KeyboardButtonColor.POSITIVE)
        .row()
        .add(Text("⚙️ Настройки"), color=KeyboardButtonColor.NEGATIVE)
        .add(Text("ℹ️ Информация"), color=KeyboardButtonColor.NEGATIVE)
    ).get_json()


def get_settings_keyboard() -> str:
    """Клавиатура настроек."""
    return (
        Keyboard(one_time=False, inline=False)
        .add(Text("🔑 Изменить логин/пароль"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("💰 Порог баланса"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("🔔 Уведомления"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("👤 Мой профиль"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("◀️ Назад"), color=KeyboardButtonColor.NEGATIVE)
    ).get_json()


def get_info_keyboard() -> str:
    """Клавиатура информации."""
    return (
        Keyboard(one_time=False, inline=False)
        .add(Text("👥 Одноклассники"), color=KeyboardButtonColor.SECONDARY)
        .add(Text("👩‍🏫 Учителя"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("🏆 Достижения"), color=KeyboardButtonColor.POSITIVE)
        .row()
        .add(Text("📋 Справка"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("◀️ Назад"), color=KeyboardButtonColor.NEGATIVE)
    ).get_json()


def get_cancel_keyboard() -> str:
    """Клавиатура отмены."""
    return (
        Keyboard(one_time=True, inline=False)
        .add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)
    ).get_json()


def get_child_select_keyboard(children, action: str) -> str:
    """Клавиатура выбора ребенка (текстовые кнопки)."""
    kb = Keyboard(one_time=False, inline=False)
    for i, child in enumerate(children):
        if i > 0 and i % 2 == 0:
            kb.row()
        kb.add(Text(f"👤 {i+1}. {child.full_name[:20]}"), color=KeyboardButtonColor.SECONDARY)
    kb.row()
    kb.add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)
    return kb.get_json()


def get_notification_keyboard(user_config: UserConfig) -> str:
    """Клавиатура для настройки уведомлений."""
    balance_status = "✅" if user_config.enabled else "❌"
    marks_status = "✅" if user_config.marks_enabled else "❌"
    
    return (
        Keyboard(one_time=False, inline=False)
        .add(Text(f"💰 Баланс: {balance_status}"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text(f"⭐ Оценки: {marks_status}"), color=KeyboardButtonColor.PRIMARY)
        .row()
        .add(Text("◀️ Назад"), color=KeyboardButtonColor.NEGATIVE)
    ).get_json()


# ===== Утилиты для FSM =====

async def get_user_state(peer_id: int) -> Optional[str]:
    """Получить текущее состояние пользователя."""
    state = await bp.state_dispenser.get(peer_id)
    if state:
        return state.state
    return None


async def set_user_state(peer_id: int, state_str: str, payload: dict = None):
    """Установить состояние пользователя."""
    await bp.state_dispenser.set(peer_id, state_str, payload or {})


async def clear_user_state(peer_id: int):
    """Очистить состояние пользователя."""
    await bp.state_dispenser.delete(peer_id)


async def get_state_payload(peer_id: int) -> Optional[dict]:
    """Получить payload текущего состояния."""
    state = await bp.state_dispenser.get(peer_id)
    if state and state.payload:
        return state.payload
    return None


# ===== Обработчики порога баланса =====

async def handle_threshold_child_selection(message: Message, text: str):
    """Обработка выбора ребёнка для настройки порога."""
    payload = await get_state_payload(message.peer_id)
    if not payload:
        await clear_user_state(message.peer_id)
        await message.answer("❌ Ошибка. Начните заново с /set_threshold", keyboard=get_main_keyboard())
        return

    children = payload.get("children", [])

    try:
        idx = int(text)
    except ValueError:
        await message.answer("❌ Введите номер ребёнка (число).")
        return

    if idx < 1 or idx > len(children):
        await message.answer(f"❌ Неверный номер. Введите число от 1 до {len(children)}.")
        return

    child = children[idx - 1]
    current_threshold = await get_child_threshold(message.peer_id, child["id"])

    # Обновляем состояние
    await set_user_state(
        message.peer_id,
        ThresholdStates.waiting_for_threshold_value.value,
        {
            **payload,
            "selected_child_id": child["id"],
            "selected_child_name": child["name"]
        }
    )

    await message.answer(
        f"👶 Выбран: {child['name']} ({child['group']})\n"
        f"Текущий порог: {current_threshold:.0f} ₽\n\n"
        f"Введите новый порог (число, например: 300):",
        keyboard=get_cancel_keyboard()
    )


async def handle_threshold_value_input(message: Message, text: str):
    """Обработка ввода значения порога."""
    payload = await get_state_payload(message.peer_id)
    if not payload:
        await clear_user_state(message.peer_id)
        await message.answer("❌ Ошибка. Начните заново с /set_threshold", keyboard=get_main_keyboard())
        return

    child_id = payload.get("selected_child_id")
    child_name = payload.get("selected_child_name", "Ребёнок")

    if child_id is None:
        await clear_user_state(message.peer_id)
        await message.answer("❌ Ошибка. Начните заново с /set_threshold", keyboard=get_main_keyboard())
        return

    try:
        value = float(text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите число (например: 300).")
        return

    # Валидация диапазона
    if value < 0:
        await message.answer("❌ Порог не может быть отрицательным.")
        return
    if value > 10000:
        await message.answer("❌ Порог слишком большой (максимум 10000 ₽).")
        return

    # Сохраняем
    await set_child_threshold(message.peer_id, child_id, value)

    # Инвалидируем кэш порогов
    from ..services.cache import threshold_cache
    threshold_cache.delete(f"{message.peer_id}:thresholds")

    await clear_user_state(message.peer_id)

    await message.answer(
        f"✅ Порог установлен!\n\n"
        f"{child_name}: {value:.0f} ₽\n\n"
        f"Вы будете получать уведомления, когда баланс упадёт ниже этого значения.",
        keyboard=get_main_keyboard()
    )


# ===== Команды =====

@bp.on.message(text="/start")
async def cmd_start(message: Message):
    user_config = await create_or_update_user(message.peer_id)
    is_auth = user_config.login and user_config.password

    welcome_text = (
        "👋 Добро пожаловать в школьный бот!\n\n"
        "Я помогаю родителям следить за:\n"
        "• 💰 Балансом школьного питания\n"
        "• 📅 Расписанием уроков\n"
        "• 📘 Домашними заданиями\n"
        "• ⭐ Оценками\n\n"
    )

    if not is_auth:
        welcome_text += "⚠️ Требуется настройка!\nИспользуйте /set_login для ввода учётных данных.\n\n"
    else:
        welcome_text += "✅ Учётные данные настроены.\n\n"

    welcome_text += (
        "📖 Команды:\n"
        "/set_login — настроить логин/пароль\n"
        "/balance — баланс питания\n"
        "/ttoday — расписание сегодня\n"
        "/ttomorrow — расписание завтра"
    )

    await message.answer(welcome_text, keyboard=get_main_keyboard())


@bp.on.message(text="/set_login")
async def cmd_set_login(message: Message):
    await set_user_state(message.peer_id, LoginStates.waiting_for_login.value)
    await message.answer(
        "🔐 Настройка учётных данных\n\n"
        "Введите логин от cabinet.ruobr.ru:\n\n"
        "❌ Отмена — для выхода",
        keyboard=get_cancel_keyboard()
    )


@bp.on.message()
async def handle_state_messages(message: Message):
    """Обработчик сообщений с проверкой состояния."""
    current_state = await get_user_state(message.peer_id)
    text = message.text.strip() if message.text else ""

    # Проверка отмены для любого состояния
    if text in ["❌ Отмена", "/cancel", "◀️ Назад"]:
        await clear_user_state(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=get_main_keyboard())
        return

    # Обработка состояния ожидания логина
    if current_state == LoginStates.waiting_for_login.value:
        if not text:
            await message.answer("❌ Логин не может быть пустым. Попробуйте ещё раз:")
            return

        if len(text) > 100:
            await message.answer("❌ Логин слишком длинный. Попробуйте ещё раз:")
            return

        await set_user_state(message.peer_id, LoginStates.waiting_for_password.value, {"login": text})
        await message.answer(
            "✅ Логин сохранён.\n\n"
            "Теперь введите пароль от cabinet.ruobr.ru:\n\n"
            "❌ Отмена — для выхода",
            keyboard=get_cancel_keyboard()
        )
        return

    # Обработка состояния ожидания пароля
    if current_state == LoginStates.waiting_for_password.value:
        password = text

        if not password:
            await message.answer("❌ Пароль не может быть пустым. Попробуйте ещё раз:")
            return

        payload = await get_state_payload(message.peer_id)
        login = payload.get("login", "") if payload else ""

        # Удаляем сообщение с паролем
        try:
            await message.delete()
        except Exception:
            pass

        status_message = await message.answer("🔄 Проверка учётных данных...")

        try:
            children = await get_children_async(login, password)

            if not children:
                await status_message.edit(
                    "⚠️ Учётные данные верны, но дети не найдены.\n"
                    "Данные сохранены. Проверьте аккаунт на cabinet.ruobr.ru"
                )
            else:
                children_list = "\n".join([f"  • {c.full_name} ({c.group})" for c in children])
                await status_message.edit(
                    f"✅ Успешная авторизация!\n\n"
                    f"Найдены дети:\n{children_list}\n\n"
                    f"Теперь доступны все функции бота."
                )

            await create_or_update_user(message.peer_id, login=login, password=password)
            await message.answer("🏠 Главное меню", keyboard=get_main_keyboard())

        except AuthenticationError:
            await status_message.edit(
                "❌ Ошибка авторизации!\n\n"
                "Неверный логин или пароль. Попробуйте снова: /set_login"
            )
        except Exception as e:
            logger.error(f"Error during login for user {message.peer_id}: {e}")
            await status_message.edit(
                "❌ Ошибка соединения!\n\n"
                "Не удалось проверить учётные данные. Попробуйте позже."
            )

        await clear_user_state(message.peer_id)
        return

    # Обработка состояний настройки порога
    from ..states import ThresholdStates
    
    if current_state == ThresholdStates.waiting_for_child_selection.value:
        await handle_threshold_child_selection(message, text)
        return

    if current_state == ThresholdStates.waiting_for_threshold_value.value:
        await handle_threshold_value_input(message, text)
        return


@bp.on.message(text="/cancel")
@bp.on.message(text="❌ Отмена")
async def cmd_cancel(message: Message):
    await clear_user_state(message.peer_id)
    await message.answer("❌ Операция отменена.", keyboard=get_main_keyboard())


@bp.on.message(text="ℹ️ Информация")
async def btn_info(message: Message):
    await message.answer(
        "ℹ️ Информация\n\n"
        "Выберите что хотите узнать:",
        keyboard=get_info_keyboard()
    )


@bp.on.message(text="⚙️ Настройки")
async def btn_settings(message: Message):
    await message.answer("⚙️ Настройки", keyboard=get_settings_keyboard())


@bp.on.message(text="🔑 Изменить логин/пароль")
async def btn_change_login(message: Message):
    await cmd_set_login(message)


# ===== Информация =====

async def show_classmates(message: Message, login: str, password: str, child_index: int, child_name: str):
    """Показать одноклассников"""
    status_msg = await message.answer("🔄 Загрузка списка одноклассников...")

    try:
        classmates = await get_classmates_for_child(login, password, child_index)

        if not classmates:
            await status_msg.edit("ℹ️ Одноклассники не найдены.")
            return

        children = await get_children_async(login, password)
        current_child = children[child_index] if children and child_index < len(children) else None

        if current_child:
            child_as_classmate = type('Classmate', (), {
                'last_name': current_child.last_name,
                'first_name': current_child.first_name,
                'middle_name': current_child.middle_name,
                'birth_date': current_child.birth_date,
                'gender': current_child.gender,
                'full_name': current_child.full_name,
                'gender_icon': current_child.gender_icon
            })()

            child_in_list = any(c.last_name == child_as_classmate.last_name and
                                c.first_name == child_as_classmate.first_name
                                for c in classmates)
            if not child_in_list:
                classmates.append(child_as_classmate)

        classmates_sorted = sorted(classmates, key=lambda c: c.last_name)

        from datetime import datetime

        lines = [f"👥 Классный список — {child_name} ({len(classmates_sorted)} чел.):\n"]
        lines.append("№   Фамилия Имя Отчество                    | Д.р.      | Возр")
        lines.append("─" * 62)

        for i, c in enumerate(classmates_sorted, 1):
            if c.birth_date:
                try:
                    bd = datetime.strptime(c.birth_date, "%Y-%m-%d")
                    bd_str = bd.strftime("%d.%m.%Y")
                    age = datetime.now().year - bd.year
                    if (datetime.now().month, datetime.now().day) < (bd.month, bd.day):
                        age -= 1
                except:
                    bd_str = c.birth_date
                    age = "?"
            else:
                bd_str = "—"
                age = "—"

            name_display = c.full_name[:40].ljust(40)
            icon = c.gender_icon

            lines.append(f"{i:2}. {name_display} {icon} | {bd_str:10} | {age}")

        lines.append("─" * 62)

        text = "\n".join(lines)
        if len(text) > 4000:
            await status_msg.edit(text[:4000])
            remaining = text[4000:]
            while remaining:
                await message.answer(remaining[:4000])
                remaining = remaining[4000:]
        else:
            await status_msg.edit(text)

    except Exception as e:
        logger.error(f"Error getting classmates: {e}")
        await status_msg.edit(f"❌ Ошибка: {e}")


async def show_teachers(message: Message, login: str, password: str, child_index: int, child_name: str):
    """Показать учителей"""
    status_msg = await message.answer("🔄 Загрузка списка учителей...")

    try:
        guide = await get_guide_for_child(login, password, child_index)

        if not guide.teachers:
            await status_msg.edit("ℹ️ Учителя не найдены.")
            return

        subject_teachers = [t for t in guide.teachers if t.subject]

        lines = [f"👩‍🏫 Учителя — {child_name}\n"]
        lines.append(f"Школа: {guide.name}")
        if guide.phone:
            lines.append(f"Телефон: {guide.phone}")
        if guide.url:
            lines.append(f"Сайт: {guide.url}")
        lines.append("")

        if subject_teachers:
            teacher_subject_pairs = []
            for t in subject_teachers:
                subjects = [s.strip() for s in t.subject.split(",") if s.strip()]
                for subject in subjects:
                    teacher_subject_pairs.append((subject, t.name))

            teacher_subject_pairs.sort(key=lambda x: x[0])

            lines.append("Предмет                         | Учитель")
            lines.append("─" * 55)
            for subject, name in teacher_subject_pairs:
                subject_display = subject[:30].ljust(30)
                lines.append(f"{subject_display} | {name}")
            lines.append("─" * 55)
        else:
            lines.append("Предметники не найдены.")

        await status_msg.edit("\n".join(lines))

    except Exception as e:
        logger.error(f"Error getting teachers: {e}")
        await status_msg.edit(f"❌ Ошибка: {e}")


async def show_achievements(message: Message, login: str, password: str, child_index: int, child_name: str):
    """Показать достижения"""
    status_msg = await message.answer("🔄 Загрузка достижений...")

    try:
        achievements = await get_achievements_for_child(login, password, child_index)

        lines = [f"🏆 Достижения — {child_name}\n"]

        if achievements.directions:
            total = sum(d.count for d in achievements.directions)
            lines.append(f"Всего: {total}\n")

            for d in achievements.directions:
                bar = "█" * (d.percent // 10) + "░" * (10 - d.percent // 10)
                lines.append(f"📍 {d.direction}")
                lines.append(f"   {bar} {d.count} ({d.percent}%)")
        else:
            lines.append("Достижений пока нет.")

        if achievements.projects:
            lines.append(f"\n📝 Проекты: {len(achievements.projects)}")

        if achievements.gto_id:
            lines.append(f"\n🏃 ГТО ID: {achievements.gto_id}")

        await status_msg.edit("\n".join(lines))

    except Exception as e:
        logger.error(f"Error getting achievements: {e}")
        await status_msg.edit(f"❌ Ошибка: {e}")


# Обработчики кнопок информации
@bp.on.message(text="👥 Одноклассники")
async def btn_classmates(message: Message):
    user_config = await get_user(message.peer_id)
    if user_config is None or not user_config.login:
        await message.answer("❌ Сначала настройте логин/пароль через /set_login", keyboard=get_main_keyboard())
        return

    try:
        children = await get_children_async(user_config.login, user_config.password)
        if not children:
            await message.answer("❌ Дети не найдены.")
            return

        if len(children) == 1:
            await show_classmates(message, user_config.login, user_config.password, 0, children[0].full_name)
        else:
            await set_user_state(message.peer_id, "select_child:classmates", 
                                 {"children": [{"id": c.id, "name": c.full_name, "group": c.group} for c in children]})
            await message.answer(
                f"👦👧 Выберите ребенка (введите номер):",
                keyboard=get_child_select_keyboard(children, "classmates")
            )
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@bp.on.message(text="👩‍🏫 Учителя")
async def btn_teachers(message: Message):
    user_config = await get_user(message.peer_id)
    if user_config is None or not user_config.login:
        await message.answer("❌ Сначала настройте логин/пароль через /set_login", keyboard=get_main_keyboard())
        return

    try:
        children = await get_children_async(user_config.login, user_config.password)
        if not children:
            await message.answer("❌ Дети не найдены.")
            return

        if len(children) == 1:
            await show_teachers(message, user_config.login, user_config.password, 0, children[0].full_name)
        else:
            await set_user_state(message.peer_id, "select_child:teachers",
                                 {"children": [{"id": c.id, "name": c.full_name, "group": c.group} for c in children]})
            await message.answer(
                f"👦👧 Выберите ребенка (введите номер):",
                keyboard=get_child_select_keyboard(children, "teachers")
            )
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@bp.on.message(text="🏆 Достижения")
async def btn_achievements(message: Message):
    user_config = await get_user(message.peer_id)
    if user_config is None or not user_config.login:
        await message.answer("❌ Сначала настройте логин/пароль через /set_login", keyboard=get_main_keyboard())
        return

    try:
        children = await get_children_async(user_config.login, user_config.password)
        if not children:
            await message.answer("❌ Дети не найдены.")
            return

        if len(children) == 1:
            await show_achievements(message, user_config.login, user_config.password, 0, children[0].full_name)
        else:
            await set_user_state(message.peer_id, "select_child:achievements",
                                 {"children": [{"id": c.id, "name": c.full_name, "group": c.group} for c in children]})
            await message.answer(
                f"👦👧 Выберите ребенка (введите номер):",
                keyboard=get_child_select_keyboard(children, "achievements")
            )
    except Exception as e:
        logger.error(f"Error: {e}")
        await message.answer(f"❌ Ошибка: {e}")


# Обработка выбора ребенка по номеру
async def handle_child_selection(message: Message, text: str, action: str):
    """Обработка выбора ребенка по номеру"""
    payload = await get_state_payload(message.peer_id)
    if not payload:
        await message.answer("❌ Ошибка. Начните заново.", keyboard=get_main_keyboard())
        return

    children = payload.get("children", [])
    
    # Парсим номер из текста вида "👤 1. Иван Иванов"
    try:
        # Извлекаем число из начала строки
        import re
        match = re.match(r'👤\s*(\d+)', text)
        if match:
            idx = int(match.group(1))
        else:
            idx = int(text)
    except (ValueError, AttributeError):
        await message.answer("❌ Введите номер ребёнка (число).")
        return

    if idx < 1 or idx > len(children):
        await message.answer(f"❌ Неверный номер. Введите число от 1 до {len(children)}.")
        return

    child = children[idx - 1]
    await clear_user_state(message.peer_id)
    
    user_config = await get_user(message.peer_id)
    
    if action == "classmates":
        await show_classmates(message, user_config.login, user_config.password, idx - 1, child["name"])
    elif action == "teachers":
        await show_teachers(message, user_config.login, user_config.password, idx - 1, child["name"])
    elif action == "achievements":
        await show_achievements(message, user_config.login, user_config.password, idx - 1, child["name"])


@bp.on.message(text="📋 Справка")
async def btn_help(message: Message):
    """Справка о боте и его командах"""
    help_text = (
        "📋 Справка по боту\n\n"
        "Школьный бот — помогает родителям следить за учёбой детей.\n\n"

        "📅 Расписание:\n"
        "• «Расписание сегодня» — уроки на сегодня\n"
        "• «Расписание завтра» — уроки на завтра\n\n"

        "📘 Домашние задания:\n"
        "• «ДЗ на завтра» — задания на завтрашний день\n\n"

        "⭐ Оценки:\n"
        "• «Оценки сегодня» — оценки за сегодняшний день\n\n"

        "🍽 Питание:\n"
        "• «Баланс питания» — текущий баланс счёта\n"
        "• «Питание сегодня» — что ребёнок ел сегодня\n\n"

        "ℹ️ Информация:\n"
        "• «Одноклассники» — список класса с датами рождения\n"
        "• «Учителя» — предметники и контакты школы\n"
        "• «Достижения» — достижения и проекты ученика\n\n"

        "⚙️ Настройки:\n"
        "• «Изменить логин/пароль» — обновить данные\n"
        "• «Порог баланса» — настроить уведомления о балансе\n"
        "• «Уведомления» — включить/выключить оповещения\n"
        "• «Мой профиль» — информация об аккаунте\n\n"

        "📝 Команды:\n"
        "/start — главное меню\n"
        "/set_login — настроить учётные данные\n"
        "/balance — баланс питания\n"
        "/ttoday — расписание сегодня\n"
        "/ttomorrow — расписание завтра\n"
        "/enable — включить уведомления\n"
        "/disable — выключить уведомления\n\n"

        "💡 Подсказка: Бот автоматически уведомляет о:\n"
        "• Низком балансе питания\n"
        "• Новых оценках\n\n"

        "🔗 Полезные ссылки:\n"
        "• cabinet.ruobr.ru — электронный дневник"
    )
    await message.answer(help_text)


@bp.on.message(text="◀️ Назад")
async def btn_back(message: Message):
    await clear_user_state(message.peer_id)
    await message.answer("🏠 Главное меню", keyboard=get_main_keyboard())


@bp.on.message(text="👤 Мой профиль")
async def btn_profile(message: Message):
    user_config = await get_user(message.peer_id)

    if user_config is None:
        await message.answer("Профиль не найден. Используйте /start", keyboard=get_main_keyboard())
        return

    status = "✅ Настроен" if user_config.login and user_config.password else "❌ Не настроен"
    notif_status = "🔔 Включены" if user_config.enabled else "🔕 Выключены"
    marks_status = "🔔 Включены" if user_config.marks_enabled else "🔕 Выключены"

    await message.answer(
        f"👤 Ваш профиль\n\n"
        f"Статус: {status}\n"
        f"Логин: {user_config.login or 'не указан'}\n\n"
        f"Уведомления о балансе: {notif_status}\n"
        f"Уведомления об оценках: {marks_status}",
        keyboard=get_settings_keyboard()
    )


@bp.on.message(text="/enable")
async def cmd_enable(message: Message):
    await create_or_update_user(message.peer_id, enabled=True, marks_enabled=True)
    await message.answer("🔔 Уведомления включены!")


@bp.on.message(text="/disable")
async def cmd_disable(message: Message):
    await create_or_update_user(message.peer_id, enabled=False, marks_enabled=False)
    await message.answer("🔕 Уведомления отключены.")


# ===== Уведомления =====

@bp.on.message(text="🔔 Уведомления")
async def btn_notifications(message: Message):
    user_config = await get_user(message.peer_id)
    if user_config is None:
        user_config = await create_or_update_user(message.peer_id)

    await message.answer(
        "🔔 Настройки уведомлений\n\n"
        "Нажмите для включения/выключения:",
        keyboard=get_notification_keyboard(user_config)
    )


@bp.on.message(text_startswith="💰 Баланс:")
async def toggle_balance(message: Message):
    user_config = await get_user(message.peer_id)
    if user_config is None:
        user_config = await create_or_update_user(message.peer_id)
    
    new_status = not user_config.enabled
    await create_or_update_user(message.peer_id, enabled=new_status)
    
    await message.answer(
        f"🔔 Настройки уведомлений\n\n"
        f"Баланс: {'✅ включено' if new_status else '❌ выключено'}\n\n"
        "Нажмите для включения/выключения:",
        keyboard=get_notification_keyboard(await get_user(message.peer_id))
    )


@bp.on.message(text_startswith="⭐ Оценки:")
async def toggle_marks(message: Message):
    user_config = await get_user(message.peer_id)
    if user_config is None:
        user_config = await create_or_update_user(message.peer_id)
    
    new_status = not user_config.marks_enabled
    await create_or_update_user(message.peer_id, marks_enabled=new_status)
    
    await message.answer(
        f"🔔 Настройки уведомлений\n\n"
        f"Оценки: {'✅ включено' if new_status else '❌ выключено'}\n\n"
        "Нажмите для включения/выключения:",
        keyboard=get_notification_keyboard(await get_user(message.peer_id))
    )


# Обработка выбора ребенка по текстовой кнопке
@bp.on.message(text_startswith="👤")
async def handle_child_button(message: Message):
    """Обработка нажатия кнопки выбора ребенка"""
    current_state = await get_user_state(message.peer_id)
    text = message.text.strip()
    
    if current_state and current_state.startswith("select_child:"):
        action = current_state.split(":")[1]
        await handle_child_selection(message, text, action)
