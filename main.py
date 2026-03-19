#!/usr/bin/env python3
"""Ruobr VK Bot - Главный файл запуска."""
import asyncio
import logging
import signal
import sys
from datetime import date, timedelta

from vkbottle import Bot, Keyboard, KeyboardButtonColor, Text
from vkbottle.bot import Message

from bot.config import config
from bot.database import db_pool, get_user, create_or_update_user, get_child_threshold, set_child_threshold, get_all_thresholds_for_peer
from bot.services import get_children_async, AuthenticationError, get_classmates_for_child, get_achievements_for_child, get_guide_for_child, get_food_for_children, get_timetable_for_children, RuobrError
from bot.services.notifications import NotificationService
from bot.services.cache import periodic_cache_cleanup, threshold_cache
from bot.utils.formatters import format_balance, format_food_visit, format_date, format_lesson, format_mark, format_weekday, truncate_text, clean_html_text, has_meaningful_text


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


# ===== Клавиатуры =====

def get_main_keyboard() -> str:
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
    return (
        Keyboard(one_time=True, inline=False)
        .add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)
    ).get_json()


def get_child_select_keyboard(children) -> str:
    kb = Keyboard(one_time=False, inline=False)
    for i, child in enumerate(children):
        if i > 0 and i % 2 == 0:
            kb.row()
        kb.add(Text(f"👤 {i+1}. {child.full_name[:20]}"), color=KeyboardButtonColor.SECONDARY)
    kb.row()
    kb.add(Text("❌ Отмена"), color=KeyboardButtonColor.NEGATIVE)
    return kb.get_json()


def get_notification_keyboard(user_config) -> str:
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

async def get_user_state(dispenser, peer_id: int) -> str:
    state = await dispenser.get(peer_id)
    return state.state if state else None


async def set_user_state(dispenser, peer_id: int, state_str: str, payload: dict = None):
    await dispenser.set(peer_id, state_str, payload or {})


async def clear_user_state(dispenser, peer_id: int):
    await dispenser.delete(peer_id)


async def get_state_payload(dispenser, peer_id: int):
    state = await dispenser.get(peer_id)
    return state.payload if state and state.payload else None


# ===== Вспомогательные функции =====

async def require_authentication(message: Message, user_config):
    if user_config is None:
        user_config = await get_user(message.peer_id)
    if not user_config or not user_config.login or not user_config.password:
        await message.answer("❌ Сначала настройте учётные данные командой /set_login", keyboard=get_main_keyboard())
        return None
    try:
        children = await get_children_async(user_config.login, user_config.password)
    except RuobrError as e:
        await message.answer(f"❌ Ошибка доступа к Ruobr: {e}", keyboard=get_main_keyboard())
        return None
    if not children:
        await message.answer("❌ Дети не найдены в аккаунте.", keyboard=get_main_keyboard())
        return None
    return user_config.login, user_config.password, children


async def show_classmates(message: Message, login: str, password: str, child_index: int, child_name: str):
    status_msg = await message.answer("🔄 Загрузка...")
    try:
        classmates = await get_classmates_for_child(login, password, child_index)
        if not classmates:
            await status_msg.edit("ℹ️ Одноклассники не найдены.")
            return
        children = await get_children_async(login, password)
        current_child = children[child_index] if children and child_index < len(children) else None
        if current_child:
            child_as_classmate = type('Classmate', (), {
                'last_name': current_child.last_name, 'first_name': current_child.first_name,
                'middle_name': current_child.middle_name, 'birth_date': current_child.birth_date,
                'gender': current_child.gender, 'full_name': current_child.full_name,
                'gender_icon': current_child.gender_icon
            })()
            if not any(c.last_name == child_as_classmate.last_name and c.first_name == child_as_classmate.first_name for c in classmates):
                classmates.append(child_as_classmate)
        from datetime import datetime
        classmates_sorted = sorted(classmates, key=lambda c: c.last_name)
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
                    bd_str, age = c.birth_date, "?"
            else:
                bd_str, age = "—", "—"
            lines.append(f"{i:2}. {c.full_name[:40].ljust(40)} {c.gender_icon} | {bd_str:10} | {age}")
        lines.append("─" * 62)
        text = "\n".join(lines)
        await status_msg.edit(text[:4000] if len(text) > 4000 else text)
    except Exception as e:
        await status_msg.edit(f"❌ Ошибка: {e}")


