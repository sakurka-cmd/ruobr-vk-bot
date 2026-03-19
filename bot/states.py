"""
Определение состояний FSM для многошаговых операций.
"""
from enum import Enum


class StateGroup(Enum):
    """Базовый класс для групп состояний."""
    pass


class LoginStates(str, Enum):
    """Состояния процесса входа."""
    waiting_for_login = "login:waiting_for_login"
    waiting_for_password = "login:waiting_for_password"


class ThresholdStates(str, Enum):
    """Состояния настройки порога баланса."""
    waiting_for_child_selection = "threshold:waiting_for_child_selection"
    waiting_for_threshold_value = "threshold:waiting_for_threshold_value"


class NotificationStates(str, Enum):
    """Состояния настройки уведомлений."""
    choosing_notification_type = "notification:choosing_notification_type"
    setting_parameters = "notification:setting_parameters"
