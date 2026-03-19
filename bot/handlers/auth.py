"""
Обработчики аутентификации и базовых команд.
"""
import logging
from typing import Optional

from vkbottle import Keyboard, KeyboardButtonColor, Text, Callback, EMPTY_KEYBOARD
from vkbottle.bot import Blueprint, Message
from vkbottle_types.events import MessageEvent

from ..config import config
from ..database import get_user, create_or_update_user, UserConfig
from ..states import LoginStates
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


def get_inline_child_select_keyboard(children, action: str, payload_prefix: str = "info") -> str:
    """Инлайн клавиатура выбора ребенка."""
    kb = Keyboard(inline=True)
    for i, child in enumerate(children):
        if i > 0:
            kb.row()
        kb.add(Callback(f"👤 {child.full_name} ({child.group})", payload={"action": action, "index": i}))
    return kb.get_json()


# ===== Команды =====

@bp.on.message(text="/start")
async def cmd_start(message: Message, user_config: Optional[UserConfig] = None):
    if user_config is None:
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
    # Устанавливаем состояние ожидания логина
    bp.state_dispenser.set(message.peer_id, LoginStates.waiting_for_login)
    await message.answer(
        "🔐 Настройка учётных данных\n\n"
        "Введите логин от cabinet.ruobr.ru:\n\n"
        "❌ Отмена — для выхода",
        keyboard=get_cancel_keyboard()
    )


@bp.on.message(state=LoginStates.waiting_for_login)
async def process_login(message: Message):
    text = message.text.strip() if message.text else ""

    # Проверка отмены
    if text == "❌ Отмена" or text == "/cancel":
        bp.state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=get_main_keyboard())
        return

    if not text:
        await message.answer("❌ Логин не может быть пустым. Попробуйте ещё раз:")
        return

    if len(text) > 100:
        await message.answer("❌ Логин слишком длинный. Попробуйте ещё раз:")
        return

    # Сохраняем логин в состоянии
    bp.state_dispenser.set(message.peer_id, LoginStates.waiting_for_password, {"login": text})
    await message.answer(
        "✅ Логин сохранён.\n\n"
        "Теперь введите пароль от cabinet.ruobr.ru:\n\n"
        "❌ Отмена — для выхода",
        keyboard=get_cancel_keyboard()
    )


@bp.on.message(state=LoginStates.waiting_for_password)
async def process_password(message: Message):
    password = message.text.strip() if message.text else ""

    # Проверка отмены
    if password == "❌ Отмена" or password == "/cancel":
        bp.state_dispenser.delete(message.peer_id)
        await message.answer("❌ Отменено.", keyboard=get_main_keyboard())
        return

    if not password:
        await message.answer("❌ Пароль не может быть пустым. Попробуйте ещё раз:")
        return

    state = bp.state_dispenser.get(message.peer_id)
    login = state.payload.get("login", "") if state and state.payload else ""

    # Удаляем сообщение с паролем (если возможно)
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

        # Сохраняем учётные данные
        await create_or_update_user(message.peer_id, login=login, password=password)

        # Отправляем клавиатуру отдельным сообщением
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

    bp.state_dispenser.delete(message.peer_id)


@bp.on.message(text="/cancel")
@bp.on.message(text="❌ Отмена")
async def cmd_cancel(message: Message):
    state = bp.state_dispenser.get(message.peer_id)
    if state is None:
        await message.answer("Нет активной операции.", keyboard=get_main_keyboard())
        return

    bp.state_dispenser.delete(message.peer_id)
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

async def get_children_or_select(message: Message, user_config: UserConfig, action: str):
    """Получить детей или показать выбор"""
    try:
        children = await get_children_async(user_config.login, user_config.password)
        if not children:
            await message.answer("❌ Дети не найдены.")
            return None

        if len(children) == 1:
            return (children, 0)  # Один ребенок - возвращаем его индекс

        # Несколько детей - показываем выбор
        await message.answer(
            f"👦👧 Выберите ребенка:",
            keyboard=get_inline_child_select_keyboard(children, action)
        )
        return None  # Ждем callback

    except Exception as e:
        logger.error(f"Error getting children: {e}")
        await message.answer(f"❌ Ошибка: {e}")
        return None


