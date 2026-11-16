import pytest

from n8nkit.examples import broken_lead_workflow
from n8nkit.lint import ERROR, RULES, lint
from n8nkit.model import Workflow


def by_slug(wfs, slug):
    return next(w for w in wfs if w.slug == slug)


def rules_hit(findings, slug):
    return {f.rule for f in findings if f.workflow == slug}


def test_shipped_workflows_are_clean(workflows):
    assert len(workflows) == 5
    assert lint(workflows) == []


def test_broken_copy_trips_the_expected_rules(workflows):
    broken = broken_lead_workflow(by_slug(workflows, "01-lead-intake-qualifier"))
    hit = rules_hit(lint(workflows + [broken]), broken.slug)
    assert hit == {"unique-names", "connections", "orphans", "webhook-paths", "secrets", "emails", "credentials",
                   "retries", "error-workflow", "node-versions", "expressions", "export-settings"}
    assert hit < set(RULES)


def _mutate(fresh, slug, fn):
    wf = by_slug(fresh, slug)
    fn(wf.raw)
    return [Workflow(w.raw, w.path) for w in fresh]       # rebuild indexes after mutation


def _node(raw, name):
    return next(n for n in raw["nodes"] if n["name"] == name)


MUTATIONS = {
    # rule id: (workflow, mutation)
    "secrets": ("04-daily-kpi-digest", lambda r: _node(r, "Fetch order metrics")["parameters"].update(
        {"sendHeaders": True, "headerParameters": {"parameters": [{"name": "X-API-Key", "value": "abc123"}]}})),
    "emails": ("00-error-handler", lambda r: _node(r, "Alert Slack")["parameters"].update(
        {"text": "ping oncall@my-company.io"})),
    "retries": ("03-meeting-scheduler", lambda r: _node(r, "Check calendar").pop("retryOnFail")),
    "ai-wiring": ("02-support-triage-agent", lambda r: r["connections"].pop("OpenAI Chat Model")),
    "respond-node": ("03-meeting-scheduler", lambda r: r["connections"]["Confirm by email"].pop("main")),
    "orphans": ("04-daily-kpi-digest", lambda r: r["connections"].pop("Combine metrics")),
    "trigger": ("00-error-handler", lambda r: _node(r, "Error Trigger").update({"disabled": True})),
    "structure": ("04-daily-kpi-digest", lambda r: _node(r, "Loop over teams").pop("typeVersion")),
}


@pytest.mark.parametrize("rule_id", list(MUTATIONS))
def test_each_rule_catches_its_mutation(fresh, rule_id):
    slug, fn = MUTATIONS[rule_id]
    findings = lint(_mutate(fresh, slug, fn))
    assert rule_id in rules_hit(findings, slug), [str(f) for f in findings]


def test_secret_patterns_and_allowed_placeholders(fresh):
    slack_token = "xox" + "b-" + "1234567890-abcdefghij"         # assembled so no token-shaped literal is committed
    wfs = _mutate(fresh, "00-error-handler", lambda r: _node(r, "Alert Slack")["parameters"].update(
        {"text": f"token {slack_token}, reply to team@example.com or a@b.test"}))
    msgs = [f.message for f in lint(wfs) if f.workflow == "00-error-handler"]
    assert any("Slack token" in m for m in msgs)
    assert not any("example.com" in m or "b.test" in m for m in msgs)   # reserved domains are fine


def test_error_workflow_must_exist_and_start_with_error_trigger(fresh):
    wfs = _mutate(fresh, "01-lead-intake-qualifier", lambda r: r["settings"].update({"errorWorkflow": "Nope"}))
    assert any(f.rule == "error-workflow" and "not in this set" in f.message for f in lint(wfs))
    only_lead = [w for w in fresh if w.slug == "01-lead-intake-qualifier"]
    assert any(f.rule == "error-workflow" and f.severity == ERROR for f in lint(only_lead))
