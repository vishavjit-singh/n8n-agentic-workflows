"""Static checks for n8n workflow exports, run before anything is imported into n8n.

Each rule is a function ``(workflow, all_workflows) -> findings``. Errors fail ``n8nkit validate``; warnings are
printed and fail only with ``--strict``.
"""
from __future__ import annotations

import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Callable

from . import catalog, expressions
from .model import Workflow, walk_strings

ERROR, WARNING = "error", "warning"


@dataclass
class Finding:
    rule: str
    severity: str
    workflow: str
    message: str
    node: str = ""

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        where = f"{self.workflow}" + (f" › {self.node}" if self.node else "")
        return f"{self.severity:<7} {self.rule:<16} {where}: {self.message}"


Rule = Callable[[Workflow, list[Workflow]], list[Finding]]
RULES: dict[str, tuple[str, Rule]] = {}


def rule(rule_id: str, description: str):
    def deco(fn: Rule) -> Rule:
        RULES[rule_id] = (description, fn)
        return fn
    return deco


def _f(rule_id, sev, wf: Workflow, msg, node=""):
    return Finding(rule_id, sev, wf.slug, msg, node)


@rule("structure", "Export has name, nodes, connections, settings; each node has id, name, type, version, position")
def r_structure(wf, _all):
    out = []
    for key, typ in (("name", str), ("nodes", list), ("connections", dict), ("settings", dict)):
        if not isinstance(wf.raw.get(key), typ):
            out.append(_f("structure", ERROR, wf, f"top-level '{key}' missing or not a {typ.__name__}"))
    for i, n in enumerate(wf.raw.get("nodes") or []):
        if not isinstance(n, dict):
            out.append(_f("structure", ERROR, wf, f"nodes[{i}] is not an object"))
            continue
        missing = [k for k in ("id", "name", "type", "typeVersion", "position", "parameters") if k not in n]
        if missing:
            out.append(_f("structure", ERROR, wf, f"missing {', '.join(missing)}", n.get("name", f"nodes[{i}]")))
        pos = n.get("position")
        if pos is not None and not (isinstance(pos, list) and len(pos) == 2
                                    and all(isinstance(x, (int, float)) for x in pos)):
            out.append(_f("structure", ERROR, wf, "position must be [x, y]", n.get("name", "")))
    return out


@rule("unique-names", "Node names are unique (connections refer to nodes by name)")
def r_unique_names(wf, _all):
    return [_f("unique-names", ERROR, wf, f"node name used {c} times", name)
            for name, c in Counter(n.name for n in wf.nodes).items() if c > 1]


@rule("unique-ids", "Node ids are unique UUIDs")
def r_unique_ids(wf, _all):
    out = [_f("unique-ids", ERROR, wf, f"node id {i!r} used {c} times")
           for i, c in Counter(n.id for n in wf.nodes).items() if c > 1]
    for n in wf.nodes:
        try:
            uuid.UUID(str(n.id))
        except ValueError:
            out.append(_f("unique-ids", WARNING, wf, f"id {n.id!r} is not a UUID", n.name))
    return out


@rule("connections", "Every connection joins existing nodes with a valid type and output/input index")
def r_connections(wf, _all):
    out = []
    for src in (wf.raw.get("connections") or {}):
        if src not in wf.by_name:
            out.append(_f("connections", ERROR, wf, f"connection from unknown node {src!r}"))
    for e in wf.edges():
        if e.source not in wf.by_name:
            continue
        if e.target not in wf.by_name:
            out.append(_f("connections", ERROR, wf, f"connects to unknown node {e.target!r}", e.source))
            continue
        src, dst = wf.by_name[e.source], wf.by_name[e.target]
        if e.type not in catalog.CONNECTION_TYPES:
            out.append(_f("connections", ERROR, wf, f"unknown connection type {e.type!r}", e.source))
        elif e.type == "main":
            if e.output >= len(src.output_names()):
                out.append(_f("connections", ERROR, wf, f"output {e.output} does not exist "
                              f"(node has {len(src.output_names())})", e.source))
            if e.input >= max(dst.input_count(), 1) or dst.is_trigger:
                out.append(_f("connections", ERROR, wf, f"input {e.input} of {e.target!r} does not exist", e.source))
        else:
            s_spec, d_spec = src.spec, dst.spec
            if s_spec and s_spec.sub_node and s_spec.sub_node != e.type:
                out.append(_f("connections", ERROR, wf, f"{src.type} emits {s_spec.sub_node}, not {e.type}", e.source))
            if d_spec and e.type not in d_spec.accepts:
                out.append(_f("connections", ERROR, wf, f"{e.target!r} does not accept {e.type}", e.source))
    return out


