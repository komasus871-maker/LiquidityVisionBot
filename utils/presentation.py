from __future__ import annotations

STATUS_LABELS = {
    "WATCHING": "👀 Наблюдение", "TRIGGERED": "🔔 В зоне входа", "ACTIVE": "⚡ Активна",
    "TP1": "🎯 TP1", "TP2": "🏆 TP2", "TP3": "👑 TP3", "STOP": "🛑 Стоп",
    "BREAKEVEN": "🛡 Безубыток", "INVALIDATED": "⚠️ Инвалидирована", "EXPIRED": "⌛ Истекла",
}
CATEGORY_LABELS = {
    "READY_NOW": "🚀 Готово к входу", "PULLBACK": "🎯 Ожидание отката",
    "CONFIRMATION": "🔔 Нужно подтверждение", "REVERSAL": "🔄 Разворотный сценарий",
    "WATCHLIST": "👀 Наблюдение", "REGIME_CONFIRMATION": "🔔 Подтверждение режима",
    "REGIME_BLOCKED": "⛔ Заблокировано режимом",
}
ACTION_LABELS = {
    "HOLD": "Удерживать", "HOLD / MONITOR TP1": "Удерживать / следить за TP1",
    "PROTECT PROFIT": "Защитить прибыль", "MOVE STOP / PROTECT PROFIT": "Перенести стоп / защитить прибыль",
    "REDUCE RISK": "Снизить риск", "MONITOR INVALIDATION": "Следить за инвалидацией",
}
REASON_LABELS = {
    "Trend conflicts": "Тренд против сценария", "Structure conflicts": "Структура против сценария",
    "Momentum conflicts": "Импульс против сценария", "Displacement conflicts with direction": "Displacement против направления",
    "Opposing CHOCH": "Противоположный CHOCH", "Opposing BOS": "Противоположный BOS",
    "Opposing Order Block": "Противоположный Order Block", "Opposing Breaker Block": "Противоположный Breaker Block",
    "Opposing Mitigation Block": "Противоположный Mitigation Block", "No major blocker": "Критичных блокеров нет",
}

def status_label(value: object) -> str:
    text = str(value or "—")
    return STATUS_LABELS.get(text, text)

def category_label(value: object) -> str:
    text = str(value or "WATCHLIST")
    return CATEGORY_LABELS.get(text, text.replace("_", " ").title())

def action_label(value: object) -> str:
    text = str(value or "HOLD")
    return ACTION_LABELS.get(text, text.replace("_", " ").title())

def reason_label(value: object) -> str:
    text = str(value or "—")
    return REASON_LABELS.get(text, text)
