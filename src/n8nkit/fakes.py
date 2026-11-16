"""Stand-ins for the services the workflows call, used by the simulator.

Google Sheets, Gmail, Slack and Google Calendar are in-memory recorders. HTTP goes to the mock Starlette app
in-process. The LLM is a deterministic keyword-rule "model" (``RuleBasedLLM``): it is there to exercise the
workflow's routing, schemas and tool wiring, not to stand in for the quality of a real model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

from . import asgi, mocks


class ServiceError(RuntimeError):
    """A failure from a (fake) external service; the engine applies the node's retry policy to it."""


# ------------------------------------------------------------------------------------------------- recorders

@dataclass
class FakeSheets:
    tables: dict[tuple[str, str], list[dict]] = field(default_factory=dict)

    def append(self, doc: str, sheet: str, row: dict) -> dict:
        self.tables.setdefault((doc, sheet), []).append(dict(row))
        return row

    def read(self, doc: str, sheet: str, filters: list[tuple[str, Any]]) -> list[dict]:
        rows = self.tables.get((doc, sheet), [])
        return [dict(r) for r in rows if all(str(r.get(c, "")) == str(v) for c, v in filters)]

    def rows(self, sheet: str) -> list[dict]:
        return [r for (_d, s), rows in self.tables.items() if s == sheet for r in rows]


@dataclass
class FakeGmail:
    sent: list[dict] = field(default_factory=list)
    drafts: list[dict] = field(default_factory=list)

    def send(self, to: str, subject: str, message: str) -> dict:
        self.sent.append({"to": to, "subject": subject, "message": message})
        return {"id": f"msg-{len(self.sent):04d}", "threadId": f"thr-{len(self.sent):04d}", "labelIds": ["SENT"]}

    def draft(self, to: str, subject: str, message: str) -> dict:
        self.drafts.append({"to": to, "subject": subject, "message": message})
        return {"id": f"draft-{len(self.drafts):04d}", "message": {"labelIds": ["DRAFT"]}}


@dataclass
class FakeSlack:
    messages: list[dict] = field(default_factory=list)

    def post(self, channel: str, text: str) -> dict:
        self.messages.append({"channel": channel, "text": text})
        ts = f"1700000000.{len(self.messages):06d}"
        return {"ok": True, "channel": channel, "ts": ts, "message": {"text": text, "ts": ts}}

    def in_channel(self, channel: str) -> list[dict]:
        return [m for m in self.messages if m["channel"] == channel]


def _dt(s: str) -> datetime:
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


@dataclass
class FakeCalendar:
    events: list[dict] = field(default_factory=list)

    def busy(self, calendar: str, time_min: str, time_max: str) -> list[dict]:
        lo, hi = _dt(time_min), _dt(time_max)
        return [{"start": e["start"], "end": e["end"]} for e in self.events
                if e["calendar"] == calendar and _dt(e["start"]) < hi and _dt(e["end"]) > lo]

    def create(self, calendar: str, start: str, end: str, summary: str, attendees: list[str]) -> dict:
        ev = {"id": f"evt-{len(self.events) + 1:04d}", "calendar": calendar, "start": start, "end": end,
              "summary": summary, "attendees": attendees}
        self.events.append(ev)
        return {"id": ev["id"], "status": "confirmed", "summary": summary, "start": {"dateTime": start},
                "end": {"dateTime": end}, "attendees": [{"email": a} for a in attendees]}


class FakeHttp:
    """Routes requests for configured base URLs to ASGI apps (by default, the mock API)."""

    def __init__(self, apps: dict[str, Any] | None = None):
        self.apps = apps or {"mock-services:8080": mocks.create_app(api_key="")}
        self.calls: list[dict] = []

    def request(self, method: str, url: str, query: dict | None = None, headers: dict | None = None) -> tuple[int, Any]:
        parts = urlsplit(url)
        app = self.apps.get(parts.netloc)
        if app is None:
            raise ServiceError(f"getaddrinfo ENOTFOUND {parts.hostname} (no fake registered for {parts.netloc})")
        qs = parts.query + ("&" if parts.query and query else "") + urlencode(query or {})
        resp = asgi.request(app, method, f"{parts.path}?{qs}" if qs else parts.path, headers=headers)
        self.calls.append({"method": method, "url": url, "query": query or {}, "status": resp.status})
        return resp.status, resp.json()


@dataclass
class Faults:
    """Make a node's service call fail ``n`` times: ``Faults({"Append CRM row": 2})``."""
    remaining: dict[str, int] = field(default_factory=dict)
    message: str = "503 Service Unavailable"

    def check(self, node_name: str) -> None:
        if self.remaining.get(node_name, 0) > 0:
            self.remaining[node_name] -= 1
            raise ServiceError(f"{self.message} (injected fault)")


