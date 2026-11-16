"""Run a Code node's JavaScript with Node.js, using a small shim for ``$input``, ``$json``, ``$()`` and ``$now``.

The shim covers what these templates use. It is not n8n's task runner: there is no sandbox, no ``require`` allow-list
and no binary data. Each call starts one ``node`` process.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

_SHIM = r"""
const __d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
console.log = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');
const __ref = (items, idx) => ({
  all: () => items,
  first: () => items[0],
  last: () => items[items.length - 1],
  item: items[Math.min(idx, items.length - 1)],
  isExecuted: true,
});
const $now = { toISO: () => __d.now, toMillis: () => Date.parse(__d.now), toString: () => __d.now };
const $execution = __d.execution;
const $workflow = __d.workflow;
const $env = __d.env;
const __node = (idx) => (name) => {
  if (!(name in __d.nodes)) throw new Error(`Referenced node "${name}" has not been executed`);
  return __ref(__d.nodes[name], idx);
};
async function __runAll() {
  const $input = __ref(__d.items, 0);
  const items = __d.items;
  const $ = __node(0);
  /*CODE*/
}
async function __runEach(item, $itemIndex) {
  const $input = { ...__ref(__d.items, $itemIndex), item };
  const $json = item.json;
  const $ = __node($itemIndex);
  /*CODE*/
}
(async () => {
  try {
    let out;
    if (__d.mode === 'runOnceForEachItem') {
      out = [];
      for (let i = 0; i < __d.items.length; i++) {
        const r = await __runEach(__d.items[i], i);
        if (r !== undefined && r !== null) out.push(r);
      }
    } else {
      out = await __runAll();
    }
    process.stdout.write(JSON.stringify({ ok: true, out }));
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) }));
  }
})();
"""

_REF = re.compile(r"""\$\(\s*(['"])(.+?)\1\s*\)""")


class CodeNodeError(RuntimeError):
    pass


def node_available() -> bool:
    return shutil.which("node") is not None


def referenced_nodes(code: str) -> set[str]:
    return {m.group(2) for m in _REF.finditer(code)}


def _normalise(out) -> list[dict]:
    if out is None:
        return []
    if isinstance(out, dict):
        out = [out]
    if not isinstance(out, list):
        raise CodeNodeError("Code doesn't return items properly: return an array of objects")
    items = []
    for x in out:
        if isinstance(x, dict) and isinstance(x.get("json"), dict):
            items.append({"json": x["json"]})
        elif isinstance(x, dict):
            items.append({"json": x})
        else:
            raise CodeNodeError(f"Code returned a non-object item: {x!r}")
    return items


def run_code(code: str, items: list[dict], mode: str, nodes: dict[str, list[dict]], now: str,
             execution: dict, workflow: dict, env: dict, timeout: float = 20.0) -> list[dict]:
    if not node_available():
        raise CodeNodeError("Node.js is required to run Code nodes in the simulator (node not found on PATH)")
    script = _SHIM.replace("/*CODE*/", code)
    payload = json.dumps({"items": items, "mode": mode, "nodes": nodes, "now": now, "execution": execution,
                          "workflow": workflow, "env": env})
    proc = subprocess.run(["node", "-e", script], input=payload, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not proc.stdout:
        raise CodeNodeError(f"node exited {proc.returncode}: {proc.stderr.strip()[-500:]}")
    res = json.loads(proc.stdout)
    if not res["ok"]:
        raise CodeNodeError(res["error"])
    out = res["out"]
    if mode == "runOnceForEachItem":
        return [it for x in out for it in _normalise(x)]
    return _normalise(out)