async def show_teachers(message: Message, login: str, password: str, child_index: int, child_name: str):
    status_msg = await message.answer("🔄 Загрузка...")
    try:
        guide = await get_guide_for_child(login, password, child_index)
        if not guide.teachers:
            await status_msg.edit("ℹ️ Учителя не найдены.")
            return
        subject_teachers = [t for t in guide.teachers if t.subject]
        lines = [f"👩‍🏫 Учителя — {child_name}\n", f"Школа: {guide.name}"]
        if guide.phone:
            lines.append(f"Телефон: {guide.phone}")
        if guide.url:
            lines.append(f"Сайт: {guide.url}")
        lines.append("")
        if subject_teachers:
            pairs = []
            for t in subject_teachers:
                for subject in [s.strip() for s in t.subject.split(",") if s.strip()]:
                    pairs.append((subject, t.name))
            pairs.sort(key=lambda x: x[0])
            lines.append("Предмет                         | Учитель")
            lines.append("─" * 55)
            for subject, name in pairs:
                lines.append(f"{subject[:30].ljust(30)} | {name}")
            lines.append("─" * 55)
        else:
            lines.append("Предметники не найдены.")
        await status_msg.edit("\n".join(lines))
    except Exception as e:
        await status_msg.edit(f"❌ Ошибка: {e}")


