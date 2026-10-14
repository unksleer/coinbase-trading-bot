"""Candidate screens load stocks only; a leftover crypto screen file is ignored."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dashboard


def test_crypto_screen_file_is_ignored(tmp_path):
    for m in ("stocks", "crypto"):
        (tmp_path / f"screen_{m}.json").write_text(json.dumps(
            {"strategy": "x", "timeframe": "1h", "cost_model": "c", "generated_at": time.time(),
             "results": [{"symbol": "A", "verdict": "check", "net": 1, "trades": 3, "win_rate": 60, "opportunities": 2}]}))
    out = dashboard.DashboardHandler.load_screens(log_dir=str(tmp_path))
    assert set(out) == {"stocks"}
