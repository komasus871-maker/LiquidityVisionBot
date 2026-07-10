import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from database.database import DATABASE_NAME


class SignalHistory:
    """SQLite repository for generated signals and their lifecycle."""

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DATABASE_NAME)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, signal: dict[str, Any]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO signals (
                    symbol, timeframe, side, status, created_at, updated_at,
                    entry, stop, tp1, tp2, tp3, rr, confidence,
                    bull_score, bear_score, recommendation, setup_key,
                    features_json, reasons_json, max_profit_pct,
                    max_drawdown_pct
                ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    signal["symbol"], signal["timeframe"], signal["side"],
                    now, now, signal["entry"], signal["stop"],
                    signal["tp1"], signal["tp2"], signal["tp3"],
                    signal["rr"], signal["confidence"], signal["bull_score"],
                    signal["bear_score"], signal["recommendation"],
                    signal["setup_key"], json.dumps(signal["features"], ensure_ascii=False),
                    json.dumps(signal["reasons"], ensure_ascii=False),
                ),
            )
            return int(cursor.lastrowid)

    def get_open(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM signals WHERE status = 'OPEN' ORDER BY created_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_progress(
        self,
        signal_id: int,
        *,
        status: str,
        current_price: float,
        max_profit_pct: float,
        max_drawdown_pct: float,
        tp1_hit_at: str | None = None,
        tp2_hit_at: str | None = None,
        tp3_hit_at: str | None = None,
        stop_hit_at: str | None = None,
        closed_at: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE signals SET
                    status = ?, current_price = ?, updated_at = ?,
                    max_profit_pct = ?, max_drawdown_pct = ?,
                    tp1_hit_at = COALESCE(tp1_hit_at, ?),
                    tp2_hit_at = COALESCE(tp2_hit_at, ?),
                    tp3_hit_at = COALESCE(tp3_hit_at, ?),
                    stop_hit_at = COALESCE(stop_hit_at, ?),
                    closed_at = COALESCE(closed_at, ?)
                WHERE id = ?
                """,
                (
                    status, current_price, now, max_profit_pct,
                    max_drawdown_pct, tp1_hit_at, tp2_hit_at, tp3_hit_at,
                    stop_hit_at, closed_at, signal_id,
                ),
            )

    def get_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'OPEN' THEN 1 ELSE 0 END) AS open_count,
                    SUM(CASE WHEN tp1_hit_at IS NOT NULL THEN 1 ELSE 0 END) AS tp1_hits,
                    SUM(CASE WHEN tp2_hit_at IS NOT NULL THEN 1 ELSE 0 END) AS tp2_hits,
                    SUM(CASE WHEN tp3_hit_at IS NOT NULL THEN 1 ELSE 0 END) AS tp3_hits,
                    SUM(CASE WHEN stop_hit_at IS NOT NULL THEN 1 ELSE 0 END) AS stop_hits,
                    AVG(CASE WHEN closed_at IS NOT NULL THEN max_profit_pct END) AS avg_mfe,
                    AVG(CASE WHEN closed_at IS NOT NULL THEN max_drawdown_pct END) AS avg_mae
                FROM signals
                """
            ).fetchone()
        data = dict(row)
        total = data["total"] or 0
        data["tp1_rate"] = round((data["tp1_hits"] or 0) / total * 100, 2) if total else 0
        return data
