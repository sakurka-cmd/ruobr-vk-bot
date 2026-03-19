"""
Определение состояний FSM для многошаговых операций.
"""
from vkbottle import StatePeer


class LoginStates:
    """Состояния процесса входа."""
    waiting_for_login = "login:waiting_for_login"
    waiting_for_password = "login:waiting_for_password"


class ThresholdStates:
    """Состояния настройки порога баланса."""
    waiting_for_child_selection = "threshold:waiting_for_child_selection"
    waiting_for_threshold_value = "threshold:waiting_for_threshold_value"


class NotificationStates:
    """Состояния настройки уведомлений."""
    choosing_notification_type = "notification:choosing_notification_type"
    setting_parameters = "notification:setting_parameters"
