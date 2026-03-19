"""
Определение состояний FSM для многошаговых операций.
"""
from vkbottle.bot import StateGroup, State


class LoginStates(StateGroup):
    """Состояния процесса входа."""
    waiting_for_login = State()
    waiting_for_password = State()


class ThresholdStates(StateGroup):
    """Состояния настройки порога баланса."""
    waiting_for_child_selection = State()
    waiting_for_threshold_value = State()


class NotificationStates(StateGroup):
    """Состояния настройки уведомлений."""
    choosing_notification_type = State()
    setting_parameters = State()
