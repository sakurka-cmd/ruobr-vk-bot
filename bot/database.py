"""
Модуль работы с базой данных.
Реализует пул соединений и асинхронные операции.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, AsyncGenerator

import aiosqlite
from aiosqlite import Connection, Cursor

from .config import config
from .encryption import encrypt_password, decrypt_password

logger = logging.getLogger(__name__)


@dataclass
class UserConfig:
    """Конфигурация пользователя."""
    peer_id: int  # VK peer_id (user_id для личных сообщений)
    login: Optional[str] = None
    password_encrypted: Optional[str] = None
    password: Optional[str] = None  # Расшифрованный пароль (только для чтения)
    enabled: bool = False
    marks_enabled: bool = True
    food_enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        """Дешифрование пароля при необходимости."""
        if self.password_encrypted and not self.password:
            try:
                self.password = decrypt_password(self.password_encrypted)
            except Exception as e:
                logger.warning(f"Failed to decrypt password for user {self.peer_id}: {e}")


@dataclass
class ChildThreshold:
    """Настройки порога баланса для ребёнка."""
    peer_id: int
    child_id: int
    threshold: float
    updated_at: Optional[datetime] = None


class DatabasePool:
    """
    Пул соединений с базой данных SQLite.
    Обеспечивает потокобезопасный доступ к БД.
    """

    _instance: Optional['DatabasePool'] = None
    _lock = asyncio.Lock()

    def __new__(cls) -> 'DatabasePool':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._pool: List[Connection] = []
        self._pool_size = 5
        self._db_path: Optional[Path] = None

    async def initialize(self, db_path: Optional[Path] = None) -> None:
        """
        Инициализация пула соединений.

        Args:
            db_path: Путь к файлу базы данных.
        """
        self._db_path = db_path or config.db_path

        # Создаём директорию если не существует
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Создаём начальные соединения
        for _ in range(self._pool_size):
            conn = await aiosqlite.connect(self._db_path)
            conn.row_factory = aiosqlite.Row
            self._pool.append(conn)

        # Инициализируем схему
        await self._init_schema()
        logger.info(f"Database pool initialized: {self._db_path}")

    async def _init_schema(self) -> None:
        """Создание схемы базы данных."""
        async with self.connection() as conn:
            await conn.executescript("""
                -- Таблица пользователей
                CREATE TABLE IF NOT EXISTS users (
                    peer_id INTEGER PRIMARY KEY,
                    login TEXT,
                    password_encrypted TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    marks_enabled INTEGER NOT NULL DEFAULT 1,
                    food_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Таблица порогов баланса
                CREATE TABLE IF NOT EXISTS thresholds (
                    peer_id INTEGER NOT NULL,
                    child_id INTEGER NOT NULL,
                    threshold REAL NOT NULL DEFAULT 300.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (peer_id, child_id),
                    FOREIGN KEY (peer_id) REFERENCES users(peer_id) ON DELETE CASCADE
                );

                -- Таблица FSM состояний
                CREATE TABLE IF NOT EXISTS fsm_states (
                    peer_id INTEGER PRIMARY KEY,
                    state TEXT NOT NULL,
                    data TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- Таблица истории уведомлений (для дедупликации)
                CREATE TABLE IF NOT EXISTS notification_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_id INTEGER NOT NULL,
                    notification_type TEXT NOT NULL,
                    notification_key TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(peer_id, notification_type, notification_key)
                );

                -- Индексы для оптимизации
                CREATE INDEX IF NOT EXISTS idx_thresholds_peer_id ON thresholds(peer_id);
                CREATE INDEX IF NOT EXISTS idx_users_enabled ON users(enabled);
                CREATE INDEX IF NOT EXISTS idx_notification_history_peer ON notification_history(peer_id, created_at);
            """)
            await conn.commit()

            # Миграция: добавляем колонку food_enabled если её нет
            try:
                async with conn.execute("PRAGMA table_info(users)") as cursor:
                    columns = await cursor.fetchall()
                    column_names = [col[1] for col in columns]

                if "food_enabled" not in column_names:
                    await conn.execute("ALTER TABLE users ADD COLUMN food_enabled INTEGER NOT NULL DEFAULT 1")
                    await conn.commit()
                    logger.info("Migration: added food_enabled column to users table")
            except Exception as e:
                logger.warning(f"Migration check failed (may be normal): {e}")

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[Connection, None]:
        """
        Контекстный менеджер для получения соединения из пула.

        Yields:
            Соединение с базой данных.
        """
        conn = None
        try:
            if self._pool:
                conn = self._pool.pop()
            else:
                # Создаём новое соединение если пул пуст
                conn = await aiosqlite.connect(self._db_path)
                conn.row_factory = aiosqlite.Row

            yield conn
        finally:
            if conn:
                # Возвращаем соединение в пул
                if len(self._pool) < self._pool_size:
                    self._pool.append(conn)
                else:
                    await conn.close()

    async def close(self) -> None:
        """Закрытие всех соединений в пуле."""
        for conn in self._pool:
            await conn.close()
        self._pool.clear()
        logger.info("Database pool closed")


# Глобальный экземпляр пула
db_pool = DatabasePool()


# ===== Операции с пользователями =====

async def get_user(peer_id: int) -> Optional[UserConfig]:
    """
    Получение конфигурации пользователя.

    Args:
        peer_id: ID пользователя VK (peer_id).

    Returns:
        Конфигурация пользователя или None если не найден.
    """
    async with db_pool.connection() as conn:
        async with conn.execute(
            "SELECT * FROM users WHERE peer_id = ?", (peer_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None

            return UserConfig(
                peer_id=row["peer_id"],
                login=row["login"],
                password_encrypted=row["password_encrypted"],
                enabled=bool(row["enabled"]),
                marks_enabled=bool(row["marks_enabled"]),
                food_enabled=bool(row["food_enabled"]) if "food_enabled" in row.keys() else True,
                created_at=row["created_at"],
                updated_at=row["updated_at"]
            )


async def create_or_update_user(
    peer_id: int,
    login: Optional[str] = None,
    password: Optional[str] = None,
    enabled: Optional[bool] = None,
    marks_enabled: Optional[bool] = None,
    food_enabled: Optional[bool] = None
) -> UserConfig:
    """
    Создание или обновление пользователя.

    Args:
        peer_id: ID пользователя VK (peer_id).
        login: Логин от Ruobr.
        password: Пароль от Ruobr (будет зашифрован).
        enabled: Включены ли уведомления о балансе.
        marks_enabled: Включены ли уведомления об оценках.
        food_enabled: Включены ли уведомления о питании.

    Returns:
        Обновлённая конфигурация пользователя.
    """
    # Шифруем пароль если он передан
    password_encrypted = None
    if password:
        password_encrypted = encrypt_password(password)

    async with db_pool.connection() as conn:
        # Проверяем существование пользователя
        async with conn.execute(
            "SELECT peer_id FROM users WHERE peer_id = ?", (peer_id,)
        ) as cursor:
            exists = await cursor.fetchone() is not None

        if exists:
            # Обновляем существующего пользователя
            updates = ["updated_at = CURRENT_TIMESTAMP"]
            params: List[Any] = []

            if login is not None:
                updates.append("login = ?")
                params.append(login)
            if password_encrypted is not None:
                updates.append("password_encrypted = ?")
                params.append(password_encrypted)
            if enabled is not None:
                updates.append("enabled = ?")
                params.append(1 if enabled else 0)
            if marks_enabled is not None:
                updates.append("marks_enabled = ?")
                params.append(1 if marks_enabled else 0)
            if food_enabled is not None:
                updates.append("food_enabled = ?")
                params.append(1 if food_enabled else 0)

            params.append(peer_id)
            await conn.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE peer_id = ?",
                params
            )
        else:
            # Создаём нового пользователя
            await conn.execute(
                """INSERT INTO users (peer_id, login, password_encrypted, enabled, marks_enabled, food_enabled)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    peer_id,
                    login,
                    password_encrypted,
                    1 if enabled else 0 if enabled is not None else 0,
                    1 if marks_enabled else 0 if marks_enabled is not None else 1,
                    1 if food_enabled else 0 if food_enabled is not None else 1
                )
            )

        await conn.commit()

    return await get_user(peer_id)


