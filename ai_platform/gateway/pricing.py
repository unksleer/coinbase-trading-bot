"""
Model ids and list prices (USD per million tokens), Anthropic first-party API,
as of 2026-10 (claude-api skill, cached 2026-10-06).

Two models only. The routing rule in gateway.py picks between them by task
type; nothing else in the prototype names a model.
"""
from __future__ import annotations

CHEAP_MODEL = "claude-haiku-5-5"
FRONTIER_MODEL = "claude-opus-5-5"

# input, output, cache_read, cache_write ($/MTok)
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
}


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    p = PRICES[model]
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read_tokens * p["cache_read"]
        + cache_write_tokens * p["cache_write"]
    ) / 1_000_000.0
