"""Node semantics in the simulator, on small hand-built workflows."""
import pytest

from n8nkit import jsrunner
from n8nkit.engine import Simulator
from n8nkit.fakes import Faults, Services

from .conftest import make_workflow

needs_node = pytest.mark.skipif(not jsrunner.node_available(), reason="Node.js not installed")


def cond(left, op_type, op, right="", single=False):
    c = {"id": "c", "leftValue": left, "rightValue": right, "operator": {"type": op_type, "operation": op}}
    if single:
        c["operator"]["singleValue"] = True
    return c


def block(*conds, combinator="and"):
    return {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
            "conditions": list(conds), "combinator": combinator}


def run(wf, items, services=None):
    sim = Simulator([wf], services or Services())
    return sim.run(wf, wf.nodes[0].name, [{"json": j} for j in items])


def test_if_routes_items_and_enforces_strict_types():
    wf = make_workflow([("Start", "manualTrigger", {}),
                        ("Score ok?", "if", {"conditions": block(cond("={{ $json.score }}", "number", "gte", 70))},
                         {"typeVersion": 2}),
                        ("Yes", "noOp", {}), ("No", "noOp", {})],
                       [("Start", "Score ok?"), ("Score ok?", "Yes", 0), ("Score ok?", "No", 1)])
    e = run(wf, [{"score": 90}, {"score": 10}, {"score": 70}])
    assert len(e.output("Yes")) == 2 and len(e.output("No")) == 1
    bad = run(wf, [{"score": "90"}])                     # a string where a number is required
    assert bad.status == "error" and "Wrong type" in bad.error["message"]


def test_switch_fallback_and_set_types():
    rules = {"values": [{"conditions": block(cond("={{ $json.t }}", "string", "equals", "a")), "outputKey": "a"}]}
    wf = make_workflow([("Start", "manualTrigger", {}),
                        ("Route", "switch", {"rules": rules, "options": {"fallbackOutput": "extra"}},
                         {"typeVersion": 3}),
                        ("Shape", "set", {"assignments": {"assignments": [
                            {"id": "1", "name": "n", "value": "={{ $json.v }}", "type": "number"},
                            {"id": "2", "name": "label", "value": "=item {{ $json.v }}", "type": "string"},
                            {"id": "3", "name": "flag", "value": "={{ $json.v > 1 }}", "type": "boolean"}]}},
                         {"typeVersion": 3.4}),
                        ("Other", "noOp", {})],
                       [("Start", "Route"), ("Route", "Shape", 0), ("Route", "Other", 1)])
    e = run(wf, [{"t": "a", "v": "2"}, {"t": "b", "v": 1}])
    assert e.output("Shape") == [{"json": {"n": 2, "label": "item 2", "flag": True}}]
    assert e.output("Other") == [{"json": {"t": "b", "v": 1}}]


def test_merge_waits_for_both_inputs_and_combines_by_position():
    wf = make_workflow([("Start", "manualTrigger", {}),
                        ("A", "set", {"assignments": {"assignments": [{"id": "1", "name": "a", "value": 1,
                                                                       "type": "number"}]}}, {"typeVersion": 3.4}),
                        ("B", "set", {"assignments": {"assignments": [{"id": "1", "name": "b", "value": 2,
                                                                       "type": "number"}]}}, {"typeVersion": 3.4}),
                        ("Merge", "merge", {"mode": "combine", "combineBy": "combineByPosition"}, {"typeVersion": 3})],
                       [("Start", "A"), ("Start", "B"), ("A", "Merge", 0, 0), ("B", "Merge", 0, 1)])
    e = run(wf, [{}])
    assert e.output("Merge") == [{"json": {"a": 1, "b": 2}}]
    assert e.path.count("Merge") == 1


def test_loop_over_items_visits_each_batch_then_emits_done():
    wf = make_workflow([("Start", "manualTrigger", {}),
                        ("Loop", "splitInBatches", {"batchSize": 2}, {"typeVersion": 3}),
                        ("Body", "noOp", {}), ("Done", "noOp", {})],
                       [("Start", "Loop"), ("Loop", "Done", 0), ("Loop", "Body", 1), ("Body", "Loop")])
    e = run(wf, [{"i": i} for i in range(5)])
    assert e.path.count("Body") == 3                     # batches of 2, 2, 1
    assert [it["json"]["i"] for it in e.output("Done")] == [0, 1, 2, 3, 4]


def test_retry_policy_then_error_workflow():
    handler = make_workflow([("Error Trigger", "errorTrigger", {}),
                             ("Alert", "slack", {"channelId": {"__rl": True, "value": "#alerts", "mode": "name"},
                                                 "text": "={{ $json.execution.lastNodeExecuted }} failed"},
                              {"typeVersion": 2.2})],
                            [("Error Trigger", "Alert")], wid="ErrHandlerShared")
    wf = make_workflow([("Start", "manualTrigger", {}),
                        ("Post", "slack", {"channelId": {"__rl": True, "value": "#x", "mode": "name"}, "text": "hi"},
                         {"typeVersion": 2.2, "retryOnFail": True, "maxTries": 3, "waitBetweenTries": 1000})],
                       [("Start", "Post")], settings={"executionOrder": "v1", "errorWorkflow": "ErrHandlerShared"})
    services = Services(faults=Faults({"Post": 2}))
    sim = Simulator([wf, handler], services)
    ok = sim.run(wf, "Start", [{"json": {}}])
    assert ok.status == "success" and ok.steps[-1].attempts == 3
    services.faults.remaining["Post"] = 3
    failed = sim.run(wf, "Start", [{"json": {}}])
    assert failed.status == "error" and failed.error["node"] == "Post"
    assert failed.error_execution.status == "success"
    assert services.slack.in_channel("#alerts")[-1]["text"] == "Post failed"


def test_on_error_continue_error_output():
    wf = make_workflow([("Start", "manualTrigger", {}),
                        ("Post", "slack", {"channelId": {"__rl": True, "value": "#x", "mode": "name"}, "text": "hi"},
                         {"typeVersion": 2.2, "onError": "continueErrorOutput"}),
                        ("Fallback", "noOp", {})],
                       [("Start", "Post"), ("Post", "Fallback", 1)])
    e = run(wf, [{}], Services(faults=Faults({"Post": 1})))
    assert e.status == "success" and "injected fault" in e.output("Fallback")[0]["json"]["error"]


@needs_node
def test_code_node_runs_javascript_with_n8n_shims():
    code = ("const prev = $('Seed').first().json;\n"
            "return $input.all().map((it, i) => ({ json: { v: it.json.v * prev.k, i, now: $now.toISO() } }));")
    wf = make_workflow([("Start", "manualTrigger", {}),
                        ("Seed", "set", {"assignments": {"assignments": [{"id": "1", "name": "k", "value": 10,
                                                                          "type": "number"}]},
                                         "includeOtherFields": True}, {"typeVersion": 3.4}),
                        ("JS", "code", {"jsCode": code}, {"typeVersion": 2})],
                       [("Start", "Seed"), ("Seed", "JS")])
    e = run(wf, [{"v": 1}, {"v": 2}])
    assert [it["json"]["v"] for it in e.output("JS")] == [10, 20]
    assert e.output("JS")[0]["json"]["now"] == "2026-11-16T09:00:00.000Z"
    with pytest.raises(jsrunner.CodeNodeError, match="boom"):
        jsrunner.run_code("throw new Error('boom')", [], "runOnceForAllItems", {}, "", {}, {}, {})
