import copy
import uuid

import pytest

from n8nkit import config
from n8nkit.model import Workflow, load_all


@pytest.fixture(scope="session")
def workflows():
    return load_all(config.WORKFLOWS)


@pytest.fixture
def fresh(workflows):
    """Deep copies of the shipped workflows, safe to mutate."""
    return [Workflow(copy.deepcopy(w.raw), w.path) for w in workflows]


def make_workflow(nodes, connections, settings=None, wid="TestWorkflow0001", name="test"):
    """A small workflow from (name, type, params, extra) tuples and (src, dst, out, in) edges."""
    raw_nodes = []
    for i, spec in enumerate(nodes):
        name_, type_, params = spec[:3]
        extra = spec[3] if len(spec) > 3 else {}
        version = extra.pop("typeVersion", 1)
        raw_nodes.append({"parameters": params, "id": str(uuid.uuid4()), "name": name_,
                          "type": type_ if "." in type_ else "n8n-nodes-base." + type_, "typeVersion": version,
                          "position": [i * 240, 0], **extra})
    conns = {}
    for edge in connections:
        src, dst, out, inp = (list(edge) + [0, 0])[:4]
        outs = conns.setdefault(src, {}).setdefault("main", [])
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": dst, "type": "main", "index": inp})
    return Workflow({"name": name, "id": wid, "nodes": raw_nodes, "connections": conns,
                     "settings": settings or {"executionOrder": "v1"}, "active": False})
