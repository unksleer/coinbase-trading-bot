#!/usr/bin/env python3
"""
make_env_example.py — regenerate .env.example from the live .env.

.env is gitignored, so every tuned value lives only on the machine that runs the
bots. Lose that machine and the code survives while the configuration — which is
most of what this project has learned — does not.

Hand-maintaining an example file does not solve it: it drifts silently, and a
drifted example is worse than none because it looks authoritative. Generating it
means the committed file always reflects what is actually configured.

Secrets are blanked and personal details replaced. Everything else, including
the comments explaining why each value is what it is, is carried through.

    .venv/bin/python make_env_example.py
    .venv/bin/python make_env_example.py --check   # exit 1 if out of date
"""
import argparse
import os
import sys

DIR = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(DIR, ".env")
EXAMPLE = os.path.join(DIR, ".env.example")

# Substring match on the key name, so a new *_API_KEY is caught without anyone
# remembering to add it here. Erring toward redacting too much is the right
# direction: a blank value is an inconvenience, a leaked key is not.
SECRET_MARKERS = ("API_KEY", "API_SECRET", "SECRET_KEY", "PASS", "PASSWORD",
                  "TOKEN", "PRIVATE")

# Real but not secret; still nobody else's business.
PII_DEFAULTS = {
    "NOTIFICATION_EMAIL": "you@example.com",
    "SMTP_USER": "you@example.com",
}

HEADER = """# ─────────────────────────────────────────────────────────────────────────────
# .env.example — the live configuration with secrets removed.
#
# Generated from the running .env so it reflects what is ACTUALLY configured,
# not what was configured whenever someone last remembered to update this file.
# Every tuned value below is the real one and carries the reasoning that
# produced it.
#
# Secrets and personal details are blank. Fill them in and rename to .env.
#
# Regenerate after changing configuration:
#   .venv/bin/python make_env_example.py
# ─────────────────────────────────────────────────────────────────────────────

"""


def is_secret(key):
    return any(marker in key.upper() for marker in SECRET_MARKERS)


def render(env_path=ENV):
    """Build the example file contents. Returns (text, redacted_keys)."""
    lines, redacted = [], []
    with open(env_path) as handle:
        for raw in handle:
            raw = raw.rstrip("\n")
            stripped = raw.strip()
            if "=" not in stripped or stripped.startswith("#"):
                lines.append(raw)
                continue
            key = stripped.split("=", 1)[0].strip()
            if is_secret(key):
                lines.append(f"{key}=")
                redacted.append(key)
            elif key in PII_DEFAULTS:
                lines.append(f"{key}={PII_DEFAULTS[key]}")
                redacted.append(key)
            else:
                lines.append(raw)
    return HEADER + "\n".join(lines) + "\n", redacted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if .env.example is out of date")
    args = parser.parse_args()

    if not os.path.exists(ENV):
        sys.exit(f"  {ENV} not found")

    text, redacted = render()

    # Never ship a file that still contains a secret, whatever the logic above
    # concluded. Cheap check, and the failure it prevents is expensive.
    for line in text.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            if is_secret(key.strip()) and value.strip():
                sys.exit(f"  refusing to write: {key.strip()} still has a value")

    if args.check:
        current = open(EXAMPLE).read() if os.path.exists(EXAMPLE) else ""
        if current != text:
            sys.exit("  .env.example is out of date — run make_env_example.py")
        print("  .env.example is current")
        return

    with open(EXAMPLE, "w") as handle:
        handle.write(text)
    print(f"  wrote .env.example ({len(text.splitlines())} lines)")
    print(f"  redacted: {', '.join(sorted(set(redacted)))}")


if __name__ == "__main__":
    main()
