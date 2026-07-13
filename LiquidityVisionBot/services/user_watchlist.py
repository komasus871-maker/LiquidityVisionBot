from database.database import connect


class UserWatchlist:
    def add(self, telegram_id: int, symbol: str, timeframe: str = "1h") -> bool:
        with connect() as conn:
            cur = conn.execute(
                """INSERT INTO user_watchlist(telegram_id, symbol, timeframe)
                   VALUES(?,?,?) ON CONFLICT(telegram_id, symbol, timeframe) DO NOTHING""",
                (telegram_id, symbol.upper(), timeframe),
            )
            return cur.rowcount > 0

    def remove(self, telegram_id: int, symbol: str, timeframe: str = "1h") -> bool:
        with connect() as conn:
            cur = conn.execute(
                "DELETE FROM user_watchlist WHERE telegram_id=? AND symbol=? AND timeframe=?",
                (telegram_id, symbol.upper(), timeframe),
            )
            conn.execute(
                "DELETE FROM watch_states WHERE telegram_id=? AND symbol=? AND timeframe=?",
                (telegram_id, symbol.upper(), timeframe),
            )
            return cur.rowcount > 0

    def list(self, telegram_id: int):
        with connect() as conn:
            return conn.execute(
                """SELECT w.symbol,w.timeframe,w.created_at,s.updated_at,s.last_checked_at,s.last_error,
                          s.consecutive_errors,s.snapshot_json,s.promoted_signal_id
                   FROM user_watchlist w
                   LEFT JOIN watch_states s
                     ON s.telegram_id=w.telegram_id AND s.symbol=w.symbol AND s.timeframe=w.timeframe
                   WHERE w.telegram_id=? ORDER BY w.created_at DESC""",
                (telegram_id,),
            ).fetchall()
