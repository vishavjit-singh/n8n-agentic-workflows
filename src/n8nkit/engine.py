"""Local simulator: executes a workflow export's graph on a sample payload and records a run trace.

What is real: graph traversal in n8n's v1 order (one branch to completion before the next, top branch first),
IF/Switch/Set/Merge/Loop Over Items/Respond to Webhook semantics, expression evaluation, Code nodes (JavaScript run
by Node.js), node retry settings, ``onError`` and ``alwaysOutputData``, and the hand-off to ``settings.errorWorkflow``.
What is fake: every external service (see ``fakes.py``), including the LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from . import expressions as ex
from . import jsonschema, jsrunner
from .fakes import LLMRequest, ServiceError, Services
from .model import Node, Workflow

BASE_URL = "http://localhost:5678"
MAX_STEPS = 5000


class NodeError(RuntimeError):
    pass


class WorkflowError(RuntimeError):
    def __init__(self, node: str, message: str):
        super().__init__(f"{node}: {message}")
        self.node, self.message = node, message


@dataclass
class Step:
    step: int
    node: str
    type: str
    items_in: int
    items_out: list[int]
    attempts: int = 1
    status: str = "success"
    note: str = ""


@dataclass
class Execution:
    id: str
    workflow_id: str
    workflow_name: str
    mode: str
    status: str = "running"
    steps: list[Step] = field(default_factory=list)
    response: dict | None = None
    error: dict | None = None
    error_execution: "Execution | None" = None
    run_data: dict[str, list[list[list[dict]]]] = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)

    def output(self, node: str, index: int = 0) -> list[dict]:
        runs = self.run_data.get(node)
        return runs[-1][index] if runs and index < len(runs[-1]) else []

    def executed(self, node: str) -> bool:
        return node in self.run_data

    @property
    def path(self) -> list[str]:
        return [s.node for s in self.steps]

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k not in ("run_data", "error_execution")}
        if self.error_execution:
            d["error_execution"] = self.error_execution.to_dict()
        return d

    def trace_text(self) -> str:
        rows = [f"execution {self.id} · {self.workflow_name} · {self.status}"]
        for s in self.steps:
            out = "/".join(str(n) for n in s.items_out) or "-"
            extra = f" ×{s.attempts}" if s.attempts > 1 else ""
            rows.append(f"{s.step:>3}  {s.node:<24} {s.type:<22} in {s.items_in:<3} out {out:<7}{extra:<4} {s.note}")
        if self.response:
            rows.append(f"     response {self.response['status']}: {json.dumps(self.response['body'])}")
        return "\n".join(rows)


class NodeRef(ex.JSObject):
    """``$('Node Name')`` inside expressions."""

    def __init__(self, items: list[dict], index: int):
        last = items[min(index, len(items) - 1)] if items else ex.UNDEFINED
        super().__init__({"item": last, "first": lambda: items[0] if items else ex.UNDEFINED,
                          "last": lambda: items[-1] if items else ex.UNDEFINED, "all": lambda: items,
                          "isExecuted": True}, "$(node)")


class Context:
    def __init__(self, sim: "Simulator", wf: Workflow, node: Node, inputs: list[list[dict]], execution: Execution):
        self.sim, self.wf, self.node, self.inputs, self.execution = sim, wf, node, inputs, execution
        self.services = sim.services
        self.note = ""
        self.state: dict[str, Any] = {}

    @property
    def items(self) -> list[dict]:
        return self.inputs[0] if self.inputs else []

    def node_items(self, name: str) -> list[dict]:
        if name not in self.execution.run_data:
            raise ex.ExpressionError(f"Referenced node \"{name}\" is unexecuted")
        return [it for out in self.execution.run_data[name][-1] for it in out]

    def scope(self, index: int = 0) -> dict[str, Any]:
        items = self.items
        item = items[index] if index < len(items) else {"json": {}}
        now = ex.DateTimeValue(self.sim.now)
        return {
            "$json": item.get("json", {}),
            "$input": ex.JSObject({"item": item, "all": lambda: items, "first": lambda: items[0] if items else
                                   ex.UNDEFINED, "last": lambda: items[-1] if items else ex.UNDEFINED}, "$input"),
            "$": lambda name: NodeRef(self.node_items(name), index),
            "$execution": ex.JSObject({"id": self.execution.id, "mode": self.execution.mode,
                                       "resumeUrl": f"{BASE_URL}/webhook-waiting/{self.execution.id}"}, "$execution"),
            "$workflow": ex.JSObject({"id": self.wf.id, "name": self.wf.name, "active": False}, "$workflow"),
            "$env": dict(self.services.env),
            "$now": now, "$today": now.js_get("startOf")("day"), "$itemIndex": index, "$runIndex": 0,
        }

    def param(self, key: str, index: int = 0, default: Any = None) -> Any:
        v = self.node.params.get(key, default)
        try:
            return ex.to_plain(ex.render_deep(v, self.scope(index)))
        except ex.ExpressionError as e:
            raise NodeError(f"expression in '{key}': {e}") from e

    def rl(self, key: str, index: int = 0) -> str:
        v = self.param(key, index)
        return str(v.get("value", "")) if isinstance(v, dict) else str(v or "")

    def external(self) -> None:
        try:
            self.services.faults.check(self.node.name)
        except ServiceError as e:
            raise NodeError(str(e)) from e


Handler = Callable[[Context], list[list[dict]]]
HANDLERS: dict[str, Handler] = {}


def handles(*types: str):
    def deco(fn):
        for t in types:
            HANDLERS[t if "." in t else "n8n-nodes-base." + t] = fn
        return fn
    return deco


class Simulator:
    def __init__(self, workflows: list[Workflow], services: Services | None = None, now: datetime | None = None):
        self.workflows = {w.id: w for w in workflows}
        self.by_slug = {w.slug: w for w in workflows}
        self.services = services or Services()
        self.now = now or datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
        self._exec_counter = 0

    def workflow(self, key: str) -> Workflow:
        return self.by_slug.get(key) or self.workflows[key]

    # ------------------------------------------------------------------------------------------- triggers
    def webhook(self, key: str, body: dict, headers: dict | None = None, query: dict | None = None) -> Execution:
        wf = self.workflow(key)
        hook = next(n for n in wf.triggers() if n.type == "n8n-nodes-base.webhook")
        item = {"json": {"headers": headers or {"content-type": "application/json"}, "params": {},
                         "query": query or {}, "body": body,
                         "webhookUrl": f"{BASE_URL}/webhook/{hook.params.get('path')}",
                         "executionMode": "production"}}
        return self.run(wf, hook.name, [item], mode="webhook")

    def schedule(self, key: str) -> Execution:
        wf = self.workflow(key)
        trig = next(n for n in wf.triggers() if n.type == "n8n-nodes-base.scheduleTrigger")
        iso = ex.DateTimeValue(self.now).to_iso()
        return self.run(wf, trig.name, [{"json": {"timestamp": iso, "Timezone": "UTC"}}], mode="trigger")

    # ------------------------------------------------------------------------------------------- engine
    def run(self, wf: Workflow, trigger: str, items: list[dict], mode: str = "manual") -> Execution:
        self._exec_counter += 1
        exe = Execution(str(1000 + self._exec_counter), wf.id, wf.name, mode)
        trig = wf.by_name[trigger]
        if trig.type == "n8n-nodes-base.webhook" and trig.params.get("responseMode", "onReceived") == "onReceived":
            code = int((trig.params.get("options") or {}).get("responseCode", 200))
            exe.response = {"status": code, "body": {"message": "Workflow was started"}}
        state: dict[str, Any] = {}
        try:
            self._traverse(wf, trig, items, exe, state)
            exe.status = "success"
        except WorkflowError as e:
            exe.status = "error"
            exe.error = {"node": e.node, "message": e.message}
            if exe.response is None and trig.type == "n8n-nodes-base.webhook":
                exe.response = {"status": 500, "body": {"message": "Error in workflow"}}
            self._run_error_workflow(wf, exe)
        return exe

    def _traverse(self, wf: Workflow, trig: Node, items: list[dict], exe: Execution, state: dict) -> None:
        stack: list[tuple[str, int, list[dict]]] = [(trig.name, 0, items)]
        pending: dict[str, dict[int, list[dict]]] = {}
        steps = 0
        while stack or pending:
            steps += 1
            if steps > MAX_STEPS:
                raise WorkflowError(trig.name, f"stopped after {MAX_STEPS} steps (loop?)")
            if stack:
                name, inp, data = stack.pop()
                node = wf.by_name[name]
                if node.input_count() > 1:
                    buf = pending.setdefault(name, {})
                    buf[inp] = data
                    wanted = {e.input for e in wf.parents(name)}
                    if not wanted <= set(buf):
                        continue
                    del pending[name]
                    inputs = [buf.get(i, []) for i in range(node.input_count())]
                else:
                    inputs = [data]
            else:  # a Merge that only ever received some of its inputs runs with what it has
                name, buf = pending.popitem()
                node = wf.by_name[name]
                inputs = [buf.get(i, []) for i in range(node.input_count())]
            if node.disabled:
                outputs = [inputs[0]]
            else:
                outputs = self._execute(wf, node, inputs, exe, state)
            exe.run_data.setdefault(name, []).append(outputs)
            pushes = []
            for out_idx, out_items in enumerate(outputs):
                if not out_items:
                    continue
                kids = sorted(wf.main_children(name, out_idx), key=lambda e: wf.by_name[e.target].position[::-1])
                pushes += [(e.target, e.input, out_items) for e in kids]
            stack.extend(reversed(pushes))

    def _execute(self, wf: Workflow, node: Node, inputs: list[list[dict]], exe: Execution, state: dict
                 ) -> list[list[dict]]:
        handler = HANDLERS.get(node.type)
        if handler is None:
            raise WorkflowError(node.name, f"simulator has no implementation for {node.type}")
        raw = node.raw
        tries = max(2, min(5, int(raw.get("maxTries", 3)))) if raw.get("retryOnFail") else 1
        attempts, notes = 0, []
        n_out = max(1, len(node.output_names()))
        while True:
            attempts += 1
            ctx = Context(self, wf, node, inputs, exe)
            ctx.state = state.setdefault(node.name, {})
            try:
                outputs = handler(ctx)
                status = "success"
                break
            except (NodeError, ex.ExpressionError, jsrunner.CodeNodeError, ServiceError) as e:
                if attempts < tries:
                    notes.append(f"try {attempts} failed: {e}; waiting {raw.get('waitBetweenTries', 1000)} ms")
                    continue
                on_error = raw.get("onError")
                if on_error == "continueRegularOutput":
                    outputs, status = [[{"json": {"error": str(e)}}]] + [[] for _ in range(n_out - 1)], "error"
                    break
                if on_error == "continueErrorOutput":
                    outputs = [[] for _ in range(n_out - 1)] + [[{"json": {"error": str(e)}} for _ in ctx.items]]
                    status = "error"
                    break
                exe.steps.append(Step(len(exe.steps) + 1, node.name, node.spec.label if node.spec else node.type,
                                      sum(map(len, inputs)), [], attempts, "error", "; ".join(notes + [str(e)])))
                raise WorkflowError(node.name, str(e)) from e
        while len(outputs) < n_out:
            outputs.append([])
        if raw.get("alwaysOutputData") and not any(outputs):
            outputs[0] = [{"json": {}}]
        if ctx.note:
            notes.append(ctx.note)
        exe.steps.append(Step(len(exe.steps) + 1, node.name, node.spec.label if node.spec else node.type,
                              sum(map(len, inputs)), [len(o) for o in outputs], attempts, status, "; ".join(notes)))
        return outputs

    def _run_error_workflow(self, wf: Workflow, exe: Execution) -> None:
        target = wf.settings.get("errorWorkflow")
        handler = self.workflows.get(target) if target else None
        if handler is None:
            return
        trig = next((n for n in handler.nodes if n.type == "n8n-nodes-base.errorTrigger"), None)
        if trig is None:
            return
        payload = {"execution": {"id": exe.id, "url": f"{BASE_URL}/workflow/{wf.id}/executions/{exe.id}",
                                 "error": {"message": exe.error["message"], "stack": ""},
                                 "lastNodeExecuted": exe.error["node"], "mode": exe.mode},
                   "workflow": {"id": wf.id, "name": wf.name}}
        exe.error_execution = self.run(handler, trig.name, [{"json": payload}], mode="error")


# ================================================================================================ node handlers

@handles("webhook", "scheduleTrigger", "errorTrigger", "manualTrigger", "noOp")
def _passthrough(ctx: Context):
    return [ctx.items]


_CASTS = {
    "string": lambda v: None if v is None or v is ex.UNDEFINED else v if isinstance(v, str) else ex.to_js_string(v),
    "number": lambda v: ex.to_number(v),
    "boolean": lambda v: ex.truthy(v),
}


@handles("set")
def _set(ctx: Context):
    out = []
    fields = ((ctx.node.params.get("assignments") or {}).get("assignments")) or []
    for i, item in enumerate(ctx.items):
        scope = ctx.scope(i)
        new = dict(item["json"]) if ctx.node.params.get("includeOtherFields") else {}
        for f in fields:
            try:
                v = ex.render(f.get("value"), scope)
            except ex.ExpressionError as e:
                raise NodeError(f"field '{f.get('name')}': {e}") from e
            new[f["name"]] = ex.to_plain(_CASTS.get(f.get("type"), lambda x: x)(v))
        out.append({"json": new})
    return [out]


def _check(cond: dict, scope: dict, strict: bool) -> bool:
    op = cond.get("operator") or {}
    typ, operation = op.get("type", "string"), op.get("operation", "equals")
    left = ex.render(cond.get("leftValue"), scope)
    right = ex.render(cond.get("rightValue"), scope)
    missing = left is None or left is ex.UNDEFINED
    if operation == "exists":
        return not missing
    if operation == "notExists":
        return missing
    if operation in ("empty", "notEmpty"):
        empty = missing or left == "" or left == [] or left == {}
        return empty if operation == "empty" else not empty
    expect = {"string": str, "number": (int, float), "boolean": bool, "array": list, "object": dict}.get(typ)
    if strict and expect and not missing and (not isinstance(left, expect) or (typ == "number" and isinstance(left,
                                                                                                        bool))):
        raise NodeError(f"Wrong type: '{ex.to_js_string(left)}' is not a {typ} [condition {cond.get('id', '')}]")
    if typ == "boolean":
        b = ex.truthy(left)
        return {"true": b, "false": not b, "equals": b == ex.truthy(right), "notEquals": b != ex.truthy(right)}[
            operation]
    if typ == "number":
        x, y = ex.to_number(left), ex.to_number(right)
        return {"equals": x == y, "notEquals": x != y, "gt": x > y, "lt": x < y, "gte": x >= y,
                "lte": x <= y}[operation]
    if typ == "array":
        arr = left if isinstance(left, list) else []
        return {"contains": right in arr, "notContains": right not in arr, "lengthEquals": len(arr) == right,
                "lengthGt": len(arr) > ex.to_number(right)}[operation]
    s, r = ex.to_js_string(left) if not missing else "", ex.to_js_string(right)
    return {"equals": s == r, "notEquals": s != r, "contains": r in s, "notContains": r not in s,
            "startsWith": s.startswith(r), "endsWith": s.endswith(r),
            "regex": bool(re.search(r, s)), "notRegex": not re.search(r, s)}[operation]


def _conditions_hold(block: dict, scope: dict) -> bool:
    strict = (block.get("options") or {}).get("typeValidation", "strict") == "strict"
    results = (_check(c, scope, strict) for c in block.get("conditions") or [])
    return any(results) if block.get("combinator") == "or" else all(results)


@handles("if")
def _if(ctx: Context):
    yes, no = [], []
    for i, item in enumerate(ctx.items):
        (yes if _conditions_hold(ctx.node.params.get("conditions") or {}, ctx.scope(i)) else no).append(item)
    return [yes, no]


@handles("switch")
def _switch(ctx: Context):
    rules = ((ctx.node.params.get("rules") or {}).get("values")) or []
    opts = ctx.node.params.get("options") or {}
    outs: list[list[dict]] = [[] for _ in ctx.node.output_names()]
    for i, item in enumerate(ctx.items):
        hit = False
        for r_idx, r in enumerate(rules):
            if _conditions_hold(r.get("conditions") or {}, ctx.scope(i)):
                outs[r_idx].append(item)
                hit = True
                if not opts.get("allMatchingOutputs"):
                    break
        if not hit and opts.get("fallbackOutput") == "extra":
            outs[len(rules)].append(item)
        elif not hit and isinstance(opts.get("fallbackOutput"), int):
            outs[opts["fallbackOutput"]].append(item)
    return outs


@handles("code")
def _code(ctx: Context):
    code = ctx.node.params.get("jsCode", "")
    mode = ctx.node.params.get("mode", "runOnceForAllItems")
    nodes = {n: ctx.node_items(n) for n in jsrunner.referenced_nodes(code)}
    return [jsrunner.run_code(code, ctx.items, mode, nodes, ex.DateTimeValue(ctx.sim.now).to_iso(),
                              {"id": ctx.execution.id, "mode": ctx.execution.mode},
                              {"id": ctx.wf.id, "name": ctx.wf.name}, dict(ctx.services.env))]


@handles("respondToWebhook")
def _respond(ctx: Context):
    if ctx.execution.response is None:
        p = ctx.node.params
        body: Any = None
        if p.get("respondWith", "firstIncomingItem") == "json":
            body = ctx.param("responseBody")
            if isinstance(body, str):
                body = json.loads(body)
        elif p.get("respondWith", "firstIncomingItem") == "firstIncomingItem":
            body = ctx.items[0]["json"] if ctx.items else {}
        code = int((p.get("options") or {}).get("responseCode", 200))
        ctx.execution.response = {"status": code, "body": body}
        ctx.note = f"responded {code}"
    else:
        ctx.note = "response already sent; ignored"
    return [ctx.items]


@handles("merge")
def _merge(ctx: Context):
    p = ctx.node.params
    if p.get("mode", "append") == "append":
        return [[it for inp in ctx.inputs for it in inp]]
    if p.get("mode") == "combine" and p.get("combineBy") == "combineByPosition":
        a, b = ctx.inputs[0], ctx.inputs[1]
        return [[{"json": {**a[i]["json"], **b[i]["json"]}} for i in range(min(len(a), len(b)))]]
    raise NodeError(f"merge mode {p.get('mode')}/{p.get('combineBy')} is not simulated")


@handles("splitInBatches")
def _split(ctx: Context):
    st = ctx.state
    size = int(ctx.node.params.get("batchSize", 1))
    if not st.get("active"):
        st.update(active=True, queue=list(ctx.items), done=[])
    else:
        st["done"].extend(ctx.items)
    if st["queue"]:
        batch, st["queue"] = st["queue"][:size], st["queue"][size:]
        return [[], batch]
    st["active"] = False
    ctx.note = f"done after {len(st['done'])} items"
    return [st["done"], []]


@handles("wait")
def _wait(ctx: Context):
    p = ctx.node.params
    if p.get("resume") != "webhook":
        ctx.note = "waited (time-based; not simulated)"
        return [ctx.items]
    agent_item = ctx.items[0]["json"] if ctx.items else {}
    context = {"execution_id": ctx.execution.id, "item": agent_item, "output": {}}
    for name, runs in ctx.execution.run_data.items():
        node = ctx.wf.by_name[name]
        if node.type.endswith(".agent") or node.type.endswith(".chainLlm"):
            context["output"] = runs[-1][0][0]["json"].get("output", {}) if runs[-1][0] else {}
    decision = ctx.services.reviewer(context)
    if decision is None:
        ctx.note = f"timed out after {p.get('resumeAmount')} {p.get('resumeUnit')}"
        return [ctx.items]
    ctx.note = "resumed: " + ", ".join(f"{k}={v}" for k, v in decision.items())
    return [[{"json": {"headers": {}, "params": {}, "query": decision, "body": {}}}]]


def _credential_headers(ctx: Context, node: Node) -> dict:
    headers = {}
    for ref in node.credentials.values():
        headers.update(ctx.services.credential_headers.get(ref.get("name", ""), {}))
    return headers


@handles("httpRequest")
def _http(ctx: Context):
    out = []
    for i in range(len(ctx.items)):
        ctx.external()
        url = ctx.param("url", i)
        query = {q["name"]: q["value"] for q in (ctx.param("queryParameters", i) or {}).get("parameters", [])} \
            if ctx.node.params.get("sendQuery") else {}
        headers = _credential_headers(ctx, ctx.node)
        status, body = ctx.services.http.request(ctx.param("method", i) or "GET", url, query, headers)
        if status >= 400:
            raise NodeError(f"{status} from {url}: {body}")
        out += [{"json": b} for b in body] if isinstance(body, list) else [{"json": body}]
    return [out]


@handles("googleSheets")
def _sheets(ctx: Context):
    out = []
    op = ctx.node.params.get("operation", "append")
    for i in range(len(ctx.items)):
        ctx.external()
        doc, sheet = ctx.rl("documentId", i), ctx.rl("sheetName", i)
        if op == "append":
            row = (ctx.param("columns", i) or {}).get("value", {})
            out.append({"json": ctx.services.sheets.append(doc, sheet, row)})
        elif op == "read":
            filters = [(f["lookupColumn"], f["lookupValue"])
                       for f in (ctx.param("filtersUI", i) or {}).get("values", [])]
            out += [{"json": r} for r in ctx.services.sheets.read(doc, sheet, filters)]
        else:
            raise NodeError(f"Sheets operation {op} is not simulated")
    ctx.note = f"{op} {sheet}"
    return [out]


@handles("gmail")
def _gmail(ctx: Context):
    out = []
    p = ctx.node.params
    for i in range(len(ctx.items)):
        ctx.external()
        subject, message = ctx.param("subject", i), ctx.param("message", i)
        if p.get("resource") == "draft":
            to = (ctx.param("options", i) or {}).get("sendTo", "")
            out.append({"json": ctx.services.gmail.draft(to, subject, message)})
            ctx.note = f"draft to {to}"
        else:
            to = ctx.param("sendTo", i)
            out.append({"json": ctx.services.gmail.send(to, subject, message)})
            ctx.note = f"sent to {to}"
    return [out]


@handles("slack")
def _slack(ctx: Context):
    out = []
    for i in range(len(ctx.items)):
        ctx.external()
        channel = ctx.rl("channelId", i)
        out.append({"json": ctx.services.slack.post(channel, ctx.param("text", i))})
        ctx.note = f"posted to {channel}"
    return [out]


@handles("googleCalendar")
def _calendar(ctx: Context):
    out = []
    p = ctx.node.params
    for i in range(len(ctx.items)):
        ctx.external()
        cal = ctx.rl("calendar", i)
        if p.get("resource") == "calendar" and p.get("operation") == "availability":
            busy = ctx.services.calendar.busy(cal, ctx.param("timeMin", i), ctx.param("timeMax", i))
            out += [{"json": b} for b in busy]
            ctx.note = f"{len(busy)} busy slots"
        elif p.get("operation", "create") == "create":
            extra = ctx.param("additionalFields", i) or {}
            ev = ctx.services.calendar.create(cal, ctx.param("start", i), ctx.param("end", i),
                                              extra.get("summary", ""), list(extra.get("attendees") or []))
            out.append({"json": ev})
            ctx.note = f"event {ev['id']}"
        else:
            raise NodeError("calendar operation is not simulated")
    return [out]


def _llm(ctx: Context, agent: bool):
    wf, node = ctx.wf, ctx.node
    models = wf.sub_nodes(node.name, "ai_languageModel")
    if len(models) != 1:
        raise NodeError("a chat model sub-node is required")
    parsers = wf.sub_nodes(node.name, "ai_outputParser")
    schema = json.loads(parsers[0].params["inputSchema"]) if parsers and node.params.get("hasOutputParser") else None
    tools = {t.name: t for t in wf.sub_nodes(node.name, "ai_tool")} if agent else {}
    memory = (wf.sub_nodes(node.name, "ai_memory") or [None])[0] if agent else None
    if agent:
        system = (node.params.get("options") or {}).get("systemMessage", "")
    else:
        system = " ".join(m.get("message", "") for m in (node.params.get("messages") or {}).get("messageValues", []))
    out = []
    for i in range(len(ctx.items)):
        ctx.external()
        prompt = ctx.param("text", i)
        scope = ctx.scope(i)
        history, session = [], None
        if memory is not None:
            session = ex.to_js_string(ex.render(memory.params.get("sessionKey"), scope))
            history = ctx.services.memory.get(session, [])

        def call_tool(name: str, args: dict, _scope=scope) -> Any:
            tool = tools.get(name)
            if tool is None:
                return {"error": f"unknown tool {name}"}
            url = ex.to_js_string(ex.render(tool.params.get("url"), _scope))
            query = {}
            for ph in (tool.params.get("placeholderDefinitions") or {}).get("values", []):
                url = url.replace("{" + ph["name"] + "}", str(args.get(ph["name"], "")))
            if tool.params.get("sendQuery"):
                for q in (tool.params.get("parametersQuery") or {}).get("values", []):
                    query[q["name"]] = args.get(q["name"], "")
            status, body = ctx.services.http.request(tool.params.get("method", "GET"), url, query,
                                                     _credential_headers(ctx, tool))
            ctx.execution.tool_calls.append({"node": node.name, "tool": name, "args": args, "status": status})
            return body if status < 400 else {"error": body, "status": status}

        req = LLMRequest(wf.slug, node.name, str(models[0].params.get("model")), system, str(prompt), schema,
                         list(tools), history, call_tool)
        answer = ctx.services.llm.complete(req)
        if schema is not None:
            try:
                jsonschema.validate(answer, schema)
            except jsonschema.SchemaError as e:
                raise NodeError(f"Model output doesn't fit required format: {e}") from e
        if session is not None:
            window = int(memory.params.get("contextWindowLength", 5))
            ctx.services.memory[session] = (history + [{"input": prompt, "output": answer}])[-window:]
        out.append({"json": {"output": answer}})
        calls = [c["tool"] for c in ctx.execution.tool_calls if c["node"] == node.name]
        ctx.note = (f"{schema.get('title') if schema else 'text'}" + (f"; tools: {', '.join(calls)}" if calls else "")
                    + (f"; memory: {len(history)} earlier" if memory is not None else ""))
    return [out]


@handles("@n8n/n8n-nodes-langchain.chainLlm")
def _chain(ctx: Context):
    return _llm(ctx, agent=False)


@handles("@n8n/n8n-nodes-langchain.agent")
def _agent(ctx: Context):
    return _llm(ctx, agent=True)