# ------------------------------------------------------------------------------------------------- fake LLM

def _line(prompt: str, label: str) -> str:
    m = re.search(rf"^{re.escape(label)}:\s*(.*)$", prompt, re.M)
    return m.group(1).strip() if m else ""


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


def _any(text: str, words) -> bool:
    return any(w in text for w in words)


LEAD_RULES = [
    ("spam", ("seo ", "backlink", "crypto", "guaranteed traffic", "rank #1", "web design services")),
    ("not_a_fit", ("internship", "student", "job opening", "my resume", "partnership", "college project")),
    ("support", ("existing customer", "not working", "error message", "our invoice", "login issue")),
    ("purchase", ("quote", "pricing", "proposal", "statement of work", "hire your team", "build us", "need a team",
                  "ready to start", "contract")),
    ("evaluation", ("exploring", "evaluating", "comparing", "case studies", "learn more", "options for")),
]


def classify_lead(prompt: str) -> dict:
    msg = _line(prompt, "Message").lower()
    intent = next((label for label, words in LEAD_RULES if _any(msg, words)), "evaluation")
    budget = _num(_line(prompt, "Budget (USD)"))
    band = "none" if budget <= 0 else "under_5k" if budget < 5000 else "5k_25k" if budget <= 25000 else "over_25k"
    if _any(msg, ("asap", "urgent", "this week", "by friday", "within two weeks", "hard deadline")):
        urgency = "high"
    elif _any(msg, ("this quarter", "next month", "soon", "in q1")):
        urgency = "medium"
    else:
        urgency = "low"
    size = int(_num(_line(prompt, "Company size")))
    summary = f"{intent.replace('_', ' ').capitalize()} enquiry from a {size}-person company"
    summary += f" with a stated budget of ${budget:,.0f}." if budget > 0 else " with no stated budget."
    return {"intent": intent, "budget_band": band, "urgency": urgency, "summary": summary[:200]}


CATEGORY_BY_ARTICLE = {"KB-101": "billing", "KB-106": "billing", "KB-109": "billing", "KB-102": "shipping",
                       "KB-103": "shipping", "KB-107": "shipping", "KB-108": "shipping", "KB-104": "account",
                       "KB-105": "account", "KB-110": "technical"}


def triage_ticket(prompt: str, call_tool: Callable[[str, dict], Any], history: list[dict]) -> dict:
    subject = _line(prompt, "Subject")
    body = prompt.split("\n\n", 1)[1] if "\n\n" in prompt else ""
    text = f"{subject} {body}".lower()

    kb = call_tool("Search knowledge base", {"q": subject or body[:80]})
    results = (kb or {}).get("results", []) if isinstance(kb, dict) else []
    top = results[0] if results else None
    confidence = float(top["score"]) if top else 0.0

    order_id, order = "", None
    m = re.search(r"\bORD-\d{5}\b", f"{subject} {body}", re.I)
    if m:
        order_id = m.group(0).upper()
        order = call_tool("Look up order", {"order_id": order_id})
        if not isinstance(order, dict) or "order_id" not in order:
            order = None
            confidence = min(confidence, 0.5)

    category = CATEGORY_BY_ARTICLE.get(top["id"], "other") if top else "other"
    flags = []
    if _any(text, ("chargeback", "lawyer", "legal action", "fraud")):
        priority = "urgent"
        flags.append("legal or chargeback language")
    elif _any(text, ("hacked", "someone logged in", "unauthorised", "unauthorized")):
        priority = "urgent"
        flags.append("possible account compromise")
    elif (order and order["status"] in ("lost", "delayed")) or "refund" in text or history:
        priority = "high"
    elif _any(text, ("how do i", "quick question", "where can i")):
        priority = "low"
    else:
        priority = "normal"
    if history:
        flags.append(f"repeat contact ({len(history)} earlier)")
    if _any(text, ("refund", "money back")):
        flags.append("refund request")
    if order_id and order is None:
        flags.append(f"order {order_id} not found")
    if category == "other":
        flags.append("no matching article")
    needs_human = priority == "urgent" or any(f.startswith(("refund", "order", "no matching")) for f in flags)

    cited = [r["id"] for r in results if r["score"] >= 0.5][:2]
    parts = [f"Thanks for contacting us about \"{subject}\"."]
    if top:
        parts.append(top["body"])
    if order:
        parts.append(f"Your order {order['order_id']} is currently {order['status']} with {order['carrier']} "
                     f"(expected delivery {order['expected_delivery']}).")
    reply = "Hi,\n\n" + " ".join(parts) + "\n\nBest regards,\nSupport team"
    reason = "; ".join(flags) if flags else f"answered by {top['id']}" if top else "no sources"
    return {"category": category, "priority": priority, "confidence": round(confidence, 2),
            "needs_human": needs_human, "reason": reason[:300], "draft_reply": reply[:1500],
            "cited_articles": cited, "order_id": order_id}


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_meeting(prompt: str) -> dict:
    now = _dt(_line(prompt, "Current time"))
    req = _line(prompt, "Request")
    text = req.lower()
    m = re.search(r"(\d+)\s*(?:min|minute)", text)
    duration = int(m.group(1)) if m else 60 if _any(text, ("an hour", "1 hour", "one hour")) else 30
    duration = max(15, min(120, duration))
    day = None
    if "tomorrow" in text:
        day = (now + timedelta(days=1)).date()
    elif "next week" in text:
        day = (now + timedelta(days=7 - now.weekday())).date()
    else:
        for i, name in enumerate(WEEKDAYS):
            if name in text:
                ahead = (i - now.weekday()) % 7 or 7
                day = (now + timedelta(days=ahead)).date()
                break
    if day is None:
        start = end = now
        clarify = True
    else:
        lo, hi = (9, 12) if "morning" in text else (13, 17) if "afternoon" in text else (9, 17)
        start = datetime(day.year, day.month, day.day, lo, tzinfo=timezone.utc)
        end = datetime(day.year, day.month, day.day, hi, tzinfo=timezone.utc)
        if "next week" in text:
            end = end + timedelta(days=4)
        clarify = False
    t = re.search(r"(?:to discuss|to review|about|for)\s+(.+?)(?:[.,]|$)", req, re.I)
    topic = (t.group(1) if t else "Meeting").strip().capitalize()[:120]
    iso = lambda d: d.isoformat().replace("+00:00", "Z")  # noqa: E731
    return {"duration_minutes": duration, "window_start": iso(start), "window_end": iso(end), "topic": topic,
            "needs_clarification": clarify}