async def get_all_enabled_users() -> List[UserConfig]:
    """Получение всех пользователей с включёнными уведомлениями."""
    async with db_pool.connection() as conn:
        async with conn.execute(
            "SELECT * FROM users WHERE enabled = 1 OR marks_enabled = 1 OR food_enabled = 1"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                UserConfig(
                    peer_id=row["peer_id"],
                    login=row["login"],
                    password_encrypted=row["password_encrypted"],
                    enabled=bool(row["enabled"]),
                    marks_enabled=bool(row["marks_enabled"]),
                    food_enabled=bool(row["food_enabled"]) if "food_enabled" in row.keys() else True
                )
                for row in rows
            ]


# ===== Операции с порогами =====

async def get_child_threshold(peer_id: int, child_id: int) -> float:
    """Получение порога баланса для ребёнка."""
    async with db_pool.connection() as conn:
        async with conn.execute(
            "SELECT threshold FROM thresholds WHERE peer_id = ? AND child_id = ?",
            (peer_id, child_id)
        ) as cursor:
            row = await cursor.fetchone()
            return float(row["threshold"]) if row else config.default_balance_threshold


async def set_child_threshold(peer_id: int, child_id: int, threshold: float) -> None:
    """Установка порога баланса для ребёнка."""
    async with db_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO thresholds (peer_id, child_id, threshold, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(peer_id, child_id) DO UPDATE SET
                   threshold = excluded.threshold,
                   updated_at = CURRENT_TIMESTAMP""",
            (peer_id, child_id, threshold)
        )
        await conn.commit()


async def get_all_thresholds_for_peer(peer_id: int) -> Dict[int, float]:
    """Получение всех порогов для пользователя."""
    async with db_pool.connection() as conn:
        async with conn.execute(
            "SELECT child_id, threshold FROM thresholds WHERE peer_id = ?",
            (peer_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return {int(row["child_id"]): float(row["threshold"]) for row in rows}


# ===== Операции с историей уведомлений =====

async def is_notification_sent(peer_id: int, notification_type: str, notification_key: str) -> bool:
    """Проверка было ли уже отправлено уведомление."""
    async with db_pool.connection() as conn:
        async with conn.execute(
            """SELECT 1 FROM notification_history
               WHERE peer_id = ? AND notification_type = ? AND notification_key = ?""",
            (peer_id, notification_type, notification_key)
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_notification_sent(peer_id: int, notification_type: str, notification_key: str) -> None:
    """Отметить уведомление как отправленное."""
    async with db_pool.connection() as conn:
        await conn.execute(
            """INSERT OR IGNORE INTO notification_history
               (peer_id, notification_type, notification_key) VALUES (?, ?, ?)""",
            (peer_id, notification_type, notification_key)
        )
        await conn.commit()


async def cleanup_old_notifications(days: int = 30) -> None:
    """Очистка старых записей истории уведомлений."""
    async with db_pool.connection() as conn:
        await conn.execute(
            f"DELETE FROM notification_history WHERE created_at < datetime('now', '-{days} days')"
        )
        await conn.commit()


# ===== FSM операции =====

async def save_fsm_state(peer_id: int, state: str, data: Optional[str] = None) -> None:
    """Сохранение состояния FSM."""
    async with db_pool.connection() as conn:
        await conn.execute(
            """INSERT INTO fsm_states (peer_id, state, data, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(peer_id) DO UPDATE SET
                   state = excluded.state,
                   data = excluded.data,
                   updated_at = CURRENT_TIMESTAMP""",
            (peer_id, state, data)
        )
        await conn.commit()


async def get_fsm_state(peer_id: int) -> Optional[Dict[str, Any]]:
    """Получение состояния FSM."""
    async with db_pool.connection() as conn:
        async with conn.execute(
            "SELECT state, data FROM fsm_states WHERE peer_id = ?", (peer_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"state": row["state"], "data": row["data"]}
            return None


async def clear_fsm_state(peer_id: int) -> None:
    """Очистка состояния FSM."""
    async with db_pool.connection() as conn:
        await conn.execute("DELETE FROM fsm_states WHERE peer_id = ?", (peer_id,))
        await conn.commit()