async def show_classmates(message: Message, login: str, password: str, child_index: int, child_name: str):
    """Показать одноклассников"""
    status_msg = await message.answer("🔄 Загрузка списка одноклассников...")

    try:
        classmates = await get_classmates_for_child(login, password, child_index)

        if not classmates:
            await status_msg.edit("ℹ️ Одноклассники не найдены.")
            return

        # Получаем информацию о текущем ребенке для добавления в список
        children = await get_children_async(login, password)
        current_child = children[child_index] if children and child_index < len(children) else None

        # Если ребёнка нет в списке одноклассников, добавляем его
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

            # Проверяем, есть ли уже ребенок в списке
            child_in_list = any(c.last_name == child_as_classmate.last_name and
                                c.first_name == child_as_classmate.first_name
                                for c in classmates)
            if not child_in_list:
                classmates.append(child_as_classmate)

        classmates_sorted = sorted(classmates, key=lambda c: c.last_name)

        from datetime import datetime

        # Формируем таблицу с увеличенной шириной для ФИО
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

            # Форматируем имя (40 символов для полного ФИО)
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

        # Фильтруем только учителей с предметами (предметники)
        subject_teachers = [t for t in guide.teachers if t.subject]

        lines = [f"👩‍🏫 Учителя — {child_name}\n"]
        lines.append(f"Школа: {guide.name}")
        if guide.phone:
            lines.append(f"Телефон: {guide.phone}")
        if guide.url:
            lines.append(f"Сайт: {guide.url}")
        lines.append("")

        if subject_teachers:
            # Разбиваем учителей с несколькими предметами на отдельные записи
            teacher_subject_pairs = []
            for t in subject_teachers:
                # Разбиваем строку предметов по запятой
                subjects = [s.strip() for s in t.subject.split(",") if s.strip()]
                for subject in subjects:
                    teacher_subject_pairs.append((subject, t.name))

            # Сортируем по предмету
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
async def btn_classmates(message: Message, user_config: Optional[UserConfig] = None):
    if user_config is None or not user_config.login:
        await message.answer("❌ Сначала настройте логин/пароль через /set_login", keyboard=get_main_keyboard())
        return

    result = await get_children_or_select(message, user_config, "classmates")
    if result:
        children, idx = result
        await show_classmates(message, user_config.login, user_config.password, idx, children[idx].full_name)


@bp.on.message(text="👩‍🏫 Учителя")
async def btn_teachers(message: Message, user_config: Optional[UserConfig] = None):
    if user_config is None or not user_config.login:
        await message.answer("❌ Сначала настройте логин/пароль через /set_login", keyboard=get_main_keyboard())
        return

    result = await get_children_or_select(message, user_config, "teachers")
    if result:
        children, idx = result
        await show_teachers(message, user_config.login, user_config.password, idx, children[idx].full_name)


@bp.on.message(text="🏆 Достижения")
async def btn_achievements(message: Message, user_config: Optional[UserConfig] = None):
    if user_config is None or not user_config.login:
        await message.answer("❌ Сначала настройте логин/пароль через /set_login", keyboard=get_main_keyboard())
        return

    result = await get_children_or_select(message, user_config, "achievements")
    if result:
        children, idx = result
        await show_achievements(message, user_config.login, user_config.password, idx, children[idx].full_name)


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


