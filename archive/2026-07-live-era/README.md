# July 2026 live-trading era

State files from when these coins traded **live** (`dry_run: false`), archived
2026-08-26.

`state_near-usdc.json.bak` holds 35 trades and a `LONG` position, last touched
2026-07-21, with −$2.94 realised. NEAR really did trade live and lose money.

They were sitting unlabelled in the project root, where an open live position in
a `.bak` file reasonably prompts "wait, is NEAR live right now?" — it is not.
Nothing reads these; the dashboard scans `state_*.json` and `.bak` does not
match. Kept because they are the only record of that era.
