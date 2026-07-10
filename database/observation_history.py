from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import connect


class ObservationHistory:
    def save_or_update(self, *, owner_telegram_id: int | None, notification_chat_id: int | None, symbol: str, timeframe: str, analysis: dict[str, Any], setup_key: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        features = {k: analysis.get(k) for k in (
            "trend", "structure", "bos", "choch", "liquidity", "sweep", "order_block", "breaker",
            "mitigation", "fvg", "premium", "volume", "displacement", "rsi", "macd", "ema50", "ema200",
            "reasons", "triggers", "alternative_conditions"
        )}
        with connect() as conn:
            row = conn.execute(
                """SELECT id FROM analysis_observations
                   WHERE COALESCE(owner_telegram_id,0)=COALESCE(?,0) AND symbol=? AND timeframe=?
                   ORDER BY id DESC LIMIT 1""",
                (owner_telegram_id, symbol.upper(), timeframe),
            ).fetchone()
            values = (notification_chat_id, analysis.get("direction","LONG"), analysis.get("market_bias",""),
                      analysis.get("execution_status",""), analysis.get("recommendation",""),
                      float(analysis.get("direction_score",0)), float(analysis.get("entry_quality",0)),
                      float(analysis.get("risk_quality",0)), float(analysis.get("execution_readiness",0)),
                      float(analysis.get("directional_edge",0)), float(analysis.get("price",0)),
                      analysis.get("preferred_entry_low"), analysis.get("preferred_entry_high"), setup_key,
                      json.dumps(features, ensure_ascii=False), now)
            if row:
                conn.execute(
                    """UPDATE analysis_observations SET notification_chat_id=?, direction=?, market_bias=?,
                       execution_status=?, recommendation=?, direction_score=?, entry_quality=?, risk_quality=?,
                       readiness=?, directional_edge=?, price=?, preferred_entry_low=?, preferred_entry_high=?,
                       setup_key=?, features_json=?, updated_at=? WHERE id=?""",
                    (*values, row[0]),
                )
                return int(row[0])
            cur = conn.execute(
                """INSERT INTO analysis_observations(owner_telegram_id,notification_chat_id,symbol,timeframe,direction,
                   market_bias,execution_status,recommendation,direction_score,entry_quality,risk_quality,readiness,
                   directional_edge,price,preferred_entry_low,preferred_entry_high,setup_key,features_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (owner_telegram_id, notification_chat_id, symbol.upper(), timeframe, analysis.get("direction","LONG"),
                 analysis.get("market_bias",""), analysis.get("execution_status",""), analysis.get("recommendation",""),
                 float(analysis.get("direction_score",0)), float(analysis.get("entry_quality",0)),
                 float(analysis.get("risk_quality",0)), float(analysis.get("execution_readiness",0)),
                 float(analysis.get("directional_edge",0)), float(analysis.get("price",0)),
                 analysis.get("preferred_entry_low"), analysis.get("preferred_entry_high"), setup_key,
                 json.dumps(features, ensure_ascii=False), now, now),
            )
            return int(cur.lastrowid)

    def promote(self, observation_id: int, signal_id: int) -> None:
        with connect() as conn:
            conn.execute("UPDATE analysis_observations SET promoted_signal_id=?, updated_at=? WHERE id=?",
                         (signal_id, datetime.now(timezone.utc).isoformat(), observation_id))

    def recent(self, owner_telegram_id: int, limit: int = 8):
        with connect() as conn:
            rows = conn.execute("SELECT * FROM analysis_observations WHERE owner_telegram_id=? ORDER BY updated_at DESC LIMIT ?",
                                (owner_telegram_id, limit)).fetchall()
        return [dict(x) for x in rows]

    def count(self, owner_telegram_id: int) -> int:
        with connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM analysis_observations WHERE owner_telegram_id=?", (owner_telegram_id,)).fetchone()
        return int(row[0] or 0)