# Обработчики callback событий (инлайн кнопки)
@bp.on.event_message_event()
async def handle_callback_event(event: MessageEvent):
    """Обработка событий с инлайн кнопок"""
    payload = event.payload
    action = payload.get("action", "")
    index = payload.get("index", 0)

    user_config = await get_user(event.peer_id)

    if user_config is None or not user_config.login:
        await event.show_snackbar("❌ Ошибка авторизации")
        return

    try:
        children = await get_children_async(user_config.login, user_config.password)

        if not children or index >= len(children):
            await event.show_snackbar("❌ Ошибка: ребёнок не найден")
            return

        child = children[index]

        if action == "classmates":
            await event.edit_message("🔄 Загрузка одноклассников...")
            classmates = await get_classmates_for_child(user_config.login, user_config.password, index)

            # Добавляем текущего ребенка
            child_as_classmate = type('Classmate', (), {
                'last_name': child.last_name,
                'first_name': child.first_name,
                'middle_name': child.middle_name,
                'birth_date': child.birth_date,
                'gender': child.gender,
                'full_name': child.full_name,
                'gender_icon': child.gender_icon
            })()

            child_in_list = any(c.last_name == child_as_classmate.last_name and
                                c.first_name == child_as_classmate.first_name
                                for c in classmates)
            if not child_in_list:
                classmates.append(child_as_classmate)

            classmates_sorted = sorted(classmates, key=lambda c: c.last_name)

            from datetime import datetime
            lines = [f"👥 Классный список — {child.full_name} ({len(classmates_sorted)} чел.):\n"]
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
                text = text[:3997] + "..."

            await event.edit_message(text)

        elif action == "teachers":
            await event.edit_message("🔄 Загрузка учителей...")
            guide = await get_guide_for_child(user_config.login, user_config.password, index)

            subject_teachers = [t for t in guide.teachers if t.subject]

            lines = [f"👩‍🏫 Учителя — {child.full_name}\n"]
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

            await event.edit_message("\n".join(lines))

        elif action == "achievements":
            await event.edit_message("🔄 Загрузка достижений...")
            achievements = await get_achievements_for_child(user_config.login, user_config.password, index)

            lines = [f"🏆 Достижения — {child.full_name}\n"]

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

            await event.edit_message("\n".join(lines))

    except Exception as e:
        logger.error(f"Error in callback handler: {e}")
        try:
            await event.show_snackbar(f"❌ Ошибка: {e}")
        except:
            pass


@bp.on.message(text="◀️ Назад")
async def btn_back(message: Message):
    await message.answer("🏠 Главное меню", keyboard=get_main_keyboard())


@bp.on.message(text="👤 Мой профиль")
async def btn_profile(message: Message, user_config: Optional[UserConfig] = None):
    if user_config is None:
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

def get_notification_keyboard(user_config: UserConfig) -> str:
    """Инлайн клавиатура для настройки уведомлений."""
    balance_status = "✅" if user_config.enabled else "❌"
    marks_status = "✅" if user_config.marks_enabled else "❌"
    food_status = "✅" if getattr(user_config, 'food_enabled', True) else "❌"

    return (
        Keyboard(inline=True)
        .add(Callback(f"💰 Баланс: {balance_status}", payload={"action": "toggle_balance"}))
        .row()
        .add(Callback(f"⭐ Оценки: {marks_status}", payload={"action": "toggle_marks"}))
        .row()
        .add(Callback(f"🍽 Питание: {food_status}", payload={"action": "toggle_food"}))
    ).get_json()


@bp.on.message(text="🔔 Уведомления")
async def btn_notifications(message: Message, user_config: Optional[UserConfig] = None):
    if user_config is None:
        user_config = await get_user(message.peer_id)
    if user_config is None:
        user_config = await create_or_update_user(message.peer_id)

    await message.answer(
        "🔔 Настройки уведомлений\n\n"
        "Нажмите для включения/выключения:",
        keyboard=get_notification_keyboard(user_config)
    )


# Обработчики для toggle уведомлений через callback
@bp.on.event_message_event()
async def handle_notification_toggle(event: MessageEvent):
    """Обработка переключения уведомлений"""
    payload = event.payload
    action = payload.get("action", "")

    if action not in ["toggle_balance", "toggle_marks", "toggle_food"]:
        return  # Не наше событие

    user_config = await get_user(event.peer_id)
    if user_config is None:
        await event.show_snackbar("Ошибка!")
        return

    if action == "toggle_balance":
        new_status = not user_config.enabled
        await create_or_update_user(event.peer_id, enabled=new_status)
        await event.show_snackbar(f"{'Включено' if new_status else 'Выключено'}!")
    elif action == "toggle_marks":
        new_status = not user_config.marks_enabled
        await create_or_update_user(event.peer_id, marks_enabled=new_status)
        await event.show_snackbar(f"{'Включено' if new_status else 'Выключено'}!")
    elif action == "toggle_food":
        new_status = not getattr(user_config, 'food_enabled', True)
        await create_or_update_user(event.peer_id, food_enabled=new_status)
        await event.show_snackbar(f"{'Включено' if new_status else 'Выключено'}!")

    updated = await get_user(event.peer_id)
    await event.edit_message(
        "🔔 Настройки уведомлений\n\n"
        "Нажмите для включения/выключения:",
        keyboard=get_notification_keyboard(updated)
    )
