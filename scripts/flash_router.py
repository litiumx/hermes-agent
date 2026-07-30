#!/usr/bin/env python3
"""
Flash-first intelligent router — определяет сложность задачи и выбирает модель.
Работает КАК ЧАСТЬ агента, не отдельно. Интегрируется в conversation_loop.py.
0 токенов — использует только структурные признаки, не LLM.
"""
import re, os, json
from dataclasses import dataclass, field
from typing import Optional, Tuple
from enum import Enum

class Effort(Enum):
    FLASH = "flash"           # deepseek-v4-flash, thinking=disabled
    FLASH_THINK = "flash_think"  # deepseek-v4-flash, thinking=enabled
    PRO = "pro"               # deepseek-v4-pro, effort=high
    PRO_MAX = "pro_max"       # deepseek-v4-pro, effort=max

class SwarmSize(Enum):
    NONE = 0
    SMALL = 3
    MEDIUM = 10
    LARGE = 30
    FULL = 100

@dataclass
class RoutingDecision:
    effort: Effort = Effort.FLASH
    swarm: SwarmSize = SwarmSize.NONE
    reason: str = ""

# Признаки сложности → эскалация
COMPLEXITY_MARKERS = [
    # Режим 1: структурные признаки (бесплатно)
    (r"(?i)(архитектур|спроектируй|перепроектируй|рефакторинг?|перепиши\s+(полностью|с\s+нуля))", Effort.PRO, "architecture_request"),
    (r"(?i)(исправь\s+баг|почини|debug\b|отлад(к|ить)|не\s+работает|сломано|ошибк[аи]\s+(в|при|из-за)|провер(ь|ки|ить).*?(файлы|файлов|на\s+ошибки|наличие\s+багов)\b)", Effort.PRO, "debug_request"),
    (r"(?i)(ауди(т|ровать)|проверь\s+безопасность|security\s+(audit|review)|vulnerability)", Effort.PRO_MAX, "security_audit"),
    (r"(?i)(миграци[яи]|migration|обнови\s+(все|all)\s+(зависимости|deps))", Effort.PRO, "migration_request"),
    (r"(?i)(анализ|исследуй|разберись|почему|как\s+работает|explain)", Effort.FLASH_THINK, "analysis_request"),

    # Режим 2: размер задачи
    (r"(?i)(3\+?\s*(файла|files)|несколько\s+файлов|multi.?file|many\s+files)", Effort.PRO, "multi_file_scope"),
    (r"(?i)(10\+?\s*(файлов|files)|весь\s+(проект|project)|codebase.?wide)", Effort.PRO_MAX, "large_scope"),

    # Режим 3: явный запрос модели
    (r"(?i)(используй\s+pro|включи\s+pro|pro\s*модель|думай\s+(глубже|максимально|max|harder))", Effort.PRO_MAX, "explicit_pro_request"),
    (r"(?i)(быстро|flash|быстрый\s+ответ|не\s+думай)", Effort.FLASH, "explicit_flash_request"),
]

# Авто-swarm ОТКЛЮЧЁН. Только ручной режим через /swarm.
SWARM_MARKERS = []
# Ручной режим: /swarm N в swarm.py. Никаких авто-триггеров по ключевым словам.

