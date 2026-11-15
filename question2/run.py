from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from question2.agent import SupportPipeline  # noqa: E402

PUBLIC = ["T-1001", "T-1002", "T-1003", "T-1004"]


def main() -> None:
    pipe = SupportPipeline()
    rows = []
    retrieved = {}
    for tid in PUBLIC:
        res = pipe.handle_ticket(tid)
        retrieved[tid] = pipe.last_retrieved
        rows.append({"ticket_id": tid, "action": res.action, "kb_article": res.kb_article})
    print(json.dumps({"resolutions": rows, "total_points": pipe.session.total_points()}, indent=2))
    # Per-call breakdown. Triage, retrieve and audit are plain code: 0 LLM points.
    print("\nticket  node      in_tok  out_tok  points  retrieved (top 3)")
    for tid, call in zip(PUBLIC, pipe.session.calls):
        print(
            f"{tid}  {call.label:<8} {call.input_tokens:>6}  {call.output_tokens:>7}  "
            f"{call.points():>6}  {', '.join(retrieved[tid])}"
        )
    print(f"average points per ticket: {pipe.session.total_points() // len(PUBLIC)}")


if __name__ == "__main__":
    main()
