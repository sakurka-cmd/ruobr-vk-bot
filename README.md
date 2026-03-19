# Ruobr VK Bot

Школьный бот для VK Messenger, помогающий родителям следить за учёбой детей.

## Возможности

- 💰 **Баланс питания** - отслеживание баланса школьного питания
- 📅 **Расписание** - просмотр расписания уроков на сегодня/завтра
- 📘 **Домашние задания** - просмотр ДЗ на завтра
- ⭐ **Оценки** - просмотр оценок за день
- 🔔 **Уведомления** - автоматические уведомления о низком балансе, новых оценках и питании
- 👥 **Одноклассники** - список класса с датами рождения
- 👩‍🏫 **Учителя** - список учителей-предметников
- 🏆 **Достижения** - достижения и проекты ученика

## Установка

### Через Docker (рекомендуется)

1. Клонируйте репозиторий:
```bash
git clone https://github.com/YOUR_USERNAME/ruobr-vk-bot.git
cd ruobr-vk-bot
```

2. Создайте файл `.env`:
```bash
cp .env.example .env
```

3. Отредактируйте `.env` и укажите свои данные:
```env
VK_TOKEN=your_vk_group_token
VK_GROUP_ID=your_group_id
ENCRYPTION_KEY=your_encryption_key
ADMIN_IDS=123456789,987654321
```

4. Запустите:
```bash
docker-compose up -d
```

### Без Docker

1. Установите Python 3.11+

2. Клонируйте репозиторий:
```bash
git clone https://github.com/YOUR_USERNAME/ruobr-vk-bot.git
cd ruobr-vk-bot
```

3. Создайте виртуальное окружение:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows
```

4. Установите зависимости:
```bash
pip install -r requirements.txt
```

5. Создайте файл `.env`:
```bash
cp .env.example .env
```

6. Отредактируйте `.env` и укажите свои данные

7. Запустите:
```bash
python main.py
```

## Настройка

### Создание VK Community

1. Создайте группу VK или используйте существующую
2. Перейдите в Управление → Работа с API → Ключи доступа
3. Создайте ключ с правами: `messages`, `groups`
4. Скопируйте токен в `VK_TOKEN`
5. ID группы укажите в `VK_GROUP_ID`

### Генерация ключа шифрования

```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

Скопируйте результат в `ENCRYPTION_KEY`.

### Включение сообщений сообщества

1. Перейдите в Управление → Сообщения
2. Включите сообщения сообщества
3. Настройте приветственное сообщение (опционально)

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/set_login` | Настроить логин/пароль Ruobr |
| `/balance` | Баланс питания |
| `/ttoday` | Расписание сегодня |
| `/ttomorrow` | Расписание завтра |
| `/hwtomorrow` | ДЗ на завтра |
| `/markstoday` | Оценки сегодня |
| `/enable` | Включить уведомления |
| `/disable` | Выключить уведомления |

## Переменные окружения

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `VK_TOKEN` | Да | Токен VK группы |
| `VK_GROUP_ID` | Да | ID VK группы |
| `ENCRYPTION_KEY` | Да | Ключ шифрования Fernet |
| `ADMIN_IDS` | Нет | ID администраторов (через запятую) |
| `LOG_LEVEL` | Нет | Уровень логирования (INFO, DEBUG) |
| `CHECK_INTERVAL_SECONDS` | Нет | Интервал проверки уведомлений |
| `DEFAULT_BALANCE_THRESHOLD` | Нет | Порог баланса по умолчанию |

## Структура проекта

```
ruobr-vk-bot/
├── bot/
│   ├── handlers/
│   │   ├── auth.py        # Аутентификация и базовые команды
│   │   ├── balance.py     # Баланс питания
│   │   └── schedule.py    # Расписание и оценки
│   ├── services/
│   │   ├── ruobr_client.py # Клиент Ruobr API
│   │   ├── cache.py       # Кэширование
│   │   └── notifications.py # Фоновые уведомления
│   ├── utils/
│   │   └── formatters.py  # Форматирование вывода
│   ├── config.py          # Конфигурация
│   ├── database.py        # Работа с БД
│   ├── encryption.py      # Шифрование
│   ├── middlewares.py     # Middleware
│   └── states.py          # FSM состояния
├── data/                   # Данные (БД, логи)
├── main.py                 # Точка входа
├── requirements.txt        # Зависимости
├── Dockerfile
├── docker-compose.yml
└── README.md
```

## Лицензия

MIT
