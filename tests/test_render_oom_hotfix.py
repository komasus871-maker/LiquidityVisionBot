from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys


HEAVY_WEB_MODULES = {
    "services.research_worker",
    "services.microstructure_observer",
    "services.pump_dump_monitor",
    "services.forward_public_collectors",
    "services.forward_event_store",
    "services.forward_shadow_lab",
    "services.live_reconciliation_worker",
}


def test_webhook_import_is_lightweight_and_registers_telegram_routes() -> None:
    script = """
import json, sys
import bot
print(json.dumps({
    'background_enabled': bot.web_background_jobs_enabled(),
    'routers': len(bot.build_dispatcher().sub_routers),
    'heavy_loaded': sorted(name for name in %r if name in sys.modules),
}))
""" % HEAVY_WEB_MODULES
    environment = dict(os.environ)
    environment.update({"BOT_TOKEN": "123456:TESTTOKEN", "BOT_MODE": "webhook",
                        "WEB_BACKGROUND_JOBS_ENABLED": "true"})
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path.cwd(), env=environment,
        text=True, capture_output=True, check=True,
    )
    report = json.loads(result.stdout.strip())
    assert report == {"background_enabled": False, "routers": 23, "heavy_loaded": []}


def test_bot_has_no_eager_heavy_worker_imports() -> None:
    tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
    eager = {
        node.module for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module in HEAVY_WEB_MODULES
    }
    assert eager == set()
    source = Path("bot.py").read_text(encoding="utf-8")
    assert 'maintenance_callback=None' in source
    assert 'if resolved == "webhook":\n        return False' in source


def test_forward_worker_still_owns_collection_without_execution_authority() -> None:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = ".codex-test-deps;."
    result = subprocess.run(
        [sys.executable, "-m", "tools.run_forward_microstructure_collector",
         "--print-capabilities"],
        cwd=Path.cwd(), env=environment, text=True, capture_output=True, check=True,
    )
    report = json.loads(result.stdout)
    assert set(report["venues"]) == {"BINANCE", "OKX", "BINGX"}
    assert report["symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert report["execution_authority"] is False


def test_render_blueprint_enforces_light_web_and_dedicated_worker() -> None:
    text = Path("render.yaml").read_text(encoding="utf-8")
    web, worker = text.split("  - type: worker", 1)
    assert "name: LiquidityVisionBot-1" in web
    assert "startCommand: python bot.py" in web
    assert "WEB_BACKGROUND_JOBS_ENABLED\n        value: \"false\"" in web
    assert "MICROSTRUCTURE_COLLECTION_ENABLED\n        value: \"false\"" in web
    assert "PUMP_SCANNER_ENABLED\n        value: \"false\"" in web
    assert "LIVE_RECONCILIATION_ENABLED\n        value: \"false\"" in web
    assert "name: liquidityvision-forward-worker" in worker
    assert "startCommand: python -m tools.run_forward_microstructure_collector" in worker
    assert "FORWARD_COLLECTION_ENABLED\n        value: \"true\"" in worker
    for key in ("LIVE_EXECUTION_ENABLED", "LIVE_DISPATCHER_ENABLED",
                "ALLOW_USER_LIVE_CONNECTIONS", "BINGX_PRODUCTION_ADAPTER_ALLOWED"):
        assert f"- key: {key}\n        value: \"false\"" in web
        assert f"- key: {key}\n        value: \"false\"" in worker


def test_web_and_worker_process_authority_are_separate() -> None:
    bot_source = Path("bot.py").read_text(encoding="utf-8")
    worker_source = Path("tools/run_forward_microstructure_collector.py").read_text(encoding="utf-8")
    assert "ForwardCollectorSupervisor" not in bot_source
    assert "AppendOnlyEventStore" not in bot_source
    assert "aiogram" not in worker_source.lower()
    assert "BOT_TOKEN" not in worker_source
    assert "place_order" not in worker_source