def route(user_message: str, context_messages: int = 0) -> RoutingDecision:
    """
    Определяет модель и размер swarm на основе текста запроса.
    Вызывается ПЕРЕД API-вызовом.

    Возвращает RoutingDecision с effort и swarm.
    ВАЖНО: это НЕ заменяет роутинг Hermes (model_router.yaml).
    Это дополнительный слой для авто-эскалации Flash→Pro и авто-swarm.
    """
    decision = RoutingDecision()

    # 1. Проверка признаков сложности (от высокого к низкому)
    for pattern, effort, reason in COMPLEXITY_MARKERS:
        if re.search(pattern, user_message):
            # Выбираем самый высокий effort из всех совпадений
            effort_order = {Effort.FLASH: 0, Effort.FLASH_THINK: 1, Effort.PRO: 2, Effort.PRO_MAX: 3}
            current_order = effort_order.get(decision.effort, 0)
            new_order = effort_order.get(effort, 0)
            if new_order > current_order:
                decision.effort = effort
                decision.reason = reason

    # 2. Проверка для авто-swarm
    for pattern, size, reason in SWARM_MARKERS:
        if re.search(pattern, user_message):
            size_order = {SwarmSize.NONE: 0, SwarmSize.SMALL: 1, SwarmSize.MEDIUM: 2,
                         SwarmSize.LARGE: 3, SwarmSize.FULL: 4}
            current = size_order.get(decision.swarm, 0)
            new = size_order.get(size, 0)
            if new > current:
                decision.swarm = size
                if not decision.reason:
                    decision.reason = reason

    # 3. Учёт истории: если уже > 50 сообщений → минимум Flash+think
    if context_messages > 50 and decision.effort == Effort.FLASH:
        decision.effort = Effort.FLASH_THINK
        decision.reason = "long_session"

    # 4. Длинный запрос → минимум Flash+think
    if len(user_message) > 1000 and decision.effort == Effort.FLASH:
        decision.effort = Effort.FLASH_THINK
        decision.reason = "long_message"

    return decision


def route_to_config(decision: RoutingDecision) -> dict:
    """Преобразует RoutingDecision в параметры для API-вызова."""
    config = {"model": "deepseek-v4-flash", "thinking": {"type": "disabled"}}

    if decision.effort == Effort.FLASH:
        config["model"] = "deepseek-v4-flash"
        config["thinking"]["type"] = "disabled"
    elif decision.effort == Effort.FLASH_THINK:
        config["model"] = "deepseek-v4-flash"
        config["thinking"] = {"type": "enabled"}
    elif decision.effort == Effort.PRO:
        config["model"] = "deepseek-v4-pro"
        config["reasoning_effort"] = "high"
    elif decision.effort == Effort.PRO_MAX:
        config["model"] = "deepseek-v4-pro"
        config["reasoning_effort"] = "max"

    return config


def suggest_swarm(decision: RoutingDecision) -> Optional[int]:
    """Возвращает рекомендуемый размер swarm или None."""
    if decision.swarm == SwarmSize.NONE:
        return None
    return decision.swarm.value


# Тесты при прямом запуске
if __name__ == "__main__":
    tests = [
        ("привет как дела", Effort.FLASH, None),
        ("почему не работает ssh на vps", Effort.PRO, None),
        ("спроектируй архитектуру нового микросервиса", Effort.PRO, None),
        ("сделай полный аудит безопасности всех файлов проекта", Effort.PRO_MAX, SwarmSize.LARGE),
        ("объясни как работает docker compose", Effort.FLASH_THINK, None),
        ("проверь все модули на наличие багов", Effort.FLASH, SwarmSize.MEDIUM),
        ("быстро скажи сколько места на диске", Effort.FLASH, None),
        ("миграция с sqlite на postgres во всех файлах", Effort.PRO, SwarmSize.MEDIUM),
        ("используй про модель и подумай максимально глубоко", Effort.PRO_MAX, None),
        ("просканируй весь проект на уязвимости", Effort.PRO_MAX, SwarmSize.LARGE),
    ]

    for msg, expected_effort, expected_swarm in tests:
        d = route(msg)
        sw = suggest_swarm(d)
        eff_ok = d.effort == expected_effort
        sw_ok = (sw == expected_swarm) or (sw is None and expected_swarm is None) or \
                (isinstance(sw, int) and isinstance(expected_swarm, Enum) and sw == expected_swarm.value)
        status = "✅" if eff_ok and sw_ok else "❌"
        print(f"{status} \"{msg[:60]}\" → {d.effort.value} (swarm={sw}) [{d.reason}]")
