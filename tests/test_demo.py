"""`make demo` end to end into a temporary directory, plus checks on the export format itself."""
import json

import pytest

from n8nkit import config, jsrunner
from n8nkit.catalog import CONNECTION_TYPES
from n8nkit.demo import run, summary_text


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    if not jsrunner.node_available():
        pytest.skip("Code nodes need Node.js")
    root = tmp_path_factory.mktemp("demo")
    result = run(config.WORKFLOWS, root / "images", root / "docs", root / "reports", log=lambda *_: None)
    return root, result


def test_demo_writes_every_artifact(demo):
    root, _ = demo
    names = {p.name for p in (root / "images").glob("*.png")}
    assert names == {"architecture.png", "lint_matrix.png", "lead_routing.png", "support_outcomes.png"}
    assert len(list((root / "images" / "workflows").glob("*.png"))) == 5
    assert len(list((root / "docs").glob("*.md"))) == 5
    sim = json.loads((root / "reports" / "simulation.json").read_text())
    assert set(sim) >= {"leads", "support", "meetings", "kpi", "failure_drill", "lint"}
    assert (root / "reports" / "traces" / "leads.jsonl").read_text().count("\n") == 50


def test_demo_numbers_are_consistent(demo):
    _, r = demo
    leads = r["leads"]
    assert sum(leads["outcomes"].values()) == leads["submissions"] == 50
    accepted = sum(leads["outcomes"].get(t, 0) for t in ("hot", "warm", "cold"))
    assert leads["crm_rows"] == leads["llm_calls"] == accepted
    assert leads["slack_alerts"] == leads["outcomes"]["hot"]
    assert leads["gmail_drafts"] == leads["outcomes"]["hot"] + leads["outcomes"]["warm"]
    assert leads["outcomes"]["duplicate"] == 3 and leads["outcomes"]["rejected"] == 2
    sup = r["support"]
    assert sum(sup["outcomes"].values()) == sup["tickets"] and sup["errors"] == 0
    assert sup["escalated"] == sup["slack_reviews"] == sup["tickets"] - sup["outcomes"]["auto_replied"]
    assert r["failure_drill"]["transient"]["status"] == "success"
    assert r["failure_drill"]["persistent"]["error_workflow_ran"] is True
    assert r["lint"]["errors"] == 0 and r["lint"]["broken_copy_rules_failed"] == 12
    assert "demo summary" in summary_text(r)


def test_docs_list_credentials_env_and_a_simulated_run(demo):
    root, _ = demo
    doc = (root / "docs" / "02-support-triage-agent.md").read_text()
    for needle in ("`openAiApi`", "Helpdesk API key", "`HELPDESK_API_URL`", "POST /webhook/support-ticket",
                   "Triage agent", "output schema `ticket_triage`", "Simulated run"):
        assert needle in doc, needle
    assert "Used as the error workflow by" in (root / "docs" / "00-error-handler.md").read_text()


def test_exports_follow_the_n8n_format(workflows):
    ids = set()
    for wf in workflows:
        raw = wf.raw
        assert raw["active"] is False and raw["settings"]["executionOrder"] == "v1"
        assert len(raw["id"]) == 16 and raw["id"].isalnum()
        for n in raw["nodes"]:
            assert isinstance(n["position"], list) and isinstance(n["typeVersion"], (int, float))
            assert n["id"] not in ids
            ids.add(n["id"])
            for ref in (n.get("credentials") or {}).values():
                assert set(ref) == {"id", "name"} and ref["id"] is None   # bound by name on import
        for by_type in raw["connections"].values():
            assert set(by_type) <= set(CONNECTION_TYPES)
        text = wf.path.read_text()
        assert "pinData\": {}" in text and "password" not in text.lower()
