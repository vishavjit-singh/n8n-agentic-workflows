"""The shipped templates, run end to end in the simulator with fake services."""
import json

import pytest

from n8nkit import jsrunner, samples
from n8nkit.engine import Simulator
from n8nkit.fakes import Services

pytestmark = pytest.mark.skipif(not jsrunner.node_available(), reason="Code nodes need Node.js")

HOT = {"name": "Asha Rao", "email": "Asha.Rao@northwind.example", "company": "Northwind Labs", "company_size": 250,
       "budget_usd": 40000, "message": "We need a quote to build us a reporting dashboard. We need this ASAP.",
       "submitted_at": "2026-11-16T08:00:00Z"}


@pytest.fixture
def sim(workflows):
    return Simulator(workflows, Services())


def test_hot_lead_path_response_and_side_effects(sim):
    e = sim.webhook("01-lead-intake-qualifier", HOT)
    assert e.status == "success"
    assert e.response == {"status": 202, "body": {"status": "accepted", "lead_id": e.output("Score lead")[0]["json"]
                                                  ["lead_id"], "tier": "hot"}}
    s = sim.services
    row = s.sheets.rows("Leads")[0]
    assert row["email"] == "asha.rao@northwind.example" and row["tier"] == "hot" and row["score"] == 100
    assert s.slack.in_channel("#sales-hot-leads") and s.gmail.drafts[0]["to"] == "asha.rao@northwind.example"
    assert s.gmail.sent == []                            # replies to leads are drafts for a person to send
    assert e.path.index("Accept lead") < e.path.index("Route by tier")   # caller gets 202 before the fan-out


def test_tiers_follow_the_score_thresholds(sim):
    cases = {"hot": HOT,
             "warm": {**HOT, "email": "w@x.example", "budget_usd": 0, "company_size": 40,
                      "message": "Could you send pricing for a chatbot?"},
             "cold": {**HOT, "email": "c@x.example", "message": "I'm a final-year student looking for an internship."}}
    for tier, body in cases.items():
        e = sim.webhook("01-lead-intake-qualifier", body)
        scored = e.output("Score lead")[0]["json"]
        assert scored["tier"] == tier, scored
        assert (scored["score"] >= 70) == (tier == "hot") and (scored["score"] < 40) == (tier == "cold")


def test_resubmission_is_idempotent_and_invalid_is_rejected(sim):
    first = sim.webhook("01-lead-intake-qualifier", HOT)
    again = sim.webhook("01-lead-intake-qualifier", {**HOT, "email": HOT["email"].upper(),
                                                     "submitted_at": "2026-11-17T10:00:00Z"})
    assert again.response["status"] == 200 and again.response["body"]["status"] == "duplicate"
    assert again.response["body"]["lead_id"] == first.response["body"]["lead_id"]
    assert len(sim.services.sheets.rows("Leads")) == 1 and len(sim.services.llm.calls) == 1
    bad = sim.webhook("01-lead-intake-qualifier", {"name": "x", "message": "hi"})
    assert bad.response["status"] == 400 and not bad.executed("Classify lead")


def test_support_agent_uses_tools_and_routes_by_confidence(sim):
    t = {"ticket_id": "T-1", "email": "c@shop.example", "subject": "Reset password",
         "message": "How do I reset my password? I forgot it."}
    e = sim.webhook("02-support-triage-agent", t)
    assert e.response["status"] == 202                   # onReceived: answered before the agent runs
    out = e.output("Triage agent")[0]["json"]["output"]
    assert out["category"] == "account" and out["cited_articles"] == ["KB-104"] and not out["needs_human"]
    assert e.executed("Send reply") and not e.executed("Ask for approval")
    assert [c["tool"] for c in e.tool_calls] == ["Search knowledge base"]


def test_support_escalation_waits_for_reviewer(workflows):
    decisions = []

    def reviewer(ctx):
        decisions.append(ctx["output"]["priority"])
        return None                                       # nobody clicks: the 4-hour limit runs out

    sim = Simulator(workflows, Services(reviewer=reviewer))
    t = {"ticket_id": "T-9", "email": "d@shop.example", "subject": "Charged twice, refund",
         "message": "I was charged twice for ORD-10003. I want my money back."}
    e = sim.webhook("02-support-triage-agent", t)
    assert [c["tool"] for c in e.tool_calls] == ["Search knowledge base", "Look up order"]
    msg = sim.services.slack.in_channel("#support-escalations")[0]["text"]
    assert f"/webhook-waiting/{e.id}?decision=approve" in msg
    assert decisions == ["high"] and e.executed("Mark for human") and not e.executed("Send approved reply")
    assert sim.services.sheets.rows("Triage log")[0]["outcome"] == "assigned_to_human"


def test_memory_marks_repeat_contacts(sim):
    t = {"ticket_id": "T-1", "email": "same@shop.example", "subject": "Delivery time",
         "message": "Quick question: how long does standard shipping take?"}
    first = sim.webhook("02-support-triage-agent", t)
    second = sim.webhook("02-support-triage-agent", {**t, "ticket_id": "T-2"})
    assert first.output("Triage agent")[0]["json"]["output"]["priority"] == "low"
    assert second.output("Triage agent")[0]["json"]["output"]["priority"] == "high"


def test_meeting_scheduler_books_free_slots_and_offers_alternatives(sim):
    cal = sim.services.env["SCHEDULING_CALENDAR_ID"]
    sim.services.calendar.create(cal, "2026-11-19T13:00:00Z", "2026-11-19T14:00:00Z", "busy", [])
    req = {"requester_email": "r@client.example", "request_text": "An hour on Thursday afternoon to review the plan"}
    booked = sim.webhook("03-meeting-scheduler", req)
    assert booked.response == {"status": 201, "body": {"status": "booked", "start": "2026-11-19T14:00:00.000Z",
                                                      "end": "2026-11-19T15:00:00.000Z"}}
    for _ in range(2):                                    # fill the rest of Thursday afternoon
        sim.webhook("03-meeting-scheduler", req)
    full = sim.webhook("03-meeting-scheduler", req)
    assert full.response["status"] == 200 and len(full.response["body"]["alternatives"]) == 3
    vague = sim.webhook("03-meeting-scheduler", {**req, "request_text": "a quick call sometime"})
    assert vague.response["status"] == 422


def test_kpi_digest_loops_over_teams(sim):
    e = sim.schedule("04-daily-kpi-digest")
    assert e.status == "success" and e.path.count("Email team lead") == 4
    assert sorted(m["to"] for m in sim.services.gmail.sent) == sorted(f"{t}.lead@example.com"
                                                                     for t in ("north", "south", "east", "west"))
    summary = e.output("Build Slack summary")[0]["json"]
    assert summary["teams_emailed"] == 4 and summary["date"] == "2026-11-15"
    calls = sim.services.http.calls
    assert {c["query"]["date"] for c in calls} == {"2026-11-15"} and all(c["status"] == 200 for c in calls)


def test_synthetic_samples_are_deterministic_and_use_reserved_domains():
    assert json.dumps(samples.leads()) == json.dumps(samples.leads())
    emails = [p.get("email", "") for p in samples.leads()] + [t["email"] for t in samples.tickets()]
    assert all(e == "" or e.lower().endswith((".example", "example.com")) for e in emails)
