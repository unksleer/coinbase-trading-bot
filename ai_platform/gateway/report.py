"""
Cost per interaction, from the gateway log. Real calls only by default.

    python -m ai_platform.gateway.report [--provider anthropic|offline|all] [--day YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import statistics

from ..paths import GATEWAY_DB
from .store import Store


def _pct(xs, q):
    if not xs:
        return 0
    xs = sorted(xs)
    i = min(len(xs) - 1, int(round(q * (len(xs) - 1))))
    return xs[i]


def summarise(store: Store, provider: str = "anthropic", day: str | None = None) -> dict:
    where, args = [], []
    if provider != "all":
        where.append("provider = ?"); args.append(provider)
    if day:
        where.append("day = ?"); args.append(day)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    rows = store.query(f"SELECT * FROM calls {w} ORDER BY id", tuple(args))
    out: dict = {"provider": provider, "day": day, "calls": len(rows), "by_task": {}, "by_interaction": {}}
    if not rows:
        return out
    ok = [r for r in rows if r["status"] == "ok"]
    out["errors"] = sum(1 for r in rows if r["status"] == "error")
    out["fallbacks_served"] = sum(1 for r in ok if r["is_fallback"])
    out["budget_stops"] = sum(1 for r in rows if r["status"] == "budget")
    out["total_cost_usd"] = round(sum(r["cost_usd"] for r in rows), 6)
    out["total_input_tokens"] = sum(r["input_tokens"] for r in rows)
    out["total_output_tokens"] = sum(r["output_tokens"] for r in rows)
    for t in sorted({r["task_type"] for r in rows}):
        rs = [r for r in ok if r["task_type"] == t]
        lat = [r["latency_ms"] for r in rs]
        out["by_task"][t] = {
            "calls": len(rs),
            "models": sorted({r["served_model"] for r in rs}),
            "avg_cost_usd": round(statistics.mean([r["cost_usd"] for r in rs]), 6) if rs else 0,
            "total_cost_usd": round(sum(r["cost_usd"] for r in rs), 6),
            "avg_input_tokens": int(statistics.mean([r["input_tokens"] for r in rs])) if rs else 0,
            "avg_output_tokens": int(statistics.mean([r["output_tokens"] for r in rs])) if rs else 0,
            "latency_p50_ms": _pct(lat, 0.5), "latency_p95_ms": _pct(lat, 0.95),
        }
    per_ix: dict[str, dict] = {}
    for r in rows:
        d = per_ix.setdefault(r["interaction_id"], {"cost_usd": 0.0, "calls": 0, "latency_ms": 0, "task_types": set()})
        d["cost_usd"] += r["cost_usd"]; d["calls"] += 1; d["latency_ms"] += r["latency_ms"]
        d["task_types"].add(r["task_type"])
    kinds: dict[str, list] = {}
    for d in per_ix.values():
        kind = "agent" if "agent" in d["task_types"] else "answer" if "answer" in d["task_types"] else "/".join(sorted(d["task_types"]))
        kinds.setdefault(kind, []).append(d)
    for kind, ds in sorted(kinds.items()):
        costs = [d["cost_usd"] for d in ds]
        out["by_interaction"][kind] = {
            "interactions": len(ds),
            "avg_cost_usd": round(statistics.mean(costs), 6),
            "p95_cost_usd": round(_pct(costs, 0.95), 6),
            "avg_calls": round(statistics.mean([d["calls"] for d in ds]), 2),
            "avg_latency_ms": int(statistics.mean([d["latency_ms"] for d in ds])),
        }
    return out


def render(s: dict) -> str:
    lines = [f"gateway log: provider={s['provider']} day={s['day'] or 'all'} calls={s['calls']}"]
    if not s["calls"]:
        return lines[0] + " (nothing logged)"
    lines.append(f"  total cost ${s['total_cost_usd']:.4f}  in {s['total_input_tokens']} tok  out {s['total_output_tokens']} tok"
                 f"  errors {s['errors']}  fallbacks served {s['fallbacks_served']}  budget stops {s['budget_stops']}")
    lines.append("  per call by task type:")
    for t, d in s["by_task"].items():
        lines.append(f"    {t:<9} n={d['calls']:<4} {','.join(d['models']):<34} avg ${d['avg_cost_usd']:.5f}"
                     f"  in {d['avg_input_tokens']:>6} out {d['avg_output_tokens']:>5}  p50 {d['latency_p50_ms']} ms  p95 {d['latency_p95_ms']} ms")
    lines.append("  per interaction:")
    for k, d in s["by_interaction"].items():
        lines.append(f"    {k:<14} n={d['interactions']:<4} avg ${d['avg_cost_usd']:.5f}  p95 ${d['p95_cost_usd']:.5f}"
                     f"  calls/ix {d['avg_calls']}  avg latency {d['avg_latency_ms']} ms")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--day", default=None)
    ap.add_argument("--db", default=str(GATEWAY_DB))
    a = ap.parse_args(argv)
    print(render(summarise(Store(a.db), a.provider, a.day)))


if __name__ == "__main__":
    main()