async def show_achievements(message: Message, login: str, password: str, child_index: int, child_name: str):
    status_msg = await message.answer("🔄 Загрузка...")
    try:
        achievements = await get_achievements_for_child(login, password, child_index)
        lines = [f"🏆 Достижения — {child_name}\n"]
        if achievements.directions:
            total = sum(d.count for d in achievements.directions)
            lines.append(f"Всего: {total}\n")
            for d in achievements.directions:
                bar = "█" * (d.percent // 10) + "░" * (10 - d.percent // 10)
                lines.append(f"📍 {d.direction}\n   {bar} {d.count} ({d.percent}%)")
        else:
            lines.append("Достижений пока нет.")
        if achievements.projects:
            lines.append(f"\n📝 Проекты: {len(achievements.projects)}")
        if achievements.gto_id:
            lines.append(f"\n🏃 ГТО ID: {achievements.gto_id}")
        await status_msg.edit("\n".join(lines))
    except Exception as e:
        await status_msg.edit(f"❌ Ошибка: {e}")


async def handle_child_selection(message: Message, text: str, action: str, bot):
    payload = await get_state_payload(bot.state_dispenser, message.peer_id)
    if not payload:
        await message.answer("❌ Ошибка. Начните заново.", keyboard=get_main_keyboard())
        return
    children = payload.get("children", [])
    import re
    match = re.match(r'👤\s*(\d+)', text)
    try:
        idx = int(match.group(1)) if match else int(text)
    except:
        await message.answer("❌ Введите номер ребёнка.")
        return
    if idx < 1 or idx > len(children):
        await message.answer(f"❌ Неверный номер. Введите от 1 до {len(children)}.")
        return
    child = children[idx - 1]
    await clear_user_state(bot.state_dispenser, message.peer_id)
    user_config = await get_user(message.peer_id)
    if action == "classmates":
        await show_classmates(message, user_config.login, user_config.password, idx - 1, child["name"])
    elif action == "teachers":
        await show_teachers(message, user_config.login, user_config.password, idx - 1, child["name"])
    elif action == "achievements":
        await show_achievements(message, user_config.login, user_config.password, idx - 1, child["name"])


def main() -> None:
    setup_logging()
    logger.info("Starting Ruobr VK Bot v2.0")

    bot = Bot(token=config.vk_token)
    labeler = bot.labeler

    # ===== Команда /start =====
    @labeler.message(text="/start")
    async def cmd_start(message: Message):
        user_config = await create_or_update_user(message.peer_id)
        is_auth = user_config.login and user_config.password
        text = ("👋 Добро пожаловать в школьный бот!\n\n"
                "Я помогаю родителям следить за:\n• 💰 Балансом школьного питания\n• 📅 Расписанием уроков\n"
                "• 📘 Домашними заданиями\n• ⭐ Оценками\n\n")
        if not is_auth:
            text += "⚠️ Требуется настройка!\nИспользуйте /set_login для ввода учётных данных.\n\n"
        else:
            text += "✅ Учётные данные настроены.\n\n"
        text += "📖 Команды:\n/set_login — настроить логин/пароль\n/balance — баланс питания\n/ttoday — расписание сегодня\n/ttomorrow — расписание завтра"
        await message.answer(text, keyboard=get_main_keyboard())

    # ===== Команда /set_login =====
    @labeler.message(text="/set_login")
    async def cmd_set_login(message: Message):
        await set_user_state(bot.state_dispenser, message.peer_id, "login:waiting_for_login")
        await message.answer("🔐 Настройка учётных данных\n\nВведите логин от cabinet.ruobr.ru:\n\n❌ Отмена — для выхода", keyboard=get_cancel_keyboard())

    # ===== Обработчик всех сообщений (для FSM) =====
    @labeler.message()
    async def handle_all_messages(message: Message):
        current_state = await get_user_state(bot.state_dispenser, message.peer_id)
        text = message.text.strip() if message.text else ""

        # Проверка отмены
        if text in ["❌ Отмена", "/cancel", "◀️ Назад"]:
            await clear_user_state(bot.state_dispenser, message.peer_id)
            await message.answer("❌ Отменено.", keyboard=get_main_keyboard())
            return

        # Ожидание логина
        if current_state == "login:waiting_for_login":
            if not text:
                await message.answer("❌ Логин не может быть пустым.")
                return
            if len(text) > 100:
                await message.answer("❌ Логин слишком длинный.")
                return
            await set_user_state(bot.state_dispenser, message.peer_id, "login:waiting_for_password", {"login": text})
            await message.answer("✅ Логин сохранён.\n\nВведите пароль от cabinet.ruobr.ru:\n\n❌ Отмена — для выхода", keyboard=get_cancel_keyboard())
            return

        # Ожидание пароля
        if current_state == "login:waiting_for_password":
            if not text:
                await message.answer("❌ Пароль не может быть пустым.")
                return
            payload = await get_state_payload(bot.state_dispenser, message.peer_id)
            login = payload.get("login", "") if payload else ""
            try:
                await message.delete()
            except:
                pass
            status_msg = await message.answer("🔄 Проверка учётных данных...")
            try:
                children = await get_children_async(login, text)
                if not children:
                    await status_msg.edit("⚠️ Учётные данные верны, но дети не найдены.")
                else:
                    children_list = "\n".join([f"  • {c.full_name} ({c.group})" for c in children])
                    await status_msg.edit(f"✅ Успешная авторизация!\n\nНайдены дети:\n{children_list}\n\nТеперь доступны все функции бота.")
                await create_or_update_user(message.peer_id, login=login, password=text)
                await message.answer("🏠 Главное меню", keyboard=get_main_keyboard())
            except AuthenticationError:
                await status_msg.edit("❌ Ошибка авторизации!\n\nНеверный логин или пароль. Попробуйте снова: /set_login")
            except Exception as e:
                logger.error(f"Login error: {e}")
                await status_msg.edit("❌ Ошибка соединения!")
            await clear_user_state(bot.state_dispenser, message.peer_id)
            return

        # Выбор порога - ребенок
        if current_state == "threshold:waiting_for_child_selection":
            payload = await get_state_payload(bot.state_dispenser, message.peer_id)
            if not payload:
                await clear_user_state(bot.state_dispenser, message.peer_id)
                await message.answer("❌ Ошибка. Начните заново с /set_threshold", keyboard=get_main_keyboard())
                return
            children = payload.get("children", [])
            try:
                idx = int(text)
            except ValueError:
                await message.answer("❌ Введите номер ребёнка (число).")
                return
            if idx < 1 or idx > len(children):
                await message.answer(f"❌ Неверный номер. Введите от 1 до {len(children)}.")
                return
            child = children[idx - 1]
            current_threshold = await get_child_threshold(message.peer_id, child["id"])
            await set_user_state(bot.state_dispenser, message.peer_id, "threshold:waiting_for_value", {**payload, "selected_child_id": child["id"], "selected_child_name": child["name"]})
            await message.answer(f"👶 Выбран: {child['name']} ({child['group']})\nТекущий порог: {current_threshold:.0f} ₽\n\nВведите новый порог (число):", keyboard=get_cancel_keyboard())
            return

        # Ввод порога
        if current_state == "threshold:waiting_for_value":
            payload = await get_state_payload(bot.state_dispenser, message.peer_id)
            if not payload:
                await clear_user_state(bot.state_dispenser, message.peer_id)
                await message.answer("❌ Ошибка. Начните заново.", keyboard=get_main_keyboard())
                return
            child_id = payload.get("selected_child_id")
            child_name = payload.get("selected_child_name", "Ребёнок")
            try:
                value = float(text.replace(",", "."))
            except ValueError:
                await message.answer("❌ Введите число.")
                return
            if value < 0 or value > 10000:
                await message.answer("❌ Порог должен быть от 0 до 10000 ₽.")
                return
            await set_child_threshold(message.peer_id, child_id, value)
            threshold_cache.delete(f"{message.peer_id}:thresholds")
            await clear_user_state(bot.state_dispenser, message.peer_id)
            await message.answer(f"✅ Порог установлен!\n\n{child_name}: {value:.0f} ₽", keyboard=get_main_keyboard())
            return

        # Выбор ребенка для информации
        if current_state and current_state.startswith("select_child:"):
            action = current_state.split(":")[1]
            await handle_child_selection(message, text, action, bot)
            return

        # Кнопки уведомлений
        if text.startswith("💰 Баланс:"):
            user_config = await get_user(message.peer_id) or await create_or_update_user(message.peer_id)
            new_status = not user_config.enabled
            await create_or_update_user(message.peer_id, enabled=new_status)
            await message.answer(f"🔔 Баланс: {'✅ включено' if new_status else '❌ выключено'}", keyboard=get_notification_keyboard(await get_user(message.peer_id)))
            return

        if text.startswith("⭐ Оценки:"):
            user_config = await get_user(message.peer_id) or await create_or_update_user(message.peer_id)
            new_status = not user_config.marks_enabled
            await create_or_update_user(message.peer_id, marks_enabled=new_status)
            await message.answer(f"🔔 Оценки: {'✅ включено' if new_status else '❌ выключено'}", keyboard=get_notification_keyboard(await get_user(message.peer_id)))
            return

    # ===== Кнопки =====
    @labeler.message(text="/cancel")
    @labeler.message(text="❌ Отмена")
    async def cmd_cancel(message: Message):
        await clear_user_state(bot.state_dispenser, message.peer_id)
        await message.answer("❌ Операция отменена.", keyboard=get_main_keyboard())

    @labeler.message(text="ℹ️ Информация")
    async def btn_info(message: Message):
        await message.answer("ℹ️ Информация\n\nВыберите что хотите узнать:", keyboard=get_info_keyboard())

    @labeler.message(text="⚙️ Настройки")
    async def btn_settings(message: Message):
        await message.answer("⚙️ Настройки", keyboard=get_settings_keyboard())

    @labeler.message(text="🔑 Изменить логин/пароль")
    async def btn_change_login(message: Message):
        await set_user_state(bot.state_dispenser, message.peer_id, "login:waiting_for_login")
        await message.answer("🔐 Настройка учётных данных\n\nВведите логин от cabinet.ruobr.ru:\n\n❌ Отмена — для выхода", keyboard=get_cancel_keyboard())

    @labeler.message(text="👥 Одноклассники")
    async def btn_classmates(message: Message):
        user_config = await get_user(message.peer_id)
        if not user_config or not user_config.login:
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
                await set_user_state(bot.state_dispenser, message.peer_id, "select_child:classmates", {"children": [{"id": c.id, "name": c.full_name, "group": c.group} for c in children]})
                await message.answer("👦👧 Выберите ребенка:", keyboard=get_child_select_keyboard(children))
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")

    @labeler.message(text="👩‍🏫 Учителя")
    async def btn_teachers(message: Message):
        user_config = await get_user(message.peer_id)
        if not user_config or not user_config.login:
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
                await set_user_state(bot.state_dispenser, message.peer_id, "select_child:teachers", {"children": [{"id": c.id, "name": c.full_name, "group": c.group} for c in children]})
                await message.answer("👦👧 Выберите ребенка:", keyboard=get_child_select_keyboard(children))
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")

    @labeler.message(text="🏆 Достижения")
    async def btn_achievements(message: Message):
        user_config = await get_user(message.peer_id)
        if not user_config or not user_config.login:
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
                await set_user_state(bot.state_dispenser, message.peer_id, "select_child:achievements", {"children": [{"id": c.id, "name": c.full_name, "group": c.group} for c in children]})
                await message.answer("👦👧 Выберите ребенка:", keyboard=get_child_select_keyboard(children))
        except Exception as e:
            await message.answer(f"❌ Ошибка: {e}")

    @labeler.message(text="📋 Справка")
    async def btn_help(message: Message):
        await message.answer("📋 Справка по боту\n\nШкольный бот — помогает родителям следить за учёбой детей.\n\n"
                            "📅 Расписание:\n• «Расписание сегодня» — уроки на сегодня\n• «Расписание завтра» — уроки на завтра\n\n"
                            "📘 Домашние задания:\n• «ДЗ на завтра» — задания на завтрашний день\n\n"
                            "⭐ Оценки:\n• «Оценки сегодня» — оценки за сегодняшний день\n\n"
                            "🍽 Питание:\n• «Баланс питания» — текущий баланс счёта\n• «Питание сегодня» — что ребёнок ел сегодня\n\n"
                            "📝 Команды:\n/start — главное меню\n/set_login — настроить учётные данные\n/balance — баланс питания\n/ttoday — расписание сегодня\n/ttomorrow — расписание завтра")

    @labeler.message(text="◀️ Назад")
    async def btn_back(message: Message):
        await clear_user_state(bot.state_dispenser, message.peer_id)
        await message.answer("🏠 Главное меню", keyboard=get_main_keyboard())

    @labeler.message(text="👤 Мой профиль")
    async def btn_profile(message: Message):
        user_config = await get_user(message.peer_id)
        if not user_config:
            await message.answer("Профиль не найден. Используйте /start", keyboard=get_main_keyboard())
            return
        status = "✅ Настроен" if user_config.login and user_config.password else "❌ Не настроен"
        await message.answer(f"👤 Ваш профиль\n\nСтатус: {status}\nЛогин: {user_config.login or 'не указан'}\n\n"
                            f"Уведомления о балансе: {'🔔 Включены' if user_config.enabled else '🔕 Выключены'}\n"
                            f"Уведомления об оценках: {'🔔 Включены' if user_config.marks_enabled else '🔕 Выключены'}", keyboard=get_settings_keyboard())

    @labeler.message(text="/enable")
    async def cmd_enable(message: Message):
        await create_or_update_user(message.peer_id, enabled=True, marks_enabled=True)
        await message.answer("🔔 Уведомления включены!")

    @labeler.message(text="/disable")
    async def cmd_disable(message: Message):
        await create_or_update_user(message.peer_id, enabled=False, marks_enabled=False)
        await message.answer("🔕 Уведомления отключены.")

    @labeler.message(text="🔔 Уведомления")
    async def btn_notifications(message: Message):
        user_config = await get_user(message.peer_id) or await create_or_update_user(message.peer_id)
        await message.answer("🔔 Настройки уведомлений\n\nНажмите для включения/выключения:", keyboard=get_notification_keyboard(user_config))

    # ===== Баланс питания =====
    @labeler.message(text="/balance")
    @labeler.message(text="💰 Баланс питания")
    async def cmd_balance(message: Message):
        user_config = await get_user(message.peer_id)
        result = await require_authentication(message, user_config)
        if not result:
            return
        login, password, children = result
        status_msg = await message.answer("🔄 Загрузка...")
        try:
            food_info = await get_food_for_children(login, password, children)
            thresholds = await get_all_thresholds_for_peer(message.peer_id)
            lines = ["💰 Баланс питания\n"]
            for idx, child in enumerate(children, 1):
                info = food_info.get(child.id)
                threshold = thresholds.get(child.id, config.default_balance_threshold)
                if info and info.has_food:
                    lines.append(f"{idx}. {format_balance(child, info.balance, threshold)}")
                else:
                    lines.append(f"{idx}. {child.full_name} ({child.group}): питание недоступно")
            lines.append("\n💡 Настройте порог через /set_threshold")
            await status_msg.edit("\n".join(lines))
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")

    @labeler.message(text="/foodtoday")
    @labeler.message(text="🍽 Питание сегодня")
    async def cmd_foodtoday(message: Message):
        user_config = await get_user(message.peer_id)
        result = await require_authentication(message, user_config)
        if not result:
            return
        login, password, children = result
        status_msg = await message.answer("🔄 Загрузка...")
        try:
            today_str = date.today().strftime("%Y-%m-%d")
            food_info = await get_food_for_children(login, password, children)
            lines = [f"🍽 Питание сегодня ({format_date(today_str)})"]
            found = False
            for child in children:
                info = food_info.get(child.id)
                if info and info.visits:
                    for visit in info.visits:
                        if visit.get("date") == today_str and (visit.get("ordered") or visit.get("state") == 30):
                            found = True
                            lines.append(format_food_visit(visit, child.full_name))
            await status_msg.edit(truncate_text("\n".join(lines)) if found else f"ℹ️ На сегодня питания не найдено.")
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")

    # ===== Порог баланса =====
    @labeler.message(text="/set_threshold")
    @labeler.message(text="💰 Порог баланса")
    async def cmd_set_threshold(message: Message):
        user_config = await get_user(message.peer_id)
        result = await require_authentication(message, user_config)
        if not result:
            return
        login, password, children = result
        thresholds = await get_all_thresholds_for_peer(message.peer_id)
        lines = ["⚙️ Настройка порога баланса\n", "Выберите ребёнка:\n"]
        for idx, child in enumerate(children, 1):
            threshold = thresholds.get(child.id, config.default_balance_threshold)
            lines.append(f"{idx}. {child.full_name} ({child.group}) — {threshold:.0f} ₽")
        lines.append("\n📝 Введите номер ребёнка.")
        await set_user_state(bot.state_dispenser, message.peer_id, "threshold:waiting_for_child_selection", {"children": [{"id": c.id, "name": c.full_name, "group": c.group} for c in children]})
        await message.answer("\n".join(lines), keyboard=get_cancel_keyboard())

    # ===== Расписание =====
    @labeler.message(text="/ttoday")
    @labeler.message(text="📅 Расписание сегодня")
    async def cmd_ttoday(message: Message):
        user_config = await get_user(message.peer_id)
        result = await require_authentication(message, user_config)
        if not result:
            return
        login, password, children = result
        status_msg = await message.answer("🔄 Загрузка...")
        try:
            today = date.today()
            timetable = await get_timetable_for_children(login, password, children, today, today)
            lines = [f"📅 Расписание на сегодня ({format_date(str(today))}, {format_weekday(today)})"]
            found = False
            for child in children:
                lessons = timetable.get(child.id, [])
                if lessons:
                    found = True
                    lines.append(f"\n👦 {child.full_name} ({child.group}):")
                    for lesson in lessons:
                        lines.append(format_lesson(lesson, show_details=True))
            await status_msg.edit(truncate_text("\n".join(lines)) if found else "ℹ️ На сегодня расписание не найдено.")
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")

    @labeler.message(text="/ttomorrow")
    @labeler.message(text="📅 Расписание завтра")
    async def cmd_ttomorrow(message: Message):
        user_config = await get_user(message.peer_id)
        result = await require_authentication(message, user_config)
        if not result:
            return
        login, password, children = result
        status_msg = await message.answer("🔄 Загрузка...")
        try:
            tomorrow = date.today() + timedelta(days=1)
            timetable = await get_timetable_for_children(login, password, children, tomorrow, tomorrow)
            lines = [f"📅 Расписание на завтра ({format_date(str(tomorrow))}, {format_weekday(tomorrow)})"]
            found = False
            for child in children:
                lessons = timetable.get(child.id, [])
                if lessons:
                    found = True
                    lines.append(f"\n👦 {child.full_name} ({child.group}):")
                    for lesson in lessons:
                        lines.append(format_lesson(lesson, show_details=True))
            await status_msg.edit(truncate_text("\n".join(lines)) if found else "ℹ️ На завтра расписание не найдено.")
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")

    @labeler.message(text="/hwtomorrow")
    @labeler.message(text="📘 ДЗ на завтра")
    async def cmd_hwtomorrow(message: Message):
        user_config = await get_user(message.peer_id)
        result = await require_authentication(message, user_config)
        if not result:
            return
        login, password, children = result
        status_msg = await message.answer("🔄 Загрузка...")
        try:
            today = date.today()
            tomorrow = today + timedelta(days=1)
            tomorrow_str = tomorrow.strftime("%Y-%m-%d")
            monday = today - timedelta(days=today.weekday())
            sunday = monday + timedelta(days=6)
            timetable = await get_timetable_for_children(login, password, children, monday, sunday)
            lines = [f"📘 Домашнее задание на завтра ({format_date(tomorrow_str)})"]
            found = False
            for child in children:
                lessons = timetable.get(child.id, [])
                child_header_added = False
                for lesson in lessons:
                    for hw in lesson.homework:
                        if hw.get("deadline") == tomorrow_str:
                            if not child_header_added:
                                lines.append(f"\n👦 {child.full_name} ({child.group}):")
                                child_header_added = True
                            found = True
                            lines.append(f"  📖 {lesson.subject}: {hw.get('title', '')}")
                            hw_text = hw.get("text", "")
                            if has_meaningful_text(hw_text):
                                clean_text = clean_html_text(hw_text)[:500]
                                lines.append(f"     📝 {clean_text}")
            await status_msg.edit(truncate_text("\n".join(lines)) if found else "ℹ️ На завтра ДЗ не найдено.")
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")

    @labeler.message(text="/markstoday")
    @labeler.message(text="⭐ Оценки сегодня")
    async def cmd_markstoday(message: Message):
        user_config = await get_user(message.peer_id)
        result = await require_authentication(message, user_config)
        if not result:
            return
        login, password, children = result
        status_msg = await message.answer("🔄 Загрузка...")
        try:
            today = date.today()
            timetable = await get_timetable_for_children(login, password, children, today, today)
            lines = [f"⭐ Оценки за сегодня ({format_date(str(today))})"]
            found = False
            for child in children:
                lessons = timetable.get(child.id, [])
                child_header_added = False
                for lesson in lessons:
                    if lesson.marks:
                        if not child_header_added:
                            lines.append(f"\n👦 {child.full_name} ({child.group}):")
                            child_header_added = True
                        for mark in lesson.marks:
                            found = True
                            lines.append(f"  {format_mark(mark, lesson.subject)}")
            await status_msg.edit(truncate_text("\n".join(lines)) if found else "ℹ️ За сегодня оценок не найдено.")
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")

    # ===== Запуск =====
    async def on_startup():
        await db_pool.initialize()
        logger.info("Database initialized")

    async def on_shutdown():
        await db_pool.close()
        logger.info("Bot stopped")

    # Добавляем задачи в loop_wrapper
    bot.loop_wrapper.on_startup.append(on_startup())
    bot.loop_wrapper.on_shutdown.append(on_shutdown())
    bot.loop_wrapper.add_task(NotificationService(bot.api).start())
    bot.loop_wrapper.add_task(periodic_cache_cleanup(interval=300))
    
    logger.info("Bot started. Press Ctrl+C to stop.")
    bot.run_polling()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.exception(f"Fatal error: {e}")
        sys.exit(1)
