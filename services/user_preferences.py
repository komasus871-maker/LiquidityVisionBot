from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import connect


DEFAULTS = {
    "preferred_symbols": [], "preferred_timeframes": [], "preferred_strategies": [],
    "alert_verbosity": "COMPACT", "notification_categories": ["TRADE_LIFECYCLE"],
    "language": "en", "output_mode": "COMPACT", "risk_presentation": "R",
}


class UserPreferenceService:
    def get(self, telegram_id: int) -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute("SELECT * FROM user_preferences WHERE telegram_id=?", (telegram_id,)).fetchone()
        if not row:
            return dict(DEFAULTS)
        data = dict(row)
        for target, column in (("preferred_symbols", "preferred_symbols_json"),
                               ("preferred_timeframes", "preferred_timeframes_json"),
                               ("preferred_strategies", "preferred_strategies_json"),
                               ("notification_categories", "notification_categories_json")):
            try:
                data[target] = json.loads(data.get(column) or "[]")
            except (TypeError, ValueError):
                data[target] = []
        return {**DEFAULTS, **data}

    def update(self, telegram_id: int, **changes: Any) -> dict[str, Any]:
        current = self.get(telegram_id)
        allowed = set(DEFAULTS)
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported preferences: {', '.join(sorted(unknown))}")
        current.update(changes)
        now = datetime.now(timezone.utc).isoformat()
        with connect() as conn:
            conn.execute("""INSERT INTO user_preferences(telegram_id,preferred_symbols_json,
                preferred_timeframes_json,preferred_strategies_json,alert_verbosity,
                notification_categories_json,language,output_mode,risk_presentation,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET
                preferred_symbols_json=excluded.preferred_symbols_json,
                preferred_timeframes_json=excluded.preferred_timeframes_json,
                preferred_strategies_json=excluded.preferred_strategies_json,
                alert_verbosity=excluded.alert_verbosity,
                notification_categories_json=excluded.notification_categories_json,
                language=excluded.language,output_mode=excluded.output_mode,
                risk_presentation=excluded.risk_presentation,updated_at=excluded.updated_at""",
                (telegram_id, json.dumps(current["preferred_symbols"]),
                 json.dumps(current["preferred_timeframes"]), json.dumps(current["preferred_strategies"]),
                 current["alert_verbosity"], json.dumps(current["notification_categories"]),
                 current["language"], current["output_mode"], current["risk_presentation"], now))
        return self.get(telegram_id)