@dataclass
class LLMRequest:
    workflow: str
    node: str
    model: str
    system: str
    prompt: str
    schema: dict | None
    tools: list[str]
    history: list[dict]
    call_tool: Callable[[str, dict], Any]


class RuleBasedLLM:
    """Deterministic stand-in for the chat model, dispatched on the output schema's ``title``."""

    def __init__(self):
        self.calls: list[dict] = []
        self.handlers: dict[str, Callable[[LLMRequest], dict]] = {
            "lead_classification": lambda r: classify_lead(r.prompt),
            "ticket_triage": lambda r: triage_ticket(r.prompt, r.call_tool, r.history),
            "meeting_request": lambda r: parse_meeting(r.prompt),
        }

    def complete(self, req: LLMRequest) -> dict:
        title = (req.schema or {}).get("title", "")
        if title not in self.handlers:
            raise ServiceError(f"RuleBasedLLM has no handler for schema {title!r}")
        out = self.handlers[title](req)
        self.calls.append({"workflow": req.workflow, "node": req.node, "schema": title})
        return out


# ------------------------------------------------------------------------------------------------- bundle

def default_reviewer(context: dict) -> dict | None:
    """The on-call reviewer in the demo: approves cited, non-urgent drafts; takes the rest by hand."""
    out = context.get("output", {})
    if out.get("priority") == "urgent" or not out.get("cited_articles"):
        return {"decision": "reject"}
    return {"decision": "approve"}


DEFAULT_ENV = {
    "CRM_SHEET_ID": "sheet-crm", "SUPPORT_LOG_SHEET_ID": "sheet-support", "ERROR_LOG_SHEET_ID": "sheet-errors",
    "HELPDESK_API_URL": "http://mock-services:8080", "METRICS_API_URL": "http://mock-services:8080",
    "SCHEDULING_CALENDAR_ID": "team-calendar@example.com",
}


@dataclass
class Services:
    sheets: FakeSheets = field(default_factory=FakeSheets)
    gmail: FakeGmail = field(default_factory=FakeGmail)
    slack: FakeSlack = field(default_factory=FakeSlack)
    calendar: FakeCalendar = field(default_factory=FakeCalendar)
    http: FakeHttp = field(default_factory=FakeHttp)
    llm: RuleBasedLLM = field(default_factory=RuleBasedLLM)
    faults: Faults = field(default_factory=Faults)
    reviewer: Callable[[dict], dict | None] = default_reviewer
    memory: dict[str, list[dict]] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ENV))
    # credential name -> headers the simulator adds (n8n would add them from the stored credential)
    credential_headers: dict[str, dict[str, str]] = field(default_factory=lambda: {
        "Helpdesk API key": {"X-API-Key": "sim"}, "Metrics API key": {"X-API-Key": "sim"}})
