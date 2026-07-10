import sqlite3


class Database:

    def __init__(

        self,

        path="history.db"

    ):

        self.connection = sqlite3.connect(

            path,

            check_same_thread=False

        )

        self.cursor = self.connection.cursor()

        self.create()

    def create(self):

        self.cursor.execute("""

        CREATE TABLE IF NOT EXISTS signals(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT,

            timeframe TEXT,

            trend TEXT,

            structure TEXT,

            bos TEXT,

            choch TEXT,

            breaker TEXT,

            mitigation TEXT,

            fvg TEXT,

            premium TEXT,

            volume TEXT,

            displacement TEXT,

            entry REAL,

            stop REAL,

            tp1 REAL,

            tp2 REAL,

            tp3 REAL,

            recommendation TEXT,

            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            result TEXT,

            rr REAL

        )

        """)

        self.connection.commit()

    def save(

        self,

        analysis

    ):

        self.cursor.execute("""

        INSERT INTO signals(

            symbol,

            timeframe,

            trend,

            structure,

            bos,

            choch,

            breaker,

            mitigation,

            fvg,

            premium,

            volume,

            displacement,

            entry,

            stop,

            tp1,

            tp2,

            tp3,

            recommendation

        )

        VALUES(

            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?

        )

        """,(

            analysis["symbol"],

            analysis["timeframe"],

            analysis["trend"],

            analysis["structure"],

            analysis["bos"],

            analysis["choch"],

            analysis["breaker"],

            analysis["mitigation"],

            analysis["fvg"],

            analysis["premium"]["zone"],

            analysis["volume"],

            analysis["displacement"],

            analysis["entry"],

            analysis["stop"],

            analysis["tp1"],

            analysis["tp2"],

            analysis["tp3"],

            analysis["recommendation"]

        ))

        self.connection.commit()

    def update(

        self,

        signal_id,

        result,

        rr

    ):

        self.cursor.execute("""

        UPDATE signals

        SET

            result=?,

            rr=?

        WHERE

            id=?

        """,(

            result,

            rr,

            signal_id

        ))

        self.connection.commit()

    def fetch_all(self):

        self.cursor.execute(

            "SELECT * FROM signals"

        )

        return self.cursor.fetchall()