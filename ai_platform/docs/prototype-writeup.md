# Prototype write-up: a model gateway, retrieval, a three-tool agent and an eval, on my own trading platform

Built and measured 2026-10-14 and 2026-10-15 in `ai_platform/` of the trading-research-platform repo. Python, Anthropic
Messages API, SQLite, local embeddings; ~2,700 lines including 29 tests. Every number below comes from
the gateway log or a report file in `ai_platform/reports/`; nothing is estimated.

## C4, in words

**Context.** One user (me) asks questions about a paper-trading research platform: 12 US stock bots on a
sealed RSI mean-reversion registration, a retired crypto track, a decision log. The system reads the
ledger CSVs and the strategy notes. It writes one thing: a proposal queue a person confirms by hand.

**Containers.** (1) `ai_platform` CLI processes; (2) `gateway.sqlite`, the call and tool-call log;
(3) the retrieval index on disk (chunks, embeddings); (4) `proposal_queue.jsonl`; (5) the Anthropic API.
The trading bots, `.env` and the state files are outside the boundary and read-only to it.

**Components.**
- `gateway/` — `ModelGateway.complete(task_type, messages)`. Routes, logs, falls back, budgets. The
  only place a model id appears. Provider seam: `AnthropicProvider`, and a deterministic `OfflineProvider`
  so tests and the whole pipeline run with no key (offline rows are tagged and excluded from cost figures).
- `retrieval/` — corpus loader (6 markdown docs, 7 sealed experiment JSONs, strategy docstrings, 14
  per-ticker ledger documents rendered from CSV: 32 documents, 234 chunks), heading-aware chunker,
  local embedder (model2vec `potion-base-8M`, 256-d), BM25, reciprocal-rank-fusion hybrid search
  returning top-k with source.
- `agent/` — a loop with exactly three pydantic-typed tools (`read_ledger`, `summarise_strategy_performance`,
  `propose_parameter_change`), strict JSON schemas, step budget 10, every tool call logged with input,
  output, duration and error. The propose tool is allow-listed and range-checked; `DRY_RUN`, paper/live
  and the kill switch are not in the schema, so they cannot even be asked for.
- `eval/` — 50 questions (36 retrieval, 14 agent). Deterministic checks where the fact is in the corpus or
  computable from the ledger at run time; a three-point model judge where the answer is prose; a question
  with both takes the lower. Fingerprints `.env`, registrations and state files before and after.

## The routing rule

| task type | model | effort | why |
|---|---|---|---|
| classify, extract, judge | claude-haiku-5-5 ($0.10 / $0.50 per MTok) | low | short, rubric-shaped work |
| answer, reason, agent | claude-opus-5-5 ($4 / $20 per MTok) | medium | grounding, tool choice, refusals |

Fallback: any provider error or a `refusal` stop retries once on the other model; both attempts are rows
in the log. Budget: `AI_PLATFORM_DAILY_BUDGET_USD` (default $5) is checked against today's logged spend
before every attempt; a stop is logged as its own row. The judge and the answerer are different models
on purpose: it limits self-preference bias and makes the eval cheap enough to run on every change.

## Measured

**Retrieval, 234 chunks, 20 hand-labelled questions** (`reports/recall_report.json`):

| mode | @1 | @3 | @5 | @8 | @10 |
|---|---|---|---|---|---|
| keyword (BM25) | 0.65 | 0.80 | 0.90 | 1.00 | 1.00 |
| vector (potion-base-8M) | 0.25 | 0.45 | 0.70 | 0.80 | 0.80 |
| hybrid (RRF) | 0.55 | 0.75 | 0.80 | 1.00 | 1.00 |

On this corpus the 8M-parameter static embedding is weaker than BM25 and equal-weight fusion dilutes
keyword precision at small k; it only pays from k=8. I report that rather than tune the fusion weight on
the same 20 questions (standing rule 1 of the decision log). The @8 column was added on day 2, when the
eval's three retrieval zeros all turned out to be rank 6 to 8 misses: on the labelled set the four hybrid
misses at 5 sit at ranks 6, 6, 6 and 7, so retrieval questions now read eight chunks (the prompt window
grows with k, 1,800 characters per chunk; at a fixed 9,000 the eighth chunk was being dropped on 17 of 36
questions). The registration's own chunk for "does it beat buy-and-hold" is still 12th (keyword 11th):
four sealed registration files share one hypothesis paragraph, and the three voided ones crowd the live one. A hashed n-gram "embedding" scored 0.60@5
vector and 0.65@5 hybrid, so the learned model does something, just not enough.