@rule("orphans", "Every node is reachable from a trigger (sticky notes excepted)")
def r_orphans(wf, _all):
    reach = wf.reachable()
    return [_f("orphans", ERROR, wf, "not reachable from any trigger", n.name)
            for n in wf.nodes if not n.is_note and not n.disabled and n.name not in reach]


@rule("trigger", "The workflow has at least one enabled trigger")
def r_trigger(wf, _all):
    return [] if wf.triggers() else [_f("trigger", ERROR, wf, "no trigger node")]


def _webhook_paths(wf: Workflow) -> list[tuple[str, str, str]]:
    return [(str(n.params.get("httpMethod", "GET")), str(n.params.get("path", "")).strip("/"), n.name)
            for n in wf.nodes if n.type == "n8n-nodes-base.webhook" and not n.disabled]


@rule("webhook-paths", "Webhook method+path pairs are unique across all workflows; webhook nodes have a webhookId")
def r_webhook_paths(wf, all_wfs):
    out = []
    mine = _webhook_paths(wf)
    for method, path, node in mine:
        if not path:
            out.append(_f("webhook-paths", ERROR, wf, "empty webhook path", node))
        clashes = [o.slug for o in all_wfs for m, p, _ in _webhook_paths(o) if (m, p) == (method, path)
                   and o is not wf]
        if [(m, p) for m, p, _ in mine].count((method, path)) > 1 or clashes:
            out.append(_f("webhook-paths", ERROR, wf, f"{method} /{path} is also used by "
                          f"{', '.join(clashes) or 'another node here'}", node))
    for n in wf.nodes:
        if n.type == "n8n-nodes-base.webhook" or (n.type == "n8n-nodes-base.wait"
                                                    and n.params.get("resume") == "webhook"):
            if not n.raw.get("webhookId"):
                out.append(_f("webhook-paths", ERROR, wf, "webhookId missing", n.name))
    ids = Counter(n.raw.get("webhookId") for n in wf.nodes if n.raw.get("webhookId"))
    out += [_f("webhook-paths", ERROR, wf, f"webhookId {i} used {c} times") for i, c in ids.items() if c > 1]
    return out


SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "Slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "Slack webhook URL": re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}"),
}
SENSITIVE_NAME = re.compile(r"(api[_-]?key|apikey|secret|password|passwd|token|authorization|x-api-key)", re.I)


def _literal(value: str) -> bool:
    return bool(value.strip()) and not expressions.is_expression(value)


@rule("secrets", "No API keys, tokens or passwords written into parameters")
def r_secrets(wf, _all):
    out = []
    for n in wf.nodes:
        for path, s in walk_strings(n.params):
            for label, pat in SECRET_PATTERNS.items():
                if pat.search(s):
                    out.append(_f("secrets", ERROR, wf, f"{label} in parameters.{path}", n.name))
            leaf = re.split(r"[.\[]", path)[-1]
            if SENSITIVE_NAME.search(leaf) and leaf not in ("genericAuthType",) and _literal(s):
                out.append(_f("secrets", ERROR, wf, f"literal value for sensitive field parameters.{path}", n.name))
        # name/value pairs: headerParameters, queryParameters, bodyParameters
        for path, obj in _pairs(n.params):
            name, value = str(obj.get("name", "")), obj.get("value")
            if SENSITIVE_NAME.search(name) and isinstance(value, str) and _literal(value):
                out.append(_f("secrets", ERROR, wf, f"literal {name!r} value in parameters.{path}", n.name))
    return out


