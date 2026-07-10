from database.database import connect


class UserWatchlist:
    def add(self, telegram_id: int, symbol: str, timeframe: str = "1h") -> bool:
        with connect() as conn:
            before = conn.total_changes
            conn.execute(
                """
                INSERT OR IGNORE INTO user_watchlist(telegram_id, symbol, timeframe)
                VALUES(?,?,?)
                """,
                (telegram_id, symbol.upper(), timeframe),
            )
            return conn.total_changes > before

    def remove(self, telegram_id: int, symbol: str, timeframe: str = "1h") -> bool:
        with connect() as conn:
            cur = conn.execute(
                "DELETE FROM user_watchlist WHERE telegram_id=? AND symbol=? AND timeframe=?",
                (telegram_id, symbol.upper(), timeframe),
            )
            return cur.rowcount > 0

    def list(self, telegram_id: int):
        with connect() as conn:
            return conn.execute(
                """
                SELECT w.symbol, w.timeframe, w.created_at,
                       s.snapshot_json, s.updated_at, s.last_notified_at
                FROM user_watchlist w
                LEFT JOIN watch_states s
                  ON s.telegram_id=w.telegram_id AND s.symbol=w.symbol AND s.timeframe=w.timeframe
                WHERE w.telegram_id=?
                ORDER BY w.created_at DESC
                """,
                (telegram_id,),
            ).fetchall()

    def clear_state(self, telegram_id: int, symbol: str, timeframe: str = "1h") -> None:
        with connect() as conn:
            conn.execute(
                "DELETE FROM watch_states WHERE telegram_id=? AND symbol=? AND timeframe=?",
                (telegram_id, symbol.upper(), timeframe),
            )