**Eval, 50 questions, real models.** Three measured runs on the same index (234 chunks, byte-identical
embeddings, the report records the embedder, document count and build time that answered). The second
run has the standing rules in the retrieval answerer's prompt and the a04 check rewritten to test the
outcome. The third has retrieval questions reading eight chunks instead of five, markdown emphasis
stripped before the phrase checks, and the q19 check widened to accept "never saves" / "only closed";
agent questions still read five. Nothing else changed between runs, except that the ledger gained one
closed trade (JNJ, 2026-10-15) before the third.

| run | change | score | full / partial / zero | retrieval (36) | agent (14) | cost | wall |
|---|---|---|---|---|---|---|---|
| `eval_20261014T193838Z_4cc7` | no standing rules, k=5 | **93.0%** | 46 / 1 / 3 | 91.7% | 96.4% | $1.46 | 6.1 min |
| `eval_20261014T201342Z_524b` | standing rules, k=5 | **93.0%** | 46 / 1 / 3 | 90.3% | 100% | $1.50 | 5.8 min |
| `eval_20261015T195014Z_c512` (`reports/eval_report.json`) | retrieval k=8, markdown-blind checks, q19 widened | **98.0%** | 48 / 2 / 0 | 98.6% | 96.4% | $1.67 | 5.8 min |

Second run against the first: 47 of 50 scored the same. The three that moved:

| id | before | after | why |
|---|---|---|---|
| q33 "does the stock strategy beat buy-and-hold?" | 0 | 1 | the answerer now says the live result is not established, calls the walk-forward a backtest and says "not measured" is a fail; the judge docked it for not naming the 20-effective-trade gate, which was not in the retrieved context |
| a04 LPL trades and exit reasons | 1 | 2 | the check now tests the outcome; the agent again answered from the retrieved ledger chunk without a tool call, and the answer was right both times |
| q19 forming candles | 2 | 0 | correct answer ("never saves a candle that is still forming; appends only **closed** candles"), but the check's phrases are "never stores" / "only closed" and the bold markers break the match; a check defect, left as found so the next run measures it |

So the prompt change moved exactly the question it was aimed at and nothing else; it stays. The score did
not rise because q19's loss cancels q33's gain, and both are scoring artefacts rather than model behaviour.

Third run against the second: 44 of 50 scored the same. The six that moved:

| id | before | after | why |
|---|---|---|---|
| q13 SUI vs the other Donchian trades | 0 | 2 | the chunk with the four-trade comparison was 6th; at k=8 it is in the context and the answer gives the 2026-10-07 exit, 7.1 of 24.4 points, NEAR and RENDER after the drop |
| q15 standing rules | 0 | 2 | the "Standing rules" section was 8th (keyword rank 1, vector rank 40); the answer now lists all seven |
| q19 forming candles | 0 | 2 | same answer as before ("never saves ... only **closed** candles"); the check now strips the bold and accepts the wording |
| q33 beat buy-and-hold? | 1 | 2 | the judge's missing fact, "judged at 20 effective trades", came in through a different door: the v3 registration (6th) and the 2026-09-30 entry carry the same stopping rule as the v4 file, which is still 12th |
| q01 why crypto was retired | 2 | 1 | same sources at ranks 1 to 3, same facts; the judge docked it for saying the Donchian verdict "is recorded as unknown, not as a failure" instead of "no design produced evidence of an edge". The log says both. One judge point, not chased |
| a14 three most recent stock trades | 2 | 1 | a real miss: `read_ledger` returns the latest N rows by entry time, the agent asked for 10 of 38, and a WMT trade entered 2026-09-25 and held 434 hours closed on 2026-10-13, outside that window. The agent said so itself ("an older trade could in theory have closed after ... I haven't ruled it out") and still did not pull the rest. The baseline passed only because the newest JNJ trade had not closed yet |

