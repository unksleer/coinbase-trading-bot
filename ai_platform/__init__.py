"""
ai_platform: a small, finished gen-AI prototype built on the trading research
platform. Four pieces, each runnable from the command line:

  gateway    every model call goes through one module (routing, cost log,
             fallback, daily budget)
  retrieval  chunk + embed + hybrid search over the ledger and strategy notes
  agent      three typed tools, step budget of 10, proposals to a queue only
  eval       30+ real questions, deterministic checks and a 3-point judge

It is READ-ONLY against trading state. The only thing it writes outside its own
data/ folder is nothing: proposals go to ai_platform/data/proposal_queue.jsonl
and a person applies them by hand (or not).
"""
