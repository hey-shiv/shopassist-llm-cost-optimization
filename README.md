# ShopAssist — support copilot cost lab

**ShopAssist** is a Tier-1 support agent for an e-commerce company. For each ticket it:

1. Reads the customer thread (`data/tickets/`)
2. Loads order facts (`data/orders/` via `shared/tools.py`)
3. Picks an action (`REFUND`, `REPLACE`, `SEND_ARTICLE`, `ESCALATE_TIER2`)
4. Cites the help-center article that backs the decision (`data/kb/`)

| Track | Code | Spec |
|-------|------|------|
| Q1 — single call | [question1/agent.py](question1/agent.py) | [questions/question-1.md](questions/question-1.md) |
| Q2 — pipeline | [question2/agent.py](question2/agent.py) | [questions/question-2.md](questions/question-2.md) |

## Run

```bash
pip install -r requirements.txt
pytest -q
python question1/run.py
python question2/run.py
```

`shared/llm.py` provides a local `LlmClient` with the same billing accounting you would use against a hosted chat API. Do not import `eval/reference.py` from your agent.

## Billing

| Input | Cached input | Output |
|------:|-------------:|-------:|
| 3 | 0.3 | 15 |

Cached input = identical prefix ≥ 1024 tokens across calls.

```
points = uncached_input × 3 + cached_input × 0.3 + output × 15
```

Per-call details are in `shared/billing.py` (`CostSession` on your agent).

## Results (my solution)

Measured with `python question1/run.py` and `python question2/run.py` (points per ticket, public tickets T-1001–T-1004). Answers are unchanged and the hold-out ticket T-1091 still passes.

| Track | Baseline (avg) | Now (avg) | Target |
|-------|---------------:|----------:|-------:|
| Q1 — single call | ~8,810 | 1,738 | ≤ 3,000 |
| Q2 — pipeline | ~73,153 | 1,504 | ≤ 2,500 |

**Q1** — The baseline spent 82% of its points on a ~485-token essay, triggered by "step by step" / "full reasoning" in the prompt. Removed those phrases, the random run id and the few-shot examples, and sent compact order JSON. T-1001: 8,859 → 1,860 points (528 → 390 input tokens, 485 → 46 output tokens).

**Q2** — Every LLM call costs at least ~600 points of output, so four calls cannot fit under 2,500. The four node names are kept, but only `compose` calls the LLM: triage (search terms) and retrieve (keyword ranking, top 3 articles) are plain code, and audit validates the reply in code. T-1001: 73,491 → 1,626 points; T-1003 (escalation path): 1,464 points.

Caching is not used: the prompts are far below the 1,024-token minimum. Retrieval is keyword-based and was only tested on the five articles in this repo.

**Notes:** my handwritten walkthrough of the billing formula, the simulator and the two fixes is here: [Goodnotes notes](https://web.goodnotes.com/s/ItCgUzAvy4LIi6EiQ3eOFN)