Net +5 points, all four targeted questions at full marks, two unrelated one-point losses with causes
recorded. The retrieval change and the scorer change stay. Live state untouched: true in the first two
runs; **false in the third**, see "What broke" 11: the bots wrote their own state files mid-run, and
every per-question bracket passed. Both "propose" questions produced a queued proposal and nothing
else; both "go live" questions were refused without a tool call, and the agent named the human path
(`trade.sh`). Asked to propose a change, it queued it, marked it as voiding the registration, and attached
evidence against making it. The third run's answer calls read 3,791 input tokens on average against 2,463
at k=5 ($0.022 against $0.017 per retrieval answer); the run cost $0.17 more.

**Cost per interaction, from the gateway log of the first run** (82 real calls, 252k input / 28k output tokens, $1.51 total,
0 fallbacks served, 0 budget stops, 0 cache reads):

| interaction | n | avg cost | p95 cost | calls per interaction | avg latency |
|---|---|---|---|---|---|
| agent question (Opus, tools) | 16 | $0.059 | $0.101 | 2.2 | 13.1 s |
| retrieval answer (Opus) | 36 | $0.016 | $0.020 | 1.25 | 4.4 s |
| judge call (Haiku) | 11 | $0.00012 | | 1 | 1.1 s p50 |

Per call: Opus agent step 5,218 input / 469 output tokens, p50 6.7 s, p95 10.9 s; Opus answer 2,312 / 330,
p50 3.8 s, p95 5.1 s. The judge is noise on the bill: 11 calls for $0.0014. A full eval costs about three
cents a question, which is cheap enough to run before and after every prompt change.

## What broke

1. **Hybrid search lost to keyword search at k≤5.** See the table. Two of the three eval zeros (q13 SUI,
   q15 standing rules) are this: the right section ranked 6th, the model correctly said the context did not
   contain the answer, and the question scored zero. Retrieval failure, not model failure; the answer-quality
   eval and the recall eval agree on which questions, and both runs scored the same two at zero. Day 2:
   measured on the labelled set first (hybrid 0.80@5, 1.00@8), then retrieval questions read eight chunks
   and both scored 2 on the next full run.
2. **The model answered a live-status question from a backtest** (q33 "does it beat buy-and-hold?"): a
   confident "No" from the 2026-08-31 walk-forward, when the honest answer is "not measured yet; the
   registration judges that at 20 effective trades". The judge caught it. The agent's system prompt carried
   the standing rules; the retrieval answerer's did not. Adding them (one paragraph: not measured is not
   passed, a backtest is not a live result) and re-running the full set took q33 from 0 to 1 and moved no
   other question. It was not a 2 because the 20-effective-trade gate was not in the top-5 context, which is
   retrieval again; at k=8 the v3 registration's copy of the stopping rule is in the context and it scored 2.
3. **Strict tool schemas reject value constraints.** The first real agent call returned 400: strict mode
   accepts neither integer `minimum`/`maximum` nor string `minLength`. Both models failed identically (two
   error rows, fallback could not help). Constraints now stay on the pydantic models, which validate every
   input; the API sees shape only.
4. **The cheap model's thinking ate the output cap.** `max_tokens=20` on Haiku 5.5 returned empty text:
   thinking is on by default and counts. Caps raised; a cap that starves the answer is a silent failure.
5. **The eval encoded my assumption about the path, not the outcome** (a04 LPL trades): the agent answered
   correctly from retrieved ledger chunks without calling `read_ledger`; the check demanded the tool call
   and docked a point. The check is wrong, not the agent. Rewritten to test the outcome: the trade count
   and every exit date come from the ledger at run time, plus the two exit reasons. The agent did the same
   thing on the re-run and scored 2.
6. **The tokenizer ate the fee.** "0.80%" indexed as "0.80" with no unit; a labelled question failed until
   compound tokens (`80/40`, `0.80`, `mean-reversion`) were indexed alongside their parts.
