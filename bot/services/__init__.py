"""
Сервисы бота.
"""
from .ruobr_client import (
    RuobrClient,
    Child,
    FoodInfo,
    Lesson,
    Classmate,
    AchievementDirection,
    Achievements,
    Teacher,
    SchoolGuide,
    RuobrError,
    AuthenticationError,
    NetworkError,
    RateLimitError,
    DataError,
    get_children_async,
    get_food_for_children,
    get_timetable_for_children,
    get_classmates_for_child,
    get_achievements_for_child,
    get_guide_for_child,
)

from .cache import (
    MemoryCache,
    children_cache,
    timetable_cache,
    food_cache,
    threshold_cache,
    get_cache_key,
    invalidate_user_cache,
    periodic_cache_cleanup,
)

__all__ = [
    # Client
    "RuobrClient",
    "Child",
    "FoodInfo",
    "Lesson",
    "Classmate",
    "AchievementDirection",
    "Achievements",
    "Teacher",
    "SchoolGuide",
    # Errors
    "RuobrError",
    "AuthenticationError",
    "NetworkError",
    "RateLimitError",
    "DataError",
    # Functions
    "get_children_async",
    "get_food_for_children",
    "get_timetable_for_children",
    "get_classmates_for_child",
    "get_achievements_for_child",
    "get_guide_for_child",
    # Cache
    "MemoryCache",
    "children_cache",
    "timetable_cache",
    "food_cache",
    "threshold_cache",
    "get_cache_key",
    "invalidate_user_cache",
    "periodic_cache_cleanup",
]
