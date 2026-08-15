from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from database.database import connect


OWNER_TELEGRAM_ID = 7975010097


class OperatorCapability(StrEnum):
    OWNER = "OWNER"
    PLAN_ADMIN = "PLAN_ADMIN"
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    RESEARCH_ADMIN = "RESEARCH_ADMIN"
    AI_ADMIN = "AI_ADMIN"


ALL_OPERATOR_CAPABILITIES = frozenset(OperatorCapability)


def _ids(variable: str) -> set[int]:
    raw = os.getenv(variable, "")
    return {int(value.strip()) for value in raw.replace(";", ",").split(",")
            if value.strip().isdigit()}


class OperatorAuthorizationService:
    """Fail-closed Telegram operator authorization with immutable audit events."""

    ROLE_VARIABLES = {
        OperatorCapability.PLAN_ADMIN: "TELEGRAM_PLAN_ADMIN_USER_IDS",
        OperatorCapability.SYSTEM_ADMIN: "TELEGRAM_SYSTEM_ADMIN_USER_IDS",
        OperatorCapability.RESEARCH_ADMIN: "TELEGRAM_RESEARCH_ADMIN_USER_IDS",
        OperatorCapability.AI_ADMIN: "TELEGRAM_AI_ADMIN_USER_IDS",
    }

    def capabilities(self, telegram_id: int | None) -> frozenset[OperatorCapability]:
        if telegram_id is None:
            return frozenset()
        user_id = int(telegram_id)
        if user_id == OWNER_TELEGRAM_ID:
            return ALL_OPERATOR_CAPABILITIES
        general = (_ids("TELEGRAM_OPERATOR_USER_IDS") | _ids("ADMIN_IDS") |
                   _ids("ADMIN_ID"))
        capabilities: set[OperatorCapability] = set()
        if user_id in general:
            capabilities.update({OperatorCapability.PLAN_ADMIN, OperatorCapability.SYSTEM_ADMIN,
                                 OperatorCapability.RESEARCH_ADMIN, OperatorCapability.AI_ADMIN})
        for capability, variable in self.ROLE_VARIABLES.items():
            if user_id in _ids(variable):
                capabilities.add(capability)
        return frozenset(capabilities)

    def has(self, telegram_id: int | None, capability: OperatorCapability | str) -> bool:
        try:
            required = OperatorCapability(str(capability))
        except ValueError:
            return False
        return required in self.capabilities(telegram_id) or OperatorCapability.OWNER in self.capabilities(telegram_id)

    def audit(self, *, actor_telegram_id: int | None, capability: OperatorCapability | str,
              action: str, outcome: str, target_telegram_id: int | None = None,
              previous_state: dict[str, Any] | None = None,
              new_state: dict[str, Any] | None = None,
              metadata: dict[str, Any] | None = None) -> str:
        event_key = str(uuid.uuid4())
        with connect() as conn:
            conn.execute("""INSERT INTO operator_audit_events(event_key,actor_telegram_id,
                target_telegram_id,required_capability,action,outcome,previous_state_json,
                new_state_json,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (event_key, actor_telegram_id, target_telegram_id, str(capability), action,
                 outcome, json.dumps(previous_state or {}, sort_keys=True, default=str),
                 json.dumps(new_state or {}, sort_keys=True, default=str),
                 json.dumps(metadata or {}, sort_keys=True, default=str),
                 datetime.now(timezone.utc).isoformat()))
        return event_key

    def authorize(self, *, actor_telegram_id: int | None,
                  capability: OperatorCapability | str, action: str,
                  target_telegram_id: int | None = None,
                  metadata: dict[str, Any] | None = None) -> bool:
        allowed = self.has(actor_telegram_id, capability)
        self.audit(actor_telegram_id=actor_telegram_id, capability=capability, action=action,
                   outcome="AUTHORIZED" if allowed else "DENIED",
                   target_telegram_id=target_telegram_id, metadata=metadata)
        return allowed

