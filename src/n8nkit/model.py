"""Load an n8n workflow export and answer graph questions about it (edges, outputs, sub-nodes, reachability)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import catalog


@dataclass(frozen=True)
class Edge:
    source: str
    output: int
    target: str
    input: int
    type: str = "main"


class Node:
    def __init__(self, raw: dict):
        self.raw = raw

    def __repr__(self) -> str:
        return f"Node({self.name!r}, {self.type})"

    name = property(lambda self: self.raw.get("name", ""))
    id = property(lambda self: self.raw.get("id", ""))
    type = property(lambda self: self.raw.get("type", ""))
    type_version = property(lambda self: self.raw.get("typeVersion"))
    params = property(lambda self: self.raw.get("parameters") or {})
    position = property(lambda self: tuple(self.raw.get("position") or (0, 0)))
    credentials = property(lambda self: self.raw.get("credentials") or {})
    disabled = property(lambda self: bool(self.raw.get("disabled")))

    @property
    def spec(self) -> catalog.NodeSpec | None:
        return catalog.spec(self.type)

    @property
    def kind(self) -> str:
        return catalog.kind_of(self.type)

    @property
    def is_trigger(self) -> bool:
        s = self.spec
        return bool(s and s.trigger) or self.type.lower().endswith("trigger")

    @property
    def is_note(self) -> bool:
        return self.type == "n8n-nodes-base.stickyNote"

    @property
    def is_sub_node(self) -> bool:
        s = self.spec
        return bool(s and s.sub_node)

    def output_names(self) -> list[str]:
        """Names of the main outputs, in index order (what n8n shows on the canvas)."""
        t, p = self.type, self.params
        if t == "n8n-nodes-base.if":
            names = ["true", "false"]
        elif t == "n8n-nodes-base.switch":
            rules = (p.get("rules") or {}).get("values") or []
            names = [r.get("outputKey") or str(i) for i, r in enumerate(rules)]
            if (p.get("options") or {}).get("fallbackOutput") == "extra":
                names.append((p.get("options") or {}).get("renameFallbackOutput") or "Fallback")
        elif t == "n8n-nodes-base.splitInBatches":
            names = ["done", "loop"]
        elif self.is_note or self.is_sub_node:
            names = []
        else:
            names = ["main"]
        if self.raw.get("onError") == "continueErrorOutput":
            names.append("error")
        return names

    def input_count(self) -> int:
        if self.is_trigger or self.is_note or self.is_sub_node:
            return 0
        if self.type == "n8n-nodes-base.merge":
            return int(self.params.get("numberInputs", 2))
        return 1


class Workflow:
    def __init__(self, raw: dict, path: Path | None = None):
        self.raw = raw
        self.path = path
        self.nodes = [Node(n) for n in raw.get("nodes") or [] if isinstance(n, dict)]
        self.by_name = {n.name: n for n in self.nodes}

    @classmethod
    def load(cls, path: str | Path) -> "Workflow":
        p = Path(path)
        return cls(json.loads(p.read_text(encoding="utf-8")), p)

    name = property(lambda self: self.raw.get("name", ""))
    id = property(lambda self: self.raw.get("id", ""))
    settings = property(lambda self: self.raw.get("settings") or {})

    @property
    def slug(self) -> str:
        return self.path.stem if self.path else self.id

    def __repr__(self) -> str:
        return f"Workflow({self.name!r}, {len(self.nodes)} nodes)"

    def edges(self, types: tuple[str, ...] | None = None) -> Iterator[Edge]:
        for src, by_type in (self.raw.get("connections") or {}).items():
            for ctype, outputs in (by_type or {}).items():
                if types and ctype not in types:
                    continue
                for out_idx, targets in enumerate(outputs or []):
                    for t in targets or []:
                        yield Edge(src, out_idx, t.get("node", ""), int(t.get("index", 0)), t.get("type", ctype))

    def main_children(self, name: str, output: int | None = None) -> list[Edge]:
        return [e for e in self.edges(("main",)) if e.source == name and (output is None or e.output == output)]

    def parents(self, name: str) -> list[Edge]:
        return [e for e in self.edges(("main",)) if e.target == name]

    def sub_nodes(self, root: str, ctype: str) -> list[Node]:
        """Sub-nodes (model, tools, memory, parser) wired into ``root`` with connection type ``ctype``."""
        return [self.by_name[e.source] for e in self.edges((ctype,)) if e.target == root and e.source in self.by_name]

    def triggers(self) -> list[Node]:
        return [n for n in self.nodes if n.is_trigger and not n.disabled]

    def reachable(self) -> set[str]:
        """Nodes reachable from any trigger along main connections, plus the sub-nodes of reachable roots."""
        seen: set[str] = set()
        stack = [t.name for t in self.triggers()]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(e.target for e in self.main_children(n))
        for e in self.edges(catalog.AI_CONNECTION_TYPES):
            if e.target in seen:
                seen.add(e.source)
        return seen

    def notes(self) -> list[str]:
        return [n.params.get("content", "") for n in self.nodes if n.is_note]


def walk_strings(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string inside a nested parameters object, with a dotted path for messages."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_strings(v, f"{path}[{i}]")


def load_all(directory: str | Path) -> list[Workflow]:
    return [Workflow.load(p) for p in sorted(Path(directory).glob("*.json"))]
