"""Draw a workflow export as a PNG from its JSON: nodes at their canvas positions, coloured by kind."""
from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from . import catalog  # noqa: E402
from .model import Workflow  # noqa: E402

INK, MUTED, GRID = "#0e1a2b", "#5b6676", "#e3e7ee"
# trigger green, AI purple, logic a deliberately neutral slate, integration amber (CVD-checked as a set)
KIND_COLORS = {catalog.TRIGGER: "#1baf7a", catalog.AI: "#6250d6", catalog.LOGIC: "#5b6676",
               catalog.INTEGRATION: "#d98a00"}
KIND_LABELS = {catalog.TRIGGER: "trigger", catalog.AI: "AI (LLM, agent, model, tool, parser)",
               catalog.LOGIC: "logic (If, Switch, Set, Code, Merge, Loop, Wait, Respond)",
               catalog.INTEGRATION: "integration (HTTP, Google Sheets, Gmail, Calendar, Slack)"}
SUB_LABELS = {"ai_languageModel": "chat model", "ai_tool": "HTTP tool", "ai_memory": "memory",
              "ai_outputParser": "output parser"}
AI_EDGE_LABELS = {"ai_languageModel": "model", "ai_tool": "tool", "ai_memory": "memory",
                  "ai_outputParser": "parser"}
W, H, SW, SH = 180, 84, 172, 70   # node box sizes in canvas units (main, sub-node)


def _tint(hex_color: str, amount: float = 0.88) -> tuple[float, float, float]:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return (r + (1 - r) * amount, g + (1 - g) * amount, b + (1 - b) * amount)


