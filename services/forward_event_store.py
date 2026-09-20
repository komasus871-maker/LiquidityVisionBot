"""Append-only storage primitives for the forward microstructure research lab."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import zlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "forward-microstructure-event-v2"
RAW_ENCODING = "ZLIB_JSON_UTF8"


class Venue(str, Enum):
    BINANCE = "BINANCE"
    OKX = "OKX"
    BINGX = "BINGX"


class EventType(str, Enum):
    TRADE = "TRADE"
    BOOK_SNAPSHOT = "BOOK_SNAPSHOT"
    BOOK_DELTA = "BOOK_DELTA"
    LIQUIDATION = "LIQUIDATION"
    OPEN_INTEREST = "OPEN_INTEREST"
    FUNDING = "FUNDING"
    MARK_PRICE = "MARK_PRICE"
    INDEX_PRICE = "INDEX_PRICE"
    HEALTH = "HEALTH"


class IntegrityStatus(str, Enum):
    VALID = "VALID"
    DUPLICATE = "DUPLICATE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    GAPPED = "GAPPED"
    STALE = "STALE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def encode_raw_payload(value: Any) -> bytes:
    return zlib.compress(canonical_json(value).encode("utf-8"), level=6)


def decode_raw_payload(value: str | bytes, encoding: str) -> Any:
    if encoding == RAW_ENCODING:
        return json.loads(zlib.decompress(bytes(value)).decode("utf-8"))
    return json.loads(value)


@dataclass(frozen=True)
class RawMarketEvent:
    venue: Venue
    market: str
    symbol: str
    instrument_type: str
    event_type: EventType
    exchange_ts_ms: int
    receive_ts_ms: int
    payload: dict[str, Any]
    sequence_start: int | None = None
    sequence_end: int | None = None
    previous_sequence: int | None = None
    price: float | None = None
    quantity: float | None = None
    side: str | None = None
    is_buyer_maker: bool | None = None
    connection_id: str | None = None
    integrity_status: IntegrityStatus = IntegrityStatus.VALID
    schema_version: str = SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.exchange_ts_ms < 0 or self.receive_ts_ms <= 0:
            raise ValueError("event timestamps must be nonnegative milliseconds")
        if self.receive_ts_ms + 60_000 < self.exchange_ts_ms:
            raise ValueError("receive time cannot materially precede exchange time")
        if self.side not in {None, "BUY", "SELL"}:
            raise ValueError("side must be BUY, SELL, or absent")
        if self.price is not None and self.price <= 0:
            raise ValueError("price must be positive")
        if self.quantity is not None and self.quantity < 0:
            raise ValueError("quantity cannot be negative")

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.payload).encode("utf-8")).hexdigest()

    @property
    def semantic_id(self) -> str:
        identity = {
            "venue": self.venue.value,
            "market": self.market,
            "symbol": self.symbol,
            "event_type": self.event_type.value,
            "exchange_ts_ms": self.exchange_ts_ms,
            "sequence_start": self.sequence_start,
            "sequence_end": self.sequence_end,
            "payload_sha256": self.payload_sha256,
        }
        return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()

    def record(self) -> dict[str, Any]:
        value = asdict(self)
        value["venue"] = self.venue.value
        value["event_type"] = self.event_type.value
        value["integrity_status"] = self.integrity_status.value
        value["payload_sha256"] = self.payload_sha256
        value["semantic_id"] = self.semantic_id
        return value


class AppendOnlyEventStore:
    """Durable SQLite ledger whose research tables reject UPDATE and DELETE."""

    def __init__(self, path: str | Path, *, raw_partition_root: str | Path | None = None,
                 minimum_free_bytes: int = 10 * 1024**3):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.raw_partition_root = Path(raw_partition_root) if raw_partition_root else None
        self.raw_ledger = None
        if self.raw_partition_root is not None:
            from services.forward_partition_store import PartitionedRawLedger
            self.raw_ledger = PartitionedRawLedger(
                self.raw_partition_root, minimum_free_bytes=minimum_free_bytes,
            )
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS raw_events (
                    ingest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    semantic_id TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    market TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    instrument_type TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    exchange_ts_ms INTEGER NOT NULL,
                    receive_ts_ms INTEGER NOT NULL,
                    sequence_start INTEGER,
                    sequence_end INTEGER,
                    previous_sequence INTEGER,
                    price REAL,
                    quantity REAL,
                    side TEXT,
                    is_buyer_maker INTEGER,
                    payload_sha256 TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    raw_encoding TEXT NOT NULL DEFAULT 'JSON_UTF8',
                    connection_id TEXT,
                    integrity_status TEXT NOT NULL,
                    duplicate_of INTEGER,
                    schema_version TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    FOREIGN KEY(duplicate_of) REFERENCES raw_events(ingest_id)
                );
                CREATE INDEX IF NOT EXISTS idx_raw_time ON raw_events(venue,symbol,event_type,exchange_ts_ms,ingest_id);
                CREATE INDEX IF NOT EXISTS idx_raw_semantic ON raw_events(semantic_id,ingest_id);
                CREATE TABLE IF NOT EXISTS feature_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    feature_ts_ms INTEGER NOT NULL,
                    receive_ts_ms INTEGER NOT NULL,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    data_quality TEXT NOT NULL,
                    feature_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_feature_time ON feature_snapshots(venue,symbol,feature_ts_ms);
                CREATE TABLE IF NOT EXISTS shadow_decisions (
                    decision_id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    family TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    decision_ts_ms INTEGER NOT NULL,
                    first_evidence_ts_ms INTEGER NOT NULL,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    feature_snapshot_id TEXT NOT NULL,
                    execution_authority INTEGER NOT NULL DEFAULT 0,
                    decision_json TEXT NOT NULL,
                    FOREIGN KEY(feature_snapshot_id) REFERENCES feature_snapshots(snapshot_id)
                );
                CREATE INDEX IF NOT EXISTS idx_decision_time ON shadow_decisions(candidate_id,decision_ts_ms);
                CREATE TABLE IF NOT EXISTS outcome_labels (
                    label_id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    horizon_ms INTEGER NOT NULL,
                    observed_ts_ms INTEGER NOT NULL,
                    label_json TEXT NOT NULL,
                    FOREIGN KEY(decision_id) REFERENCES shadow_decisions(decision_id)
                );
                CREATE TABLE IF NOT EXISTS collector_checkpoints (
                    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_ts_ms INTEGER NOT NULL,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    connection_id TEXT,
                    last_sequence INTEGER,
                    state TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoint_latest ON collector_checkpoints(venue,symbol,recorded_ts_ms DESC);
                CREATE TABLE IF NOT EXISTS gap_records (
                    gap_id TEXT PRIMARY KEY,
                    venue TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    feed TEXT NOT NULL,
                    start_ts_ms INTEGER NOT NULL,
                    end_ts_ms INTEGER,
                    reason TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    replay_usable INTEGER NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gap_time ON gap_records(venue,symbol,start_ts_ms);
                CREATE TABLE IF NOT EXISTS portfolio_shadow_events (
                    portfolio_event_id TEXT PRIMARY KEY,
                    event_ts_ms INTEGER NOT NULL,
                    policy_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL
                );
            """)
            raw_columns = {row[1] for row in connection.execute("PRAGMA table_info(raw_events)")}
            if "raw_encoding" not in raw_columns:
                connection.execute(
                    "ALTER TABLE raw_events ADD COLUMN raw_encoding TEXT NOT NULL DEFAULT 'JSON_UTF8'"
                )
            for table in ("raw_events", "feature_snapshots", "shadow_decisions", "outcome_labels",
                          "collector_checkpoints", "gap_records", "portfolio_shadow_events"):
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_update
                    BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END""")
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_delete
                    BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY'); END""")

    def append(self, event: RawMarketEvent) -> dict[str, Any]:
        if self.raw_ledger is not None:
            return self.raw_ledger.append(event)
        return self.append_many((event,))[0]

    def append_many(self, events: Iterable[RawMarketEvent]) -> list[dict[str, Any]]:
        if self.raw_ledger is not None:
            return [self.raw_ledger.append(event) for event in events]
        receipts: list[dict[str, Any]] = []
        with self._lock, self._connect() as connection:
            for event in events:
                record = event.record()
                duplicate = connection.execute(
                    "SELECT ingest_id FROM raw_events WHERE semantic_id=? ORDER BY ingest_id LIMIT 1",
                    (record["semantic_id"],),
                ).fetchone()
                cursor = connection.execute("""INSERT INTO raw_events(
                    semantic_id,venue,market,symbol,instrument_type,event_type,
                    exchange_ts_ms,receive_ts_ms,sequence_start,sequence_end,previous_sequence,
                    price,quantity,side,is_buyer_maker,payload_sha256,raw_json,raw_encoding,connection_id,
                    integrity_status,duplicate_of,schema_version,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    record["semantic_id"], record["venue"], event.market, event.symbol,
                    event.instrument_type, record["event_type"], event.exchange_ts_ms,
                    event.receive_ts_ms, event.sequence_start, event.sequence_end,
                    event.previous_sequence, event.price, event.quantity, event.side,
                    None if event.is_buyer_maker is None else int(event.is_buyer_maker),
                    record["payload_sha256"], sqlite3.Binary(encode_raw_payload(event.payload)),
                    RAW_ENCODING, event.connection_id,
                    IntegrityStatus.DUPLICATE.value if duplicate else record["integrity_status"],
                    duplicate["ingest_id"] if duplicate else None, event.schema_version,
                    canonical_json(event.metadata),
                ))
                receipts.append({
                    "ingest_id": cursor.lastrowid,
                    "semantic_id": record["semantic_id"],
                    "duplicate": duplicate is not None,
                    "duplicate_of": duplicate["ingest_id"] if duplicate else None,
                })
        return receipts

    def append_feature(self, snapshot: dict[str, Any]) -> str:
        payload = canonical_json(snapshot)
        snapshot_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            try:
                connection.execute("""INSERT INTO feature_snapshots(
                    snapshot_id,feature_ts_ms,receive_ts_ms,venue,symbol,schema_version,data_quality,feature_json
                ) VALUES(?,?,?,?,?,?,?,?)""", (
                    snapshot_id, int(snapshot["timestamp_ms"]), int(snapshot["receive_ts_ms"]),
                    snapshot["venue"], snapshot["symbol"], snapshot["schema_version"],
                    snapshot["data_quality"]["status"], payload,
                ))
            except sqlite3.IntegrityError:
                pass
        return snapshot_id

    def append_shadow_decision(self, decision: dict[str, Any]) -> str:
        if decision.get("execution_authority"):
            raise ValueError("forward lab decisions must never have execution authority")
        payload = canonical_json(decision)
        decision_id = decision.get("decision_id") or hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            try:
                connection.execute("""INSERT INTO shadow_decisions(
                    decision_id,candidate_id,family,direction,decision_ts_ms,first_evidence_ts_ms,
                    venue,symbol,feature_snapshot_id,execution_authority,decision_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
                    decision_id, decision["candidate_id"], decision["family"], decision["direction"],
                    int(decision["decision_ts_ms"]), int(decision["first_evidence_ts_ms"]),
                    decision["venue"], decision["symbol"], decision["feature_snapshot_id"], 0, payload,
                ))
            except sqlite3.IntegrityError:
                pass
        return decision_id

    def append_label(self, label: dict[str, Any]) -> str:
        payload = canonical_json(label)
        label_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO outcome_labels(label_id,decision_id,horizon_ms,observed_ts_ms,label_json) VALUES(?,?,?,?,?)",
                    (label_id, label["decision_id"], int(label["horizon_ms"]), int(label["observed_ts_ms"]), payload),
                )
            except sqlite3.IntegrityError:
                pass
        return label_id

    def unresolved_shadow_decisions(self, expected_horizons: Iterable[int]) -> list[dict[str, Any]]:
        expected = {int(value) for value in expected_horizons}
        with self._connect() as connection:
            decisions = connection.execute(
                "SELECT decision_id,decision_json FROM shadow_decisions ORDER BY decision_ts_ms,decision_id"
            ).fetchall()
            completed_rows = connection.execute(
                "SELECT decision_id,horizon_ms FROM outcome_labels"
            ).fetchall()
        completed: dict[str, set[int]] = {}
        for row in completed_rows:
            completed.setdefault(str(row["decision_id"]), set()).add(int(row["horizon_ms"]))
        return [{
            "decision": json.loads(row["decision_json"]),
            "missing_horizons": sorted(expected - completed.get(str(row["decision_id"]), set())),
        } for row in decisions if expected - completed.get(str(row["decision_id"]), set())]

    def checkpoint(self, *, recorded_ts_ms: int, venue: str, symbol: str, connection_id: str | None,
                   last_sequence: int | None, state: str, details: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("""INSERT INTO collector_checkpoints(
                recorded_ts_ms,venue,symbol,connection_id,last_sequence,state,details_json
            ) VALUES(?,?,?,?,?,?,?)""", (
                recorded_ts_ms, venue, symbol, connection_id, last_sequence, state, canonical_json(details),
            ))

    def append_gap(self, *, venue: str, symbol: str, feed: str, start_ts_ms: int,
                   reason: str, severity: str = "HIGH", replay_usable: bool = False,
                   end_ts_ms: int | None = None, details: dict[str, Any] | None = None) -> str:
        identity = {
            "venue": venue, "symbol": symbol, "feed": feed, "start_ts_ms": int(start_ts_ms),
            "end_ts_ms": end_ts_ms, "reason": reason, "severity": severity,
        }
        gap_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        with self._connect() as connection:
            try:
                connection.execute("""INSERT INTO gap_records(
                    gap_id,venue,symbol,feed,start_ts_ms,end_ts_ms,reason,severity,replay_usable,details_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                    gap_id, venue, symbol, feed, int(start_ts_ms), end_ts_ms, reason, severity,
                    int(replay_usable), canonical_json(details or {}),
                ))
            except sqlite3.IntegrityError:
                pass
        return gap_id

    def append_portfolio_event(self, event: dict[str, Any]) -> str:
        payload = canonical_json(event)
        event_id = event.get("portfolio_event_id") or hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            try:
                connection.execute("""INSERT INTO portfolio_shadow_events(
                    portfolio_event_id,event_ts_ms,policy_id,event_type,event_json
                ) VALUES(?,?,?,?,?)""", (
                    event_id, int(event["event_ts_ms"]), event["policy_id"],
                    event["event_type"], payload,
                ))
            except sqlite3.IntegrityError:
                pass
        return event_id

    def flush(self) -> None:
        if self.raw_ledger is not None:
            for segment in self.raw_ledger._segments.values():
                segment.handle.flush()
                os.fsync(segment.handle.fileno())
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(FULL)")

    def close(self) -> None:
        if self.raw_ledger is not None:
            self.raw_ledger.close()
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def latest_checkpoint(self, venue: str, symbol: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("""SELECT * FROM collector_checkpoints
                WHERE venue=? AND symbol=? ORDER BY recorded_ts_ms DESC,checkpoint_id DESC LIMIT 1""",
                (venue, symbol)).fetchone()
        return dict(row) if row else None

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            counts = {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("raw_events", "feature_snapshots", "shadow_decisions", "outcome_labels",
                              "collector_checkpoints", "gap_records", "portfolio_shadow_events")
            }
        if self.raw_ledger is not None:
            counts["raw_events_partitioned_session"] = self.raw_ledger.event_count
        return counts

    def quality_summary(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT venue,symbol,event_type,integrity_status,COUNT(*) count,
                MAX(receive_ts_ms) last_receive_ts_ms,MAX(exchange_ts_ms) last_exchange_ts_ms
                FROM raw_events GROUP BY venue,symbol,event_type,integrity_status
                ORDER BY venue,symbol,event_type,integrity_status""").fetchall()
        return [dict(row) for row in rows]

    def data_quality_dashboard(self, *, observed_ts_ms: int | None = None) -> dict[str, Any]:
        now = int(observed_ts_ms if observed_ts_ms is not None else time.time_ns() // 1_000_000)
        with self._connect() as connection:
            event_rows = connection.execute("""SELECT venue,symbol,event_type,
                MIN(receive_ts_ms) first_receive_ts_ms,MAX(receive_ts_ms) last_receive_ts_ms,
                COUNT(*) event_count,
                SUM(CASE WHEN integrity_status='GAPPED' THEN 1 ELSE 0 END) gap_events,
                SUM(CASE WHEN integrity_status='OUT_OF_ORDER' THEN 1 ELSE 0 END) out_of_order_events,
                SUM(CASE WHEN integrity_status='DUPLICATE' THEN 1 ELSE 0 END) duplicate_events
                FROM raw_events GROUP BY venue,symbol,event_type
                ORDER BY venue,symbol,event_type""").fetchall()
            checkpoint_rows = connection.execute("""SELECT c.* FROM collector_checkpoints c
                JOIN (SELECT venue,symbol,MAX(checkpoint_id) checkpoint_id
                      FROM collector_checkpoints GROUP BY venue,symbol) latest
                ON c.checkpoint_id=latest.checkpoint_id""").fetchall()
        instruments: dict[str, dict[str, Any]] = {}
        for row in event_rows:
            key = f"{row['venue']}:{row['symbol']}"
            state = instruments.setdefault(key, {
                "venue": row["venue"], "symbol": row["symbol"], "feeds": {},
                "first_receive_ts_ms": int(row["first_receive_ts_ms"]),
                "last_receive_ts_ms": int(row["last_receive_ts_ms"]),
                "gap_events": 0, "out_of_order_events": 0, "duplicate_events": 0,
            })
            state["first_receive_ts_ms"] = min(state["first_receive_ts_ms"], int(row["first_receive_ts_ms"]))
            state["last_receive_ts_ms"] = max(state["last_receive_ts_ms"], int(row["last_receive_ts_ms"]))
            state["gap_events"] += int(row["gap_events"] or 0)
            state["out_of_order_events"] += int(row["out_of_order_events"] or 0)
            state["duplicate_events"] += int(row["duplicate_events"] or 0)
            state["feeds"][row["event_type"]] = {
                "events": int(row["event_count"]),
                "last_receive_ts_ms": int(row["last_receive_ts_ms"]),
                "age_ms": max(0, now - int(row["last_receive_ts_ms"])),
            }
        for row in checkpoint_rows:
            key = f"{row['venue']}:{row['symbol']}"
            if key in instruments:
                instruments[key]["book_sequence_status"] = row["state"]
                instruments[key]["last_sequence"] = row["last_sequence"]
                instruments[key]["last_checkpoint_ts_ms"] = int(row["recorded_ts_ms"])
        for state in instruments.values():
            state["observed_uptime_ms"] = state["last_receive_ts_ms"] - state["first_receive_ts_ms"]
            state.setdefault("book_sequence_status", "NO_CHECKPOINT")
        return {
            "as_of_ts_ms": now, "instrument_count": len(instruments),
            "instruments": dict(sorted(instruments.items())), "execution_authority": False,
        }
