from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from database.database import connect
from domain.intelligence import TradeDNABuilder


class TradeMemoryService:
    """Creates one deterministic post-trade lesson per closed signal."""

    WIN_RESULTS = {"TP1", "TP2", "TP3", "MANUAL_PROFIT"}

    @staticmethod
    def _lessons(signal: dict[str, Any], dna: dict[str, Any]) -> dict[str, Any]:
        result = str(signal.get("result") or signal.get("status") or "UNKNOWN")
        realized_r = float(signal.get("realized_r") or 0)
        mfe = float(signal.get("max_profit_pct") or 0)
        mae = abs(float(signal.get("max_drawdown_pct") or 0))
        strengths, weaknesses, worked, failed = [], [], [], []
        if dna.get("htf_alignment", "UNKNOWN") not in {"UNKNOWN", "NEUTRAL", "CONFLICT"}:
            strengths.append("Higher-timeframe context supported the setup")
        if dna.get("sweep", "UNKNOWN") != "UNKNOWN":
            strengths.append("Liquidity sweep evidence was present")
        if float(dna.get("entry_quality") or 0) >= 70:
            strengths.append("Entry quality was strong")
        if float(dna.get("risk_quality") or 0) >= 70:
            strengths.append("Risk geometry was efficient")
        if realized_r > 0:
            worked.append(f"The setup converted into {realized_r:+.2f}R")
        if mfe > 0:
            worked.append(f"Maximum favorable excursion reached {mfe:.2f}%")
        if realized_r <= 0:
            failed.append(f"The setup closed at {realized_r:+.2f}R")
        if mae > max(mfe, 0.01):
            weaknesses.append("Adverse excursion dominated favorable excursion")
        if float(dna.get("readiness") or 0) < 60:
            weaknesses.append("Execution readiness was low at creation")
        if float(dna.get("rr") or 0) < 1.5:
            weaknesses.append("Planned reward-to-risk was marginal")
        if mfe > 0 and realized_r <= 0:
            failed.append("Open profit was not converted into a positive close")
        if result in {"INVALIDATED", "EXPIRED", "INVALIDATED_BEFORE_ENTRY"}:
            lesson = "The idea did not become a valid live trade; preserve the context but do not treat it as execution evidence."
        elif realized_r > 0:
            lesson = "Keep the strongest confluences and compare future entries against this winning DNA pattern."
        elif mfe > mae:
            lesson = "The direction showed edge, but trade management or timing reduced the realized result."
        else:
            lesson = "Require stronger confirmation or better location when this DNA pattern appears again."
        return {
            "what_worked": worked or ["No validated positive edge was isolated"],
            "what_failed": failed or ["No major failure was isolated"],
            "strengths": strengths or ["No dominant strength recorded"],
            "weaknesses": weaknesses or ["No dominant weakness recorded"],
            "lesson": lesson,
            "result": result,
            "realized_r": realized_r,
            "mfe_pct": mfe,
            "mae_pct": mae,
        }

    def create_for_signal(self, signal_id: int) -> dict[str, Any] | None:
        with connect() as conn:
            signal = conn.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not signal or not signal.get("closed_at"):
                return None
            existing = conn.execute("SELECT * FROM trade_memories WHERE signal_id=?", (signal_id,)).fetchone()
            if existing:
                return dict(existing)
            signal_dict = dict(signal)
            dna = TradeDNABuilder.from_signal(signal_dict).to_dict()
            memory = self._lessons(signal_dict, dna)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO trade_memories(signal_id,dna_fingerprint,memory_json,lesson,result,realized_r,created_at)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(signal_id) DO NOTHING""",
                (signal_id, dna.get("fingerprint"), json.dumps(memory, ensure_ascii=False), memory["lesson"],
                 memory["result"], memory["realized_r"], now),
            )
            return memory

    def progress(self) -> dict[str, Any]:
        with connect() as conn:
            closed = int(conn.execute("SELECT COUNT(*) n FROM signals WHERE closed_at IS NOT NULL").fetchone()["n"] or 0)
            dna = int(conn.execute("SELECT COUNT(*) n FROM signals WHERE trade_dna_json IS NOT NULL AND trade_dna_json<>''").fetchone()["n"] or 0)
            memories = int(conn.execute("SELECT COUNT(*) n FROM trade_memories").fetchone()["n"] or 0)
        comparable = min(closed, memories)
        progress = min(100.0, comparable / 100 * 100)
        reliability = "High" if comparable >= 100 else "Moderate" if comparable >= 30 else "Low" if comparable >= 10 else "Insufficient"
        return {"dna_library": dna, "closed_trades": closed, "similar_trades": comparable,
                "learning_progress": round(progress, 1), "reliability": reliability}