def render(wf: Workflow, out: Path) -> Path:
    nodes = [n for n in wf.nodes if not n.is_note]
    xs = [n.position[0] for n in nodes]
    ys = [n.position[1] for n in nodes]
    x0, x1, y0, y1 = min(xs) - W, max(xs) + W, min(ys) - H * 1.6, max(ys) + H * 1.9
    width_in = min(18.0, max(8.0, (x1 - x0) / 140))
    height_in = width_in * (y1 - y0) / (x1 - x0) + 0.6
    fig, ax = plt.subplots(figsize=(width_in, max(3.2, height_in)))
    ax.set_xlim(x0, x1)
    ax.set_ylim(y1, y0)
    ax.axis("off")
    size = {n.name: (SW, SH) if n.is_sub_node else (W, H) for n in nodes}

    def box_edge(name, side):
        n = wf.by_name[name]
        x, y = n.position
        w, h = size[name]
        return {"right": (x + w / 2, y), "left": (x - w / 2, y), "top": (x, y - h / 2), "bottom": (x, y + h / 2)}[side]

    labelled: set[tuple[str, int]] = set()
    for e in wf.edges(("main",)):
        if e.source not in size or e.target not in size:
            continue
        src, dst = wf.by_name[e.source], wf.by_name[e.target]
        back = dst.position[0] <= src.position[0]
        if back:   # loop-back: leave from the bottom, curve under, enter from the bottom
            start, end, style = box_edge(e.source, "left"), box_edge(e.target, "bottom"), "angle3,angleA=180,angleB=90"
        else:
            start, end, style = box_edge(e.source, "right"), box_edge(e.target, "left"), "arc3,rad=0"
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=11, lw=1.3, color=MUTED,
                                     connectionstyle=style, shrinkA=2, shrinkB=2, zorder=1))
        names = src.output_names()
        if len(names) > 1 and (e.source, e.output) not in labelled:
            labelled.add((e.source, e.output))
            if back:
                lx, ly = start[0] - 40, start[1] - 12
            elif abs(end[1] - start[1]) < 1:
                lx, ly = (start[0] + end[0]) / 2, start[1] - 13
            else:
                f = 26 / max(26, abs(end[0] - start[0]))
                lx, ly = start[0] + (end[0] - start[0]) * f + 4, start[1] + (end[1] - start[1]) * min(0.45, f * 2.2)
            ax.text(lx, ly, names[e.output], fontsize=7.2, color=INK, ha="center", va="center", zorder=4,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec=GRID, lw=0.6))

    ai_edges = [e for e in wf.edges(catalog.AI_CONNECTION_TYPES) if e.source in size and e.target in size]
    for e in ai_edges:
        start, end = box_edge(e.source, "top"), box_edge(e.target, "bottom")
        siblings = sorted((x for x in ai_edges if x.target == e.target), key=lambda x: wf.by_name[x.source].position)
        if len(siblings) > 1:   # spread the arrow heads along the root's bottom edge
            k = siblings.index(e)
            end = (end[0] - W * 0.36 + k * (W * 0.72 / (len(siblings) - 1)), end[1])
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=9, lw=1.1, ls=(0, (3, 2)),
                                     color=KIND_COLORS[catalog.AI], shrinkA=2, shrinkB=2, zorder=1))
        mx, my = start[0] + (end[0] - start[0]) * 0.35, start[1] + (end[1] - start[1]) * 0.35
        ax.text(mx, my, AI_EDGE_LABELS.get(e.type, e.type), fontsize=7, color=KIND_COLORS[catalog.AI],
                ha="center", va="center", zorder=4, bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none"))
    ai_roots = {e.target for e in ai_edges}

    for n in nodes:
        x, y = n.position
        w, h = size[n.name]
        color = KIND_COLORS.get(n.kind, MUTED)
        ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle="round,pad=0,rounding_size=10",
                                    fc=_tint(color), ec=color, lw=1.6, zorder=2))
        label = "\n".join(textwrap.wrap(n.name, 15 if not n.is_sub_node else 16)[:2])
        if n.is_sub_node:
            sub = SUB_LABELS.get(n.spec.sub_node, "sub-node")
        else:
            sub = n.spec.label if n.spec else n.type.rsplit(".", 1)[-1]
            sub = {"Respond to Webhook": "Respond", "Edit Fields (Set)": "Set", "Basic LLM Chain": "LLM Chain",
                   "Schedule Trigger": "Schedule"}.get(sub, sub)
        flags = []
        if n.raw.get("retryOnFail"):
            flags.append(f"retry ×{n.raw.get('maxTries', 3)}")
        if n.type == "n8n-nodes-base.webhook":
            flags.append(f"{n.params.get('httpMethod', 'GET')} /{n.params.get('path')}")
        if n.type == "n8n-nodes-base.scheduleTrigger":
            iv = (n.params.get("rule") or {}).get("interval") or [{}]
            flags.append("cron " + iv[0].get("expression", ""))
        if n.type == "n8n-nodes-base.wait" and n.params.get("resume") == "webhook":
            flags.append(f"resume by URL, max {n.params.get('resumeAmount')} {n.params.get('resumeUnit')}")
        lines = 2 if "\n" in label else 1
        ty = y - (10 if lines == 2 else 9) * (1 if not n.is_sub_node else 0.8)
        ax.text(x, ty, label, ha="center", va="center", fontsize=8.4 if not n.is_sub_node else 7.4,
                weight="bold", color=INK, zorder=3, linespacing=1.05)
        ax.text(x, y + h / 2 - (11 if not n.is_sub_node else 8), sub, ha="center", va="center",
                fontsize=7 if not n.is_sub_node else 6.6, color=MUTED, zorder=3)
        if flags:
            fy = y - h / 2 - 12 if n.name in ai_roots else y + h / 2 + 12
            ax.text(x, fy, " · ".join(flags), ha="center", va="center", fontsize=6.6, color=MUTED,
                    zorder=3)

    creds = sorted({c for n in nodes for c in n.credentials})
    ax.set_title(f"{wf.name}  ·  {len(nodes)} nodes  ·  credentials: {', '.join(creds) or 'none'}",
                 loc="left", fontsize=11.5, weight="bold", color=INK, pad=8)
    handles = [Line2D([], [], marker="s", ls="", markersize=9, markerfacecolor=_tint(c), markeredgecolor=c,
                      label=KIND_LABELS[k]) for k, c in KIND_COLORS.items()]
    handles.append(Line2D([], [], color=KIND_COLORS[catalog.AI], ls=(0, (3, 2)), label="AI sub-node connection"))
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, -0.02), ncol=3, frameon=False, fontsize=7.6,
              handletextpad=0.4, columnspacing=1.2)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    return out


def render_all(workflows: list[Workflow], out_dir: Path) -> list[Path]:
    return [render(wf, out_dir / f"{wf.slug}.png") for wf in workflows]
