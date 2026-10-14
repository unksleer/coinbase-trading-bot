"""
The proposal queue. The agent's propose tool appends here and nowhere else.
A person lists, confirms or rejects entries. Confirming records the decision
and prints the .env line a person would change by hand; this module never
opens .env, a state file or anything under the bot's control.

    python -m ai_platform.agent.proposals list
    python -m ai_platform.agent.proposals confirm <id>
    python -m ai_platform.agent.proposals reject <id> [--reason "..."]
"""
from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import paths

PENDING, CONFIRMED, REJECTED = "pending_confirmation", "confirmed", "rejected"


def _path(path: Path | None) -> Path:
    return path or paths.PROPOSAL_QUEUE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path: Path | None = None) -> list[dict]:
    p = _path(path)
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def _write_all(items: list[dict], path: Path | None = None) -> None:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    tmp.replace(p)


def enqueue(entry: dict, path: Path | None = None) -> dict:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    item = {"proposal_id": f"prop_{uuid.uuid4().hex[:8]}", "created_at": _now(), "status": PENDING,
            "decided_at": None, "decision_note": None, **entry}
    item["how_a_person_applies_it"] = (
        f"Edit .env: {item['parameter']}={item['proposed_value']} under the active profile, note it in "
        f"DECISIONS.md, void and re-seal the running registration, then restart_bots.py. The agent cannot do any of this.")
    with p.open("a") as fh:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


def decide(proposal_id: str, status: str, note: str | None = None, path: Path | None = None) -> dict:
    items = load(path)
    for it in items:
        if it["proposal_id"] == proposal_id:
            if it["status"] != PENDING:
                raise ValueError(f"{proposal_id} already {it['status']}")
            it["status"], it["decided_at"], it["decision_note"] = status, _now(), note
            _write_all(items, path)
            return it
    raise KeyError(proposal_id)


def render(items: list[dict]) -> str:
    if not items:
        return "proposal queue is empty"
    out = []
    for it in items:
        out.append(f"{it['proposal_id']}  {it['status']:<20} {it['parameter']} {it['current_value']} -> {it['proposed_value']}"
                   f"  ({it['created_at']})\n    {it['rationale'][:160]}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("confirm"); c.add_argument("id"); c.add_argument("--reason", default=None)
    r = sub.add_parser("reject"); r.add_argument("id"); r.add_argument("--reason", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        print(render(load()))
    else:
        it = decide(a.id, CONFIRMED if a.cmd == "confirm" else REJECTED, a.reason)
        print(json.dumps(it, indent=2))
        if a.cmd == "confirm":
            print("\nConfirmed in the queue only. To apply:\n  " + it["how_a_person_applies_it"])


if __name__ == "__main__":
    main()