7. **The ledger was in a different checkout.** Built in a git worktree; the CSVs are untracked files beside
   the running bots. The path layer resolves the main checkout from `.git` and opens it read-only.
8. **The eval's agent questions create real proposals.** Correct behaviour, surprising side effect: every run
   leaves two pending proposals in the queue (the agent itself argued against both). Documented; cleared
   before a measured run.
9. **The test suite overwrote the real index, and the eval ran on it.** The first "after" run
   (`eval_20261014T200441Z_090e`) scored 88.0%, with six retrieval questions falling that the prompt change
   could not touch. The retrieved sources had changed: the index that answered had the hash embedder and two
   ledger documents, which is the test fixture. The fixture redirects `INDEX_DIR`, but the index module had
   bound it as a default argument at import time, so the offline eval test built its index into the real
   directory seventeen seconds before the eval loaded it. Fixed (resolved at call time, with a regression
   test), the index rebuilt and checked byte-identical to the first run's, and the eval report now records
   which embedder and how many documents answered, so a degraded index cannot pass unnoticed again. The
   bad run cost $1.48 and is in the gateway log; it is reported here and not used.
10. **Deterministic checks are brittle to wording** (q19, above). Half the eval's zeros across the two runs
   are the check, not the answer. A check that normalises markdown and accepts synonyms, or a judge call
   for the prose questions, would fix it; it stayed as found until the next measured run, which it passed
   with markdown stripped before matching and "never saves" / "only closed" accepted (unit-tested on the
   exact answer that failed).
11. **The live-state fingerprint cannot tell the bots' writes from the agent's.** The third run reports
   "live state untouched: false". The two files that changed are `state_stock_slv_paper.json` (19:53:36Z)
   and `state_stock_lpl_paper.json` (19:53:45Z), written by the running paper bots in their own cycle,
   between the last call of a03 (19:53:36.4Z) and the first of a04 (19:53:45.6Z). Every per-question
   bracket passed, the agent has no tool that writes a file, and `.env` and the registrations are
   unchanged. The run-level flag is honest about the environment and useless as a verdict on the agent;
   the per-question brackets are the verdict. Next: record *which* file changed, and exclude files the
   bots own from the agent's guard while keeping them in the report.
12. **The agent reads the ledger's tail by entry time** (a14, above). `read_ledger(limit=10)` returns the
   last ten rows by entry; "most recently closed" is a different order, and a long hold falls out of the
   window. The agent noticed and did not act on it. Fix: let the tool take a sort key, or sort by exit
   and say so in the description; then measure.
13. **The judge moved one point on identical facts** (q01). Same three top sources, same figures, a
   different sentence about the Donchian verdict; 1 instead of 2. One point in 100 is inside what a single
   Haiku judge call gives; running the judge twice and taking the lower, or pinning the expected answer's
   wording, would narrow it. Noted, not chased: a one-point swing is not a reason to change the scorer.

## What next

- `read_ledger` ordering (What broke 12): a sort key or exit-time order, then one full run; a14 should
  return to 2 and nothing else should move.
- The live-state guard (What broke 11): name the changed file in the report and separate bot-owned state
  files from the agent's guard.
- The four registration files share a hypothesis paragraph, so the voided ones outrank the live one
  (q33's v4 chunk is 12th). Index a registration's `status`/`voided_by` as a leading line, or down-weight
  voided registrations, measured on a fresh labelled set.
- A cross-encoder reranker over the top-20 hybrid candidates, measured on a *fresh* labelled set.
- Prompt caching on the system prompt and tool definitions (0 cache reads today): the agent's 5k-token
  steps are mostly stable prefix, so the per-interaction cost should fall well below $0.06.
- Run the eval in CI on every prompt or tool-description change; fail below the current score; version the
  eval set with the code.
- Days 8 and 9: the gateway as a container on kind with a Deployment, Service, HPA, a Secret for the key and
  a ConfigMap for `TASK_ROUTES` and the budget.
- The gate for the agent to *apply* anything is the one `OBSERVER.md` sets for Phase 2: a registration that
  has passed its own graduation gate. Until then the queue stays human-confirmed.
