#!/usr/bin/env python3
import argparse
import json
import os
import sys

from utils.learner_policy import atomic_write_json, load_approved_ids


ROOT = os.path.dirname(os.path.abspath(__file__))
RECOMMENDATIONS_PATH = os.path.join(ROOT, "logs", "learner_recommendations.json")
APPROVALS_PATH = os.path.join(ROOT, "logs", "learner_approvals.json")


def load_recommendations(path):
    try:
        with open(path) as handle:
            return json.load(handle).get("recommendations", [])
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return []


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review Learner V2 recommendations")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--approve", metavar="ID", help="approve an exact recommendation ID")
    action.add_argument("--revoke", metavar="ID", help="revoke a recommendation approval")
    parser.add_argument("--recommendations", default=RECOMMENDATIONS_PATH)
    parser.add_argument("--approvals", default=APPROVALS_PATH)
    args = parser.parse_args(argv)

    recommendations = load_recommendations(args.recommendations)
    by_id = {item.get("id"): item for item in recommendations if item.get("id")}
    approved = load_approved_ids(args.approvals)

    if args.approve:
        if args.approve not in by_id:
            print(f"Unknown recommendation ID: {args.approve}", file=sys.stderr)
            return 2
        approved.add(args.approve)
        atomic_write_json(args.approvals, {"approved": sorted(approved)})
        print(f"Approved paper recommendation {args.approve}")
        return 0

    if args.revoke:
        approved.discard(args.revoke)
        atomic_write_json(args.approvals, {"approved": sorted(approved)})
        print(f"Revoked recommendation {args.revoke}")
        return 0

    if not recommendations:
        print("No Learner V2 recommendations are waiting for review.")
        return 0

    for item in recommendations:
        status = "APPROVED" if item["id"] in approved else "PENDING"
        proposed = item["proposed"]
        evidence = item["evidence"]
        print(
            f"{item['id']}  {item['pair']}  {status}\n"
            f"  SMA {proposed['short']}/{proposed['long']}  "
            f"SL={proposed['stop_loss']}%  TP={proposed['take_profit']}%\n"
            f"  validation=${evidence['validation_net_profit']:.2f}  "
            f"PF={evidence['validation_profit_factor']:.2f}  "
            f"trades={evidence['validation_trades']}  "
            f"stability={evidence['stability_pass_rate']:.0%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
