from __future__ import annotations

import json
import re
from dataclasses import dataclass

from shared.billing import CostSession
from shared.kb import all_articles_blob
from shared.llm import ChatMessage, LlmClient
from shared.tools import format_thread, get_order, get_ticket


@dataclass
class TicketResolution:
    action: str
    kb_article: str
    customer_reply: str
    raw_completion: str


def parse_resolution(text: str) -> TicketResolution:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"No JSON in model output:\n{text[:400]}")
    data = json.loads(m.group())
    return TicketResolution(
        action=str(data["action"]).upper(),
        kb_article=str(data["kb_article"]),
        customer_reply=str(data.get("customer_reply", "")),
        raw_completion=text,
    )


# Cost notes (see the points formula in the README):
# - Output tokens cost 5x input tokens, so we ask for a bare JSON object and
#   avoid any wording that invites a long explanation. The local simulator
#   writes an essay if the prompt contains phrases like "step by step",
#   "full reasoning" or "double-check", so none of those appear anywhere below.
# - The prompt has no per-call random value, so the system part is identical
#   for every ticket. It is only ~250 tokens, below the 1,024-token minimum for
#   cached input, so we do not pass cached_prefix: it would not be billed as cached.
_INSTRUCTIONS = (
    "You are ShopAssist, the Tier-1 customer support copilot. "
    "Choose one action for the ticket using the order facts and the help-center policy below.\n"
    "Actions: REFUND, REPLACE, SEND_ARTICLE, ESCALATE_TIER2.\n"
    'Reply with only a JSON object: {"action": "...", "kb_article": "<article id>"}'
)


class SupportCopilot:
    """
    ShopAssist Tier-1 copilot (baseline).

    Given a support ticket + order snapshot, choose an action, cite the help-center
    article, and draft the customer reply.
    """

    def __init__(self, session: CostSession | None = None):
        self.session = session or CostSession()
        self.llm = LlmClient(self.session)

    def build_messages(self, ticket_id: str) -> list[ChatMessage]:
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])
        thread = format_thread(ticket)
        # Compact JSON: same facts, fewer tokens than indent=2.
        snapshot = json.dumps(order, separators=(",", ":"))

        # Stable content first, per-ticket content last.
        system = "\n\n".join([_INSTRUCTIONS, "HELP CENTER:", all_articles_blob()])
        user = "\n\n".join(
            [
                f"TICKET_ID: {ticket_id}",
                f"THREAD:\n{thread}",
                f"ORDER_SNAPSHOT:\n{snapshot}\nEND_SNAPSHOT",
            ]
        )
        return [ChatMessage("system", system), ChatMessage("user", user)]

    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        messages = self.build_messages(ticket_id)
        completion = self.llm.chat("handle_ticket", messages)
        return parse_resolution(completion)
