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
                SELECT symbol, timeframe, created_at
                FROM user_watchlist
                WHERE telegram_id=?
                ORDER BY created_at DESC
                """,
                (telegram_id,),
            ).fetchall()
