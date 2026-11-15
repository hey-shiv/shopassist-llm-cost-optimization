from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from shared.billing import CostSession
from shared.kb import article_ids, load_article
from shared.llm import ChatMessage, LlmClient
from shared.tools import format_thread, get_order, get_ticket

from question1.agent import parse_resolution

# Cost design (see the points formula in the README):
#
# Every LLM call has a floor of ~40 output tokens (the JSON answer) = ~600 points,
# plus its whole input. Four LLM calls would spend ~2,400 of the 2,500-point budget
# on output alone, so only the node that needs language understanding calls the LLM.
#
#   triage   -> plain code   (turn the ticket + order facts into search terms)
#   retrieve -> plain code   (rank help articles by keyword overlap, keep the top few)
#   compose  -> ONE LLM call (sees only the few retrieved articles, never the full KB)
#   audit    -> plain code   (validate the model's JSON against known actions/articles)
#
# The prompt has no wording that invites long output (the simulator writes an essay
# for phrases like "step by step" or "double-check") and no per-call random values.
# It stays far below the 1,024-token minimum for cached input, so cached_prefix is
# not used.

ALLOWED_ACTIONS = {"REFUND", "REPLACE", "SEND_ARTICLE", "ESCALATE_TIER2"}
TOP_K = 3

_INSTRUCTIONS = (
    "You are ShopAssist, the Tier-1 customer support copilot. "
    "Choose one action for the ticket using the order facts and the help-center articles below.\n"
    "Actions: REFUND, REPLACE, SEND_ARTICLE, ESCALATE_TIER2.\n"
    'Reply with only a JSON object: {"action": "...", "kb_article": "<article id>"}'
)

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "and", "for", "you", "your", "are", "was", "were", "has", "have", "had",
    "not", "can", "may", "any", "all", "but", "with", "that", "this", "from", "they",
    "them", "their", "its", "our", "out", "who", "get", "got", "please", "hi",
}


@dataclass
class TicketResolution:
    action: str
    kb_article: str


def _terms(text: str) -> list[str]:
    """Lowercase words with a light suffix strip so 'refunded' matches 'refund'."""
    out = []
    for word in _WORD.findall(text.lower()):
        if len(word) < 3 or word in _STOP:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                word = word[: -len(suffix)]
                break
        out.append(word)
    return out


class SupportPipeline:
    """Same product as Question 1 — triage, retrieve, compose, audit."""

    def __init__(self, session: CostSession | None = None):
        self.session = session or CostSession()
        self.llm = LlmClient(self.session)
        self.last_retrieved: list[str] = []
        self._build_index()

    # -- index (built once, free) -------------------------------------------------
    def _build_index(self) -> None:
        self._ids = article_ids()
        self._tf: dict[str, Counter] = {}
        self._title_terms: dict[str, set[str]] = {}
        for aid in self._ids:
            self._tf[aid] = Counter(_terms(load_article(aid)))
            self._title_terms[aid] = set(_terms(aid.replace("-", " ")))
        n = len(self._ids)
        df = Counter(t for tf in self._tf.values() for t in tf)
        self._idf = {t: math.log(1 + n / c) for t, c in df.items()}

    # -- pipeline -----------------------------------------------------------------
    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])
        thread = format_thread(ticket)
        # Compact JSON: same facts, fewer tokens than indent=2.
        snapshot = json.dumps(order, separators=(",", ":"))

        query = self._triage(thread, order)
        article_ids_found = self._retrieve(query)
        self.last_retrieved = article_ids_found
        completion = self._compose(ticket_id, thread, snapshot, article_ids_found)
        return self._audit(completion, article_ids_found)

    def _triage(self, thread: str, order: dict) -> list[str]:
        """Code node: ticket text plus words describing the order facts."""
        facts = [str(order.get("product_type", ""))]
        if order.get("customized"):
            facts += ["custom", "personalized", "customized"]
        if order.get("damage_reported"):
            facts += ["damaged", "replacement"]
        if order.get("license_revealed"):
            facts += ["license", "digital", "revealed"]
        if order.get("product_type") == "subscription":
            facts += ["subscription", "annual", "cancelled"]
        return _terms(thread + " " + " ".join(facts))

    def _retrieve(self, query: list[str]) -> list[str]:
        """Code node: rank articles by idf-weighted keyword overlap (title hits count double)."""
        scores = {}
        for aid in self._ids:
            score = 0.0
            for term in set(query):
                if term in self._tf[aid]:
                    weight = self._idf[term]
                    score += weight * (2 if term in self._title_terms[aid] else 1)
            scores[aid] = score
        ranked = sorted(self._ids, key=lambda a: (-scores[a], a))
        hits = [a for a in ranked if scores[a] > 0][:TOP_K]
        return hits or ranked[:TOP_K]

    def _compose(
        self, ticket_id: str, thread: str, snapshot: str, retrieved: list[str]
    ) -> str:
        """The only LLM call: stable instructions first, per-ticket content last."""
        articles = "\n\n".join(f"--- {aid} ---\n{load_article(aid)}" for aid in retrieved)
        messages = [
            ChatMessage("system", _INSTRUCTIONS),
            ChatMessage(
                "user",
                "\n\n".join(
                    [
                        f"ARTICLES:\n{articles}",
                        f"TICKET_ID: {ticket_id}",
                        f"THREAD:\n{thread}",
                        f"ORDER_SNAPSHOT:\n{snapshot}\nEND_SNAPSHOT",
                    ]
                ),
            ),
        ]
        return self.llm.chat("compose", messages)

    def _audit(self, completion: str, retrieved: list[str]) -> TicketResolution:
        """Code node: accept only a well-formed answer with a real action and article."""
        try:
            parsed = parse_resolution(completion)
        except (ValueError, KeyError):
            return TicketResolution("ESCALATE_TIER2", retrieved[0])
        if parsed.action not in ALLOWED_ACTIONS or parsed.kb_article not in self._tf:
            return TicketResolution("ESCALATE_TIER2", retrieved[0])
        return TicketResolution(parsed.action, parsed.kb_article)
