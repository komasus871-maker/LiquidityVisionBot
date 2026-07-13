from __future__ import annotations


class ScenarioEngine:
    """Builds deterministic primary/alternative market paths from analysis output."""

    @staticmethod
    def build(data: dict) -> dict:
        side = str(data.get("direction") or "LONG")
        opposite = "SHORT" if side == "LONG" else "LONG"
        status = str(data.get("execution_status") or "")
        triggers = list(data.get("triggers") or [])
        alternatives = list(data.get("alternative_conditions") or [])
        zone = f"{data.get('preferred_entry_low')}–{data.get('preferred_entry_high')}"
        if "PULLBACK" in status:
            primary = [f"Price retraces toward preferred zone ({zone})", "Reaction candle confirms direction", f"{side} BOS/CHOCH or displacement confirms", "Expansion toward TP1"]
        elif "TRIGGER" in status:
            primary = ["Current context remains valid", triggers[0] if triggers else f"Wait for {side} confirmation", "Execution becomes READY", "Expansion toward TP1"]
        elif "READY" in status:
            primary = ["Current setup remains valid", "Entry holds above/below invalidation", "TP1 becomes first objective"]
        else:
            primary = ["Observe current structure", triggers[0] if triggers else "Wait for independent confirmation", "Re-evaluate execution quality"]
        alternative = alternatives[:3] or [f"Opposing {opposite} BOS/CHOCH", "Reaction from opposing dealing-range zone", f"{opposite} displacement with volume"]
        return {"primary": primary, "alternative": alternative}
