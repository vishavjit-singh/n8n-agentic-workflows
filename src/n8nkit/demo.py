"""``make demo``: validate → render → simulate → charts → docs → summary."""
from __future__ import annotations

import json
from pathlib import Path

from . import charts, docsgen, lint, render, samples, scenarios
from .engine import Simulator
from .examples import broken_lead_workflow
from .fakes import Services
from .model import Workflow, load_all


def doc_samples(workflows: list[Workflow]) -> dict:
    """One simulated request per webhook workflow, shown in its generated doc."""
    out = {}
    services = Services()
    sim = Simulator(workflows, services, scenarios.MONDAY_9AM)
    picks = {scenarios.LEAD: samples.leads()[0], scenarios.SUPPORT: samples.tickets()[0],
             scenarios.MEETING: samples.meetings()[1]}
    for slug, payload in picks.items():
        out[slug] = (payload, sim.webhook(slug, payload))
    out[scenarios.KPI] = ({}, sim.schedule(scenarios.KPI))
    return out


def run(workflow_dir: Path, images: Path, docs_dir: Path, reports: Path, log=print) -> dict:
    workflows = load_all(workflow_dir)
    reports.mkdir(parents=True, exist_ok=True)

    findings = lint.lint(workflows)
    errors = [f for f in findings if f.severity == lint.ERROR]
    log(f"validate: {len(workflows)} workflows, {len(lint.RULES)} rules, {len(errors)} errors, "
        f"{len(findings) - len(errors)} warnings")
    for f in findings:
        log(f"  {f}")
    lead = next(w for w in workflows if w.slug == scenarios.LEAD)
    broken = broken_lead_workflow(lead)
    broken_findings = [f for f in lint.lint(workflows + [broken]) if f.workflow == broken.slug]
    matrix = lint.matrix(workflows, findings)
    matrix.update(lint.matrix([broken], broken_findings))
    log(f"validate (broken copy): {len(broken_findings)} findings across "
        f"{sum(v != 'pass' for v in matrix[broken.slug].values())} rules")
    (reports / "lint.json").write_text(json.dumps({"findings": [f.as_dict() for f in findings],
                                                   "broken_copy": [f.as_dict() for f in broken_findings],
                                                   "matrix": matrix}, indent=2))
    if errors:
        raise SystemExit("lint errors in the shipped workflows; fix them before rendering")

    for p in render.render_all(workflows, images / "workflows"):
        log(f"render: {p}")

    result = scenarios.run_all(workflows, reports)
    result["lint"] = {"workflows": len(workflows), "rules": len(lint.RULES), "errors": len(errors),
                      "warnings": len(findings) - len(errors), "broken_copy_findings": len(broken_findings),
                      "broken_copy_rules_failed": sum(v != "pass" for v in matrix[broken.slug].values())}
    result["workflows"] = {w.slug: {"id": w.id, "name": w.name, "nodes": len([n for n in w.nodes if not n.is_note])}
                           for w in workflows}
    (reports / "simulation.json").write_text(json.dumps(result, indent=2))

    for p in (charts.architecture(images), charts.lint_matrix(matrix, images),
              charts.lead_routing(result["leads"], images), charts.support_outcomes(result["support"], images)):
        log(f"chart: {p}")
    for p in docsgen.write_docs(workflows, docs_dir, doc_samples(workflows)):
        log(f"docs: {p}")
    return result


def summary_text(r: dict) -> str:
    L, S, M, K, F = r["leads"], r["support"], r["meetings"], r["kpi"], r["failure_drill"]
    lines = [
        "=== demo summary (SYNTHETIC data, fake services) ===",
        f"lint: {r['lint']['workflows']} workflows × {r['lint']['rules']} rules → {r['lint']['errors']} errors, "
        f"{r['lint']['warnings']} warnings; broken copy → {r['lint']['broken_copy_findings']} findings in "
        f"{r['lint']['broken_copy_rules_failed']} rules",
        f"leads: {L['submissions']} posts → {json.dumps(L['outcomes'])}; CRM rows {L['crm_rows']}, Slack alerts "
        f"{L['slack_alerts']}, Gmail drafts {L['gmail_drafts']}, emails sent {L['emails_sent']}, "
        f"LLM calls {L['llm_calls']}",
        f"support: {S['tickets']} tickets → {json.dumps(S['outcomes'])}; escalated {S['escalated']}, "
        f"tool calls {json.dumps(S['tool_calls'])} ({S['tool_calls_per_ticket']}/ticket), repeat contacts "
        f"{S['repeat_contacts']}, emails sent {S['emails_sent']}",
        f"meetings: {M['requests']} requests → {json.dumps(M['outcomes'])}; events created {M['events_created']} "
        f"(calendar had {M['busy_slots_before']} busy slots)",
        f"kpi digest: {K['runs']} runs → {K['emails_sent']} team emails, {K['slack_posts']} Slack posts; teams "
        f"flagged per day {[d['teams_flagged'] for d in K['per_day']]}",
        f"failure drill: transient {F['transient']['injected_failures']} failures → {F['transient']['status']} on "
        f"attempt {F['transient']['attempts']}; persistent → {F['persistent']['status']} after "
        f"{F['persistent']['attempts']} attempts, HTTP {F['persistent']['response']}, error workflow ran: "
        f"{F['persistent']['error_workflow_ran']}",
    ]
    return "\n".join(lines)