def _pairs(obj, path=""):
    if isinstance(obj, dict):
        if "name" in obj and "value" in obj:
            yield path, obj
        for k, v in obj.items():
            yield from _pairs(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _pairs(v, f"{path}[{i}]")


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_TLDS = (".example", ".test", ".invalid", ".localhost")


@rule("emails", "No real email addresses in parameters (example.com and other reserved domains are allowed)")
def r_emails(wf, _all):
    out = []
    for n in wf.nodes:
        for path, s in walk_strings(n.params):
            for m in EMAIL.finditer(s):
                dom = m.group(1).lower()
                if dom in RESERVED_DOMAINS or dom.endswith(tuple("." + d for d in RESERVED_DOMAINS)) \
                        or dom.endswith(RESERVED_TLDS):
                    continue
                out.append(_f("emails", ERROR, wf, f"hardcoded address {m.group(0)!r} in parameters.{path}",
                              n.name))
    return out


@rule("credentials", "Credentials are references (name, optional id) of a type the node accepts; no inline data")
def r_credentials(wf, _all):
    out = []
    for n in wf.nodes:
        for ctype, ref in n.credentials.items():
            if not isinstance(ref, dict) or not isinstance(ref.get("name"), str) or not ref.get("name"):
                out.append(_f("credentials", ERROR, wf, f"{ctype}: reference needs a name", n.name))
                continue
            extra = set(ref) - {"id", "name"}
            if extra:
                out.append(_f("credentials", ERROR, wf, f"{ctype}: unexpected keys {sorted(extra)} (inline data?)",
                              n.name))
            s = n.spec
            if s and s.credential_types and ctype not in s.credential_types:
                out.append(_f("credentials", ERROR, wf, f"{ctype} is not a credential type for {s.label}", n.name))
        s = n.spec
        auth = n.params.get("authentication")
        if auth == "genericCredentialType":
            gat = n.params.get("genericAuthType")
            if gat and gat not in n.credentials:
                out.append(_f("credentials", ERROR, wf, f"genericAuthType {gat} has no credential reference", n.name))
        if s and s.credential_types and s.kind == catalog.INTEGRATION and not n.credentials \
                and n.type != "n8n-nodes-base.httpRequest":
            out.append(_f("credentials", ERROR, wf, f"{s.label} node without credentials", n.name))
        if s and s.sub_node == "ai_languageModel" and not n.credentials:
            out.append(_f("credentials", ERROR, wf, "chat model without credentials", n.name))
    return out


@rule("retries", "Nodes that call external services retry (retryOnFail, maxTries >= 2, waitBetweenTries set)")
def r_retries(wf, _all):
    out = []
    for n in wf.nodes:
        s = n.spec
        if not s or not s.external or n.disabled:
            continue
        r = n.raw
        if not r.get("retryOnFail"):
            out.append(_f("retries", ERROR, wf, "retryOnFail is not set", n.name))
        elif int(r.get("maxTries", 3)) < 2 or not r.get("waitBetweenTries"):
            out.append(_f("retries", WARNING, wf, "set maxTries >= 2 and waitBetweenTries", n.name))
    return out


@rule("error-workflow", "settings.errorWorkflow names a workflow in this set that starts with an Error Trigger")
def r_error_workflow(wf, all_wfs):
    if any(n.type == "n8n-nodes-base.errorTrigger" for n in wf.nodes):
        return []
    target = wf.settings.get("errorWorkflow")
    if not target:
        return [_f("error-workflow", ERROR, wf, "settings.errorWorkflow is not set")]
    handler = next((o for o in all_wfs if o.id == target), None)
    if handler is None:
        return [_f("error-workflow", ERROR, wf, f"errorWorkflow {target!r} is not in this set")]
    if not any(n.type == "n8n-nodes-base.errorTrigger" for n in handler.nodes):
        return [_f("error-workflow", ERROR, wf, f"{handler.slug} has no Error Trigger")]
    return []


@rule("node-versions", "Every node type is known and uses a supported typeVersion")
def r_node_versions(wf, _all):
    out = []
    for n in wf.nodes:
        s = n.spec
        if s is None:
            out.append(_f("node-versions", ERROR, wf, f"unknown node type {n.type!r}", n.name))
        elif n.type_version not in s.versions:
            out.append(_f("node-versions", ERROR, wf, f"typeVersion {n.type_version} not in {list(s.versions)}",
                          n.name))
    return out


@rule("ai-wiring", "AI roots have exactly one chat model; parsers and tools are wired where the root expects them")
def r_ai_wiring(wf, _all):
    out = []
    for n in wf.nodes:
        s = n.spec
        if s and s.accepts:
            models = wf.sub_nodes(n.name, "ai_languageModel")
            if len(models) != 1:
                out.append(_f("ai-wiring", ERROR, wf, f"needs exactly one chat model, has {len(models)}", n.name))
            parsers = wf.sub_nodes(n.name, "ai_outputParser")
            if n.params.get("hasOutputParser") and len(parsers) != 1:
                out.append(_f("ai-wiring", ERROR, wf, "hasOutputParser is on but no parser is connected", n.name))
            if parsers and not n.params.get("hasOutputParser"):
                out.append(_f("ai-wiring", ERROR, wf, "a parser is connected but hasOutputParser is off", n.name))
            if n.type.endswith(".agent") and not wf.sub_nodes(n.name, "ai_tool"):
                out.append(_f("ai-wiring", WARNING, wf, "agent without tools (a Basic LLM Chain may fit better)",
                              n.name))
        if s and s.sub_node:
            targets = [e.target for e in wf.edges((s.sub_node,)) if e.source == n.name]
            if len(targets) != 1:
                out.append(_f("ai-wiring", ERROR, wf, f"sub-node must feed exactly one root, feeds {len(targets)}",
                              n.name))
        if n.type.endswith(".outputParserStructured") and n.params.get("schemaType") == "manual":
            import json
            try:
                json.loads(n.params.get("inputSchema", ""))
            except ValueError as e:
                out.append(_f("ai-wiring", ERROR, wf, f"inputSchema is not valid JSON: {e}", n.name))
    return out


@rule("respond-node", "Webhooks in 'responseNode' mode reach a Respond to Webhook node on every branch")
def r_respond(wf, _all):
    out = []
    responders = [n for n in wf.nodes if n.type == "n8n-nodes-base.respondToWebhook"]
    hooks = [n for n in wf.nodes if n.type == "n8n-nodes-base.webhook"]
    needs = [h for h in hooks if h.params.get("responseMode") == "responseNode"]
    if responders and not needs:
        out.append(_f("respond-node", ERROR, wf, "Respond to Webhook used but no webhook is in responseNode mode"))
    for h in needs:
        if not responders:
            out.append(_f("respond-node", ERROR, wf, "responseNode mode without a Respond to Webhook node", h.name))
            continue
        # every leaf path from the webhook must pass a responder
        for leaf_path in _paths(wf, h.name):
            if not any(wf.by_name[p].type == "n8n-nodes-base.respondToWebhook" for p in leaf_path):
                out.append(_f("respond-node", ERROR, wf, "branch ends without responding: " + " → ".join(leaf_path),
                              h.name))
                break
    return out


def _paths(wf: Workflow, start: str, limit: int = 500):
    """Simple paths from ``start`` to leaves along main connections (loops cut)."""
    stack, n = [[start]], 0
    while stack and n < limit:
        path = stack.pop()
        kids = [e.target for e in wf.main_children(path[-1]) if e.target not in path and e.target in wf.by_name]
        if not kids:
            n += 1
            yield path
        for k in kids:
            stack.append(path + [k])


@rule("expressions", "Expressions parse with n8nkit's evaluator (so the simulator can run them)")
def r_expressions(wf, _all):
    out = []
    for n in wf.nodes:
        if n.type == "n8n-nodes-base.code" or n.is_note:
            continue
        for path, s in walk_strings(n.params):
            if s.startswith("=") and s.count("{{") != s.count("}}"):
                out.append(_f("expressions", ERROR, wf, f"unbalanced braces in parameters.{path}", n.name))
                continue
            for body in expressions.expression_bodies(s):
                try:
                    expressions.parse(body)
                except expressions.ExpressionError as e:
                    out.append(_f("expressions", WARNING, wf, f"parameters.{path}: {e}", n.name))
    return out


@rule("export-settings", "Exported inactive, with executionOrder v1 and an id (needed for errorWorkflow links)")
def r_export_settings(wf, _all):
    out = []
    if wf.raw.get("active"):
        out.append(_f("export-settings", WARNING, wf, "exported as active; templates should import inactive"))
    if wf.settings.get("executionOrder") != "v1":
        out.append(_f("export-settings", ERROR, wf, "settings.executionOrder should be 'v1'"))
    if not wf.id:
        out.append(_f("export-settings", ERROR, wf, "workflow id missing"))
    if wf.raw.get("pinData"):
        out.append(_f("export-settings", WARNING, wf, "pinData is exported (may contain real payloads)"))
    return out


def lint(workflows: list[Workflow], only: list[str] | None = None) -> list[Finding]:
    ids = Counter(w.id for w in workflows if w.id)
    out: list[Finding] = [Finding("export-settings", ERROR, w.slug, f"workflow id {w.id!r} used {ids[w.id]} times")
                          for w in workflows if w.id and ids[w.id] > 1]
    for wf in workflows:
        for rid, (_desc, fn) in RULES.items():
            if only and rid not in only:
                continue
            try:
                out += fn(wf, workflows)
            except Exception as e:  # a malformed file must not crash the whole run
                out.append(Finding(rid, ERROR, wf.slug, f"rule crashed: {type(e).__name__}: {e}"))
    return out


def matrix(workflows: list[Workflow], findings: list[Finding]) -> dict[str, dict[str, str]]:
    """workflow slug → rule → 'pass' | 'warning' | 'error' (for the README chart)."""
    m = {w.slug: {r: "pass" for r in RULES} for w in workflows}
    for f in findings:
        if f.workflow in m:
            cur = m[f.workflow][f.rule]
            if cur != "error":
                m[f.workflow][f.rule] = f.severity
    return m
