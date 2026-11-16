"""The demo scenarios: SYNTHETIC payloads pushed through each workflow in the simulator, summarised for the README."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import samples
from .engine import Execution, Simulator
from .fakes import Faults, Services
from .model import Workflow

LEAD, SUPPORT, MEETING, KPI, ERRORS = ("01-lead-intake-qualifier", "02-support-triage-agent", "03-meeting-scheduler",
                                       "04-daily-kpi-digest", "00-error-handler")
MONDAY_9AM = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)


def run_leads(workflows: list[Workflow], n: int = 50) -> tuple[dict, list[Execution]]:
    sim = Simulator(workflows, Services(), MONDAY_9AM)
    payloads = samples.leads(n)
    runs = [sim.webhook(LEAD, p) for p in payloads]
    outcome = Counter()
    scores: dict[str, list[int]] = {"hot": [], "warm": [], "cold": []}
    intents = Counter()
    for e in runs:
        body = (e.response or {}).get("body") or {}
        if e.status != "success":
            outcome["error"] += 1
        elif body.get("status") == "accepted":
            outcome[body["tier"]] += 1
            scored = e.output("Score lead")[0]["json"]
            scores[body["tier"]].append(scored["score"])
            intents[scored["intent"]] += 1
        else:
            outcome[body.get("status", "unknown")] += 1
    s = sim.services
    summary = {
        "submissions": len(payloads), "outcomes": dict(outcome), "scores": scores, "intents": dict(intents),
        "crm_rows": len(s.sheets.rows("Leads")), "slack_alerts": len(s.slack.in_channel("#sales-hot-leads")),
        "gmail_drafts": len(s.gmail.drafts), "emails_sent": len(s.gmail.sent), "llm_calls": len(s.llm.calls),
        "response_codes": dict(Counter(str(e.response["status"]) for e in runs)),
    }
    return summary, runs


def run_support(workflows: list[Workflow], n: int = 24) -> tuple[dict, list[Execution]]:
    sim = Simulator(workflows, Services(), MONDAY_9AM)
    runs = [sim.webhook(SUPPORT, t) for t in samples.tickets(n)]
    log = sim.services.sheets.rows("Triage log")
    tools = Counter(c["tool"] for e in runs for c in e.tool_calls)
    categories = Counter(r["category"] for r in log)
    by_cat: dict[str, Counter] = {}
    for r in log:
        by_cat.setdefault(r["category"], Counter())[r["outcome"]] += 1
    escalated = [e for e in runs if e.executed("Ask for approval")]
    summary = {
        "tickets": len(runs), "outcomes": dict(Counter(r["outcome"] for r in log)), "categories": dict(categories),
        "outcomes_by_category": {k: dict(v) for k, v in sorted(by_cat.items())},
        "escalated": len(escalated), "tool_calls": dict(tools),
        "tool_calls_per_ticket": round(sum(tools.values()) / max(1, len(runs)), 2),
        "repeat_contacts": sum(1 for e in runs if "memory: 0 earlier" not in _note(e, "Triage agent")),
        "emails_sent": len(sim.services.gmail.sent), "slack_reviews": len(sim.services.slack.messages),
        "errors": sum(e.status != "success" for e in runs),
        "response_codes": dict(Counter(str(e.response["status"]) for e in runs)),
    }
    return summary, runs


def _note(e: Execution, node: str) -> str:
    return next((s.note for s in e.steps if s.node == node), "")


def run_meetings(workflows: list[Workflow]) -> tuple[dict, list[Execution]]:
    services = Services()
    cal = services.env["SCHEDULING_CALENDAR_ID"]
    for start, end, title in samples.calendar_load():
        services.calendar.create(cal, start, end, title, [])
    preexisting = len(services.calendar.events)
    sim = Simulator(workflows, services, MONDAY_9AM)
    runs = [sim.webhook(MEETING, m) for m in samples.meetings()]
    statuses = Counter((e.response or {}).get("body", {}).get("status", "error") for e in runs)
    summary = {"requests": len(runs), "outcomes": dict(statuses), "busy_slots_before": preexisting,
               "events_created": len(services.calendar.events) - preexisting,
               "emails_sent": len(services.gmail.sent),
               "response_codes": dict(Counter(str(e.response["status"]) for e in runs))}
    return summary, runs


def run_kpi(workflows: list[Workflow], days: int = 5) -> tuple[dict, list[Execution]]:
    services = Services()
    per_day, runs = [], []
    for d in range(days):
        sim = Simulator(workflows, services, MONDAY_9AM.replace(hour=8) + timedelta(days=d))
        before = len(services.gmail.sent)
        e = sim.schedule(KPI)
        runs.append(e)
        summary_item = e.output("Build Slack summary")[0]["json"]
        per_day.append({"run_at": sim.now.date().isoformat(), "metrics_date": summary_item["date"],
                        "emails": len(services.gmail.sent) - before, "teams_flagged": summary_item["teams_flagged"]})
    return {"runs": days, "per_day": per_day, "emails_sent": len(services.gmail.sent),
            "slack_posts": len(services.slack.in_channel("#kpi-digest")),
            "loop_iterations": sum(1 for e in runs for s in e.steps if s.node == "Email team lead")}, runs


def run_failure_drill(workflows: list[Workflow]) -> tuple[dict, list[Execution]]:
    lead = samples.leads()[0]
    out = {}
    runs = []
    for label, failures in (("transient", 2), ("persistent", 5)):
        services = Services(faults=Faults({"Append CRM row": failures}))
        sim = Simulator(workflows, services, MONDAY_9AM)
        e = sim.webhook(LEAD, lead)
        runs.append(e)
        step = next(s for s in e.steps if s.node == "Append CRM row")
        out[label] = {"injected_failures": failures, "attempts": step.attempts, "status": e.status,
                      "response": e.response["status"],
                      "error_workflow_ran": e.error_execution is not None,
                      "alerts": [m["channel"] for m in services.slack.messages if m["channel"] == "#automation-alerts"],
                      "error_log_rows": len(services.sheets.rows("Errors"))}
    return out, runs


def run_all(workflows: list[Workflow], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    traces = out_dir / "traces"
    traces.mkdir(exist_ok=True)
    result = {}
    for key, fn in (("leads", run_leads), ("support", run_support), ("meetings", run_meetings), ("kpi", run_kpi),
                    ("failure_drill", run_failure_drill)):
        summary, runs = fn(workflows)
        result[key] = summary
        (traces / f"{key}.jsonl").write_text("\n".join(json.dumps(e.to_dict()) for e in runs) + "\n")
        result[key]["sample_trace"] = _pick(key, runs).trace_text()
        if key == "failure_drill":
            result[key]["sample_error_trace"] = runs[1].error_execution.trace_text()
    (out_dir / "simulation.json").write_text(json.dumps(result, indent=2))
    return result


def _pick(key: str, runs: list[Execution]) -> Execution:
    """A representative run for the README trace."""
    if key == "leads":
        return next(e for e in runs if (e.response or {}).get("body", {}).get("tier") == "hot")
    if key == "support":
        return next((e for e in runs if e.executed("Send approved reply")), runs[0])
    if key == "meetings":
        return next((e for e in runs if (e.response or {}).get("status") == 200), runs[0])
    if key == "failure_drill":
        return runs[1]
    return runs[0]
