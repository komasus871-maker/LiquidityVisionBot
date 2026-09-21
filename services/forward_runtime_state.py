"""Shared operational state for the hosted forward collector.

Raw evidence is sealed locally and archived as immutable objects. This module
publishes only compact partition identity plus bounded current-state, gap and
health records so Telegram and PostgreSQL never scan or duplicate raw payloads.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from database.database import connect
FORWARD_PROGRAM_ID = "forward-microstructure-alpha-v1"


FROZEN_AT_UTC = "2026-09-20T10:42:36.5655344Z"
EXPECTED_CANDIDATE_IDS = (
    "a8ccc1b1ded41bef", "6e05b968e61e801f", "c119789e70cd968a",
    "062fe62e6c775320", "1af9004c5fb37aa9", "1bd7009a9c433bc7",
    "728d4faaacc69b80", "89e868a4531e3449", "f8926793803c30ca",
    "804922d97a11cce0",
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_identity() -> tuple[tuple[str, ...], str]:
    from services.forward_shadow_lab import frozen_forward_candidates

    candidate_ids = tuple(candidate.candidate_id for candidate in frozen_forward_candidates())
    if candidate_ids != EXPECTED_CANDIDATE_IDS:
        raise RuntimeError(
            "frozen forward candidate identity changed; collector startup is blocked"
        )
    digest = hashlib.sha256(canonical_json(candidate_ids).encode("utf-8")).hexdigest()
    return candidate_ids, digest


class ForwardRuntimeStateRepository:
    worker_name = "forward_microstructure_collector"

    def register_identity(self) -> str:
        candidate_ids, digest = candidate_identity()
        now = utc_now()
        with connect() as connection:
            existing = connection.execute(
                "SELECT candidate_identity_hash,candidate_ids_json,execution_authority "
                "FROM forward_experiment_identity WHERE program_id=?",
                (FORWARD_PROGRAM_ID,),
            ).fetchone()
            if existing:
                if (str(existing["candidate_identity_hash"]) != digest or
                        tuple(json.loads(existing["candidate_ids_json"])) != candidate_ids or
                        bool(existing["execution_authority"])):
                    raise RuntimeError("stored forward experiment identity conflicts with frozen source")
                return digest
            connection.execute(
                """INSERT INTO forward_experiment_identity(
                    program_id,candidate_identity_hash,candidate_ids_json,feature_schema,
                    execution_authority,frozen_at,registered_at
                ) VALUES(?,?,?,?,0,?,?)""",
                (FORWARD_PROGRAM_ID, digest, json.dumps(candidate_ids),
                 "forward-microstructure-feature-v1", FROZEN_AT_UTC, now),
            )
        return digest

    def publish_snapshot(self, snapshot: dict[str, Any], cross_venue: dict[str, Any]) -> None:
        bounded = dict(snapshot)
        bounded["cross_venue"] = dict(cross_venue)
        bounded["execution_authority"] = False
        observed = datetime.fromtimestamp(
            int(snapshot["receive_ts_ms"]) / 1000, tz=timezone.utc
        ).isoformat()
        updated = utc_now()
        with connect() as connection:
            connection.execute(
                """INSERT INTO forward_market_state(
                    venue,symbol,feature_schema,data_quality,market_state,snapshot_json,
                    observed_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(venue,symbol) DO UPDATE SET
                    feature_schema=excluded.feature_schema,
                    data_quality=excluded.data_quality,
                    market_state=excluded.market_state,
                    snapshot_json=excluded.snapshot_json,
                    observed_at=excluded.observed_at,
                    updated_at=excluded.updated_at""",
                (snapshot["venue"], snapshot["symbol"], snapshot["schema_version"],
                 snapshot["data_quality"]["status"], snapshot["market_state"],
                 canonical_json(bounded), observed, updated),
            )

    def heartbeat(
        self, *, instance_id: str, state: str, started_at: str,
        candidate_identity_hash: str, venues: dict[str, Any], storage: dict[str, Any],
        last_event_at: str | None = None, migration_boundary_at: str | None = None,
        last_error: str | None = None,
    ) -> None:
        now = utc_now()
        with connect() as connection:
            connection.execute(
                """INSERT INTO forward_worker_health(
                    worker_name,instance_id,state,started_at,heartbeat_at,last_event_at,
                    venues_json,storage_json,candidate_identity_hash,migration_boundary_at,
                    last_error,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(worker_name) DO UPDATE SET
                    instance_id=excluded.instance_id,state=excluded.state,
                    started_at=excluded.started_at,heartbeat_at=excluded.heartbeat_at,
                    last_event_at=excluded.last_event_at,venues_json=excluded.venues_json,
                    storage_json=excluded.storage_json,
                    candidate_identity_hash=excluded.candidate_identity_hash,
                    migration_boundary_at=excluded.migration_boundary_at,
                    last_error=excluded.last_error,updated_at=excluded.updated_at""",
                (self.worker_name, instance_id, state, started_at, now, last_event_at,
                 canonical_json(venues), canonical_json(storage), candidate_identity_hash,
                 migration_boundary_at, last_error, now),
            )

    def record_gap(
        self, *, venue: str, symbol: str, gap_start_at: str, gap_end_at: str,
        reason: str, details: dict[str, Any] | None = None,
    ) -> str:
        identity = {
            "venue": venue, "symbol": symbol, "gap_start_at": gap_start_at,
            "gap_end_at": gap_end_at, "reason": reason,
        }
        key = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
        with connect() as connection:
            connection.execute(
                """INSERT INTO forward_gap_events(
                    event_key,venue,symbol,gap_start_at,gap_end_at,reason,details_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING""",
                (key, venue, symbol, gap_start_at, gap_end_at, reason,
                 canonical_json(details or {}), utc_now()),
            )
        return key

    def register_partition(self, record: dict[str, Any]) -> None:
        """Publish compact searchable identity only; raw payloads stay in object storage."""
        now = utc_now()
        existing_error = str(record.get("last_error") or "") or None
        with connect() as connection:
            existing = connection.execute(
                "SELECT sha256,object_key FROM forward_partition_registry WHERE partition_id=?",
                (record["partition_id"],),
            ).fetchone()
            if existing and (
                str(existing["sha256"]) != str(record["sha256"])
                or str(existing["object_key"]) != str(record["object_key"])
            ):
                raise RuntimeError("partition registry identity collision")
            connection.execute(
                """INSERT INTO forward_partition_registry(
                    partition_id,schema_version,venue,symbol,event_types_json,
                    bucket_start_ts_ms,first_receive_ts_ms,last_receive_ts_ms,
                    first_exchange_ts_ms,last_exchange_ts_ms,event_count,byte_count,
                    sha256,object_key,manifest_object_key,upload_state,replay_available,retention_state,
                    incident_state,collector_version,program_identity,finalized_at,
                    remote_verified_at,local_state,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(partition_id) DO UPDATE SET
                    upload_state=excluded.upload_state,
                    replay_available=excluded.replay_available,
                    retention_state=excluded.retention_state,
                    incident_state=excluded.incident_state,
                    remote_verified_at=excluded.remote_verified_at,
                    local_state=excluded.local_state,updated_at=excluded.updated_at""",
                (
                    record["partition_id"], record["schema"], record["venue"], record["symbol"],
                    canonical_json(record.get("event_types") or []), int(record["bucket_start_ts_ms"]),
                    int(record["first_receive_ts_ms"]), int(record["last_receive_ts_ms"]),
                    record.get("first_exchange_ts_ms"), record.get("last_exchange_ts_ms"),
                    int(record["event_count"]), int(record["byte_count"]), record["sha256"],
                    record["object_key"], record["manifest_object_key"], record["state"],
                    int(record["state"] == "REMOTE_VERIFIED"),
                    record.get("retention_state") or "FROZEN_30_DAY",
                    "CHECKSUM_MISMATCH" if record["state"] == "INTEGRITY_INCIDENT" else existing_error,
                    record.get("collector_version"), record.get("program_identity"),
                    record["finalized_at_utc"], record.get("verified_at_utc"),
                    record.get("local_state") or "PRESENT", now,
                ),
            )

    def replay_partitions(
        self, *, start_ts_ms: int | None = None, end_ts_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["upload_state='REMOTE_VERIFIED'", "replay_available=1"]
        params: list[Any] = []
        if start_ts_ms is not None:
            clauses.append("last_receive_ts_ms>=?")
            params.append(int(start_ts_ms))
        if end_ts_ms is not None:
            clauses.append("first_receive_ts_ms<=?")
            params.append(int(end_ts_ms))
        with connect() as connection:
            rows = connection.execute(
                "SELECT * FROM forward_partition_registry WHERE " + " AND ".join(clauses)
                + " ORDER BY bucket_start_ts_ms,partition_id",
                tuple(params),
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            value["event_types"] = json.loads(value.pop("event_types_json"))
            result.append(value)
        return result

    def latest_states(self, symbols: Iterable[str] | None = None) -> list[dict[str, Any]]:
        normalized = tuple(dict.fromkeys(str(item).upper() for item in (symbols or ()) if item))
        sql = "SELECT * FROM forward_market_state"
        params: tuple[Any, ...] = ()
        if normalized:
            sql += " WHERE symbol IN (" + ",".join("?" for _ in normalized) + ")"
            params = normalized
        sql += " ORDER BY symbol,venue"
        with connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["snapshot"] = json.loads(item.pop("snapshot_json"))
            result.append(item)
        return result

    def health(self) -> dict[str, Any] | None:
        with connect() as connection:
            row = connection.execute(
                "SELECT * FROM forward_worker_health WHERE worker_name=?",
                (self.worker_name,),
            ).fetchone()
            gaps = connection.execute(
                "SELECT COUNT(*) AS n FROM forward_gap_events"
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["venues"] = json.loads(value.pop("venues_json"))
        value["storage"] = json.loads(value.pop("storage_json"))
        value["gap_count"] = int(gaps["n"] if gaps else 0)
        value["execution_authority"] = False
        return value
