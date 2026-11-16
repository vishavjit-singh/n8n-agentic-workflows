"""README images: the architecture diagram and charts drawn from the demo's lint and simulation results."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

INK, MUTED, GRID = "#0e1a2b", "#5b6676", "#e3e7ee"
ACCENT, SIGNAL, BAD, GOOD, PURPLE = "#2a78d6", "#d98a00", "#e34948", "#1baf7a", "#6250d6"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "axes.edgecolor": GRID, "axes.labelcolor": MUTED, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.titleweight": "bold", "axes.titlesize": 12, "axes.titlecolor": INK,
    "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False, "figure.dpi": 150,
})


def _save(fig, out: Path, name: str) -> Path:
    fig.tight_layout()
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    fig.savefig(p, facecolor="white")
    plt.close(fig)
    return p


def architecture(out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(13, 6.2))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 6.6)
    ax.axis("off")

    def box(x, y, w, h, title, sub, color=INK):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc="white",
                                    ec=color, lw=2))
        ax.text(x + w / 2, y + h - 0.32, title, ha="center", va="center", fontsize=10.5, weight="bold", color=color)
        ax.text(x + w / 2, y + (h - 0.45) / 2, sub, ha="center", va="center", fontsize=8, color=MUTED,
                linespacing=1.45)

    def arrow(x1, y1, x2, y2, color=MUTED, style="-|>", ls="-"):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=13, lw=1.5, color=color,
                                     ls=ls))

    ax.text(0.15, 6.3, "n8n-agentic-workflows: templates checked, drawn and simulated in CI, then imported into "
            "a real n8n", fontsize=12.5, weight="bold", color=INK)
    box(0.2, 3.0, 2.6, 2.8, "workflows/*.json", "5 n8n exports\n\n00 shared error handler\n01 lead intake\n"
        "02 support triage agent\n03 meeting scheduler\n04 daily KPI digest", INK)
    stages = [("validate", "17 lint rules: graph,\nsecrets, emails, creds,\nretries, error workflow"),
              ("render", "graph PNG per workflow\nfrom node positions\nand connections"),
              ("simulate", "runs the graph on\nSYNTHETIC payloads with\nfake services · traces"),
              ("docs", "docs/workflows/*.md:\ncredentials, env vars,\nsetup, sample run")]
    ax.text(3.35, 5.95, "n8nkit (Python CLI) · `make demo`", fontsize=9, color=PURPLE, weight="bold")
    for i, (t, s) in enumerate(stages):
        box(3.35 + i * 2.4, 4.15, 2.15, 1.6, t, s, PURPLE)
    arrow(2.8, 4.95, 3.35, 4.95)
    for i in range(3):
        arrow(3.35 + i * 2.4 + 2.15, 4.95, 3.35 + (i + 1) * 2.4, 4.95)
    ax.text(3.4, 3.65, "simulator: IF / Switch / Set / Merge / Loop / Wait / Respond evaluated for real · Code nodes "
            "run in Node.js · retries and errorWorkflow honoured", fontsize=7.8, color=MUTED)
    ax.text(3.4, 3.35, "fakes: Sheets, Gmail, Slack, Calendar recorders · keyword-rule LLM checked against each "
            "parser schema · HTTP to the mock API in-process", fontsize=7.8, color=MUTED)

    ax.text(0.2, 0.08, "docker compose (CI job n8n-import; not run where this was built)", fontsize=9, color=ACCENT,
            weight="bold")
    box(0.2, 0.45, 2.4, 1.9, "postgres:16", "n8n database", ACCENT)
    box(3.0, 0.45, 2.6, 1.9, "n8n-import", "one-shot:\nn8n import:workflow\n--separate --input=/workflows",
        ACCENT)
    box(6.0, 0.45, 2.6, 1.9, "n8n (pinned)", "editor + webhooks :5678\nexecutions in postgres", ACCENT)
    box(9.0, 0.45, 2.3, 1.9, "mock-services", "Starlette :8080\n/kb/search · /orders/{id}\n/metrics/*", GOOD)
    box(11.6, 0.45, 1.3, 1.9, "Cloud APIs", "OpenAI\nGoogle\nWorkspace\nSlack", SIGNAL)
    arrow(1.5, 3.0, 4.3, 2.35, ACCENT)
    ax.text(2.95, 2.75, "mounted read-only", fontsize=7.4, color=MUTED)
    arrow(3.0, 1.4, 2.6, 1.4, MUTED)
    arrow(6.0, 1.4, 5.6, 1.4, MUTED)
    ax.text(5.62, 1.6, "starts\nafter", fontsize=7.4, color=MUTED)
    ax.text(2.62, 1.6, "writes", fontsize=7.4, color=MUTED)
    arrow(8.6, 1.4, 9.0, 1.4, GOOD)
    ax.text(8.63, 1.6, "HTTP\ntools", fontsize=7.4, color=MUTED)
    ax.add_patch(FancyArrowPatch((7.9, 2.35), (12.25, 2.35), arrowstyle="-|>", mutation_scale=13, lw=1.5,
                                 color=SIGNAL, ls="--", connectionstyle="arc3,rad=-0.25"))
    ax.text(10.1, 3.05, "credentials by reference", fontsize=7.4, color=MUTED, ha="center")
    return _save(fig, out, "architecture.png")


def lint_matrix(matrix: dict[str, dict[str, str]], out: Path) -> Path:
    rows = list(matrix)
    rules = list(next(iter(matrix.values())))
    fig, ax = plt.subplots(figsize=(13, 0.55 * len(rows) + 2.3))
    fill = {"pass": "#e3f5ec", "warning": "#fff1d6", "error": "#fde4e3"}
    edge = {"pass": GOOD, "warning": SIGNAL, "error": BAD}
    text = {"pass": "ok", "warning": "warn", "error": "fail"}
    for r, wf in enumerate(rows):
        for c, rule in enumerate(rules):
            st = matrix[wf][rule]
            ax.add_patch(FancyBboxPatch((c + 0.06, r + 0.1), 0.88, 0.8, boxstyle="round,pad=0,rounding_size=0.08",
                                        fc=fill[st], ec=edge[st], lw=1))
            ax.text(c + 0.5, r + 0.5, text[st], ha="center", va="center", fontsize=7.6,
                    color=INK, weight="bold" if st != "pass" else "normal")
    ax.set_xlim(0, len(rules))
    ax.set_ylim(len(rows), 0)
    ax.set_xticks([i + 0.5 for i in range(len(rules))], rules, rotation=40, ha="right", fontsize=8.2)
    ax.set_yticks([i + 0.5 for i in range(len(rows))], rows, fontsize=8.6)
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    bad = sum(v != "pass" for wf in rows if "broken" in wf for v in matrix[wf].values())
    shipped = [wf for wf in rows if "broken" not in wf]
    clean = all(v == "pass" for wf in shipped for v in matrix[wf].values())
    ax.set_title(f"`n8nkit validate`: {len(shipped)} templates × {len(rules)} rules "
                 f"{'all pass' if clean else 'have findings'}; the deliberately broken copy trips {bad} rules",
                 fontsize=12)
    return _save(fig, out, "lint_matrix.png")


def lead_routing(leads: dict, out: Path) -> Path:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.2), gridspec_kw={"width_ratios": [1, 1.25]})
    order = ["hot", "warm", "cold", "duplicate", "rejected"]
    labels = {"hot": "hot → Slack + reply draft", "warm": "warm → nurture draft", "cold": "cold → CRM row only",
              "duplicate": "duplicate → 200, skipped", "rejected": "invalid → 400"}
    vals = [leads["outcomes"].get(k, 0) for k in order]
    colors = [ACCENT, ACCENT, ACCENT, MUTED, MUTED]
    a1.barh([labels[k] for k in order], vals, color=colors, height=0.55)
    for i, v in enumerate(vals):
        a1.text(v + 0.4, i, str(v), va="center", fontsize=9.5, color=INK)
    a1.invert_yaxis()
    a1.set_xlim(0, max(vals) * 1.18)
    a1.grid(axis="x", color=GRID)
    a1.set_axisbelow(True)
    a1.set_title(f"{leads['submissions']} SYNTHETIC form posts: where each one went", fontsize=11)
    a1.set_xlabel("submissions")

    tiers = ["cold", "warm", "hot"]
    import random
    rng = random.Random(0)
    for i, t in enumerate(tiers):
        ys = leads["scores"].get(t, [])
        a2.scatter(ys, [i + rng.uniform(-0.18, 0.18) for _ in ys], s=46, color=ACCENT, edgecolor="white",
                   linewidth=1.5, zorder=3)
    for x, lab in ((40, "warm ≥ 40"), (70, "hot ≥ 70")):
        a2.axvline(x, color=MUTED, lw=1, ls="--")
        a2.text(x + 1, 2.45, lab, fontsize=8, color=MUTED)
    a2.set_yticks(range(3), [f"{t} ({len(leads['scores'].get(t, []))})" for t in tiers])
    a2.set_ylim(-0.6, 2.7)
    a2.set_xlim(-2, 102)
    a2.set_xlabel("score: intent + budget + urgency + company size (Code node, after the LLM labels)")
    a2.grid(axis="x", color=GRID)
    a2.set_axisbelow(True)
    a2.set_title("Scores by tier: the LLM labels, plain rules decide the tier", fontsize=11)
    fig.suptitle(f"Lead intake: {leads['outcomes'].get('hot', 0)} hot, {leads['outcomes'].get('warm', 0)} warm, "
                 f"{leads['outcomes'].get('cold', 0)} cold; {leads['outcomes'].get('duplicate', 0)} resubmissions "
                 "caught by the idempotency key", x=0.01, ha="left", fontsize=12.5, weight="bold", color=INK)
    return _save(fig, out, "lead_routing.png")


def support_outcomes(sup: dict, out: Path) -> Path:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.2), gridspec_kw={"width_ratios": [1.5, 1]})
    outcomes = [("auto_replied", "auto-replied", ACCENT), ("approved_and_sent", "approved in Slack, sent", GOOD),
                ("assigned_to_human", "handled by a person", SIGNAL)]
    cats = sorted(sup["outcomes_by_category"], key=lambda c: -sum(sup["outcomes_by_category"][c].values()))
    left = [0] * len(cats)
    for key, label, color in outcomes:
        vals = [sup["outcomes_by_category"][c].get(key, 0) for c in cats]
        a1.barh(cats, vals, left=left, color=color, height=0.55, label=label, edgecolor="white", linewidth=2)
        left = [a + b for a, b in zip(left, vals)]
    for i, total in enumerate(left):
        a1.text(total + 0.2, i, str(total), va="center", fontsize=9.5, color=INK)
    a1.invert_yaxis()
    a1.set_xlim(0, max(left) * 1.15)
    a1.grid(axis="x", color=GRID)
    a1.set_axisbelow(True)
    a1.legend(frameon=False, fontsize=8.5, loc="lower right")
    a1.set_xlabel("tickets")
    a1.set_title("Outcome by triage category", fontsize=11)

    tools = sup["tool_calls"]
    names = list(tools)
    a2.barh(names, [tools[n] for n in names], color=ACCENT, height=0.4)
    for i, n in enumerate(names):
        a2.text(tools[n] + 0.3, i, str(tools[n]), va="center", fontsize=9.5, color=INK)
    a2.invert_yaxis()
    a2.set_xlim(0, max(tools.values()) * 1.2)
    a2.grid(axis="x", color=GRID)
    a2.set_axisbelow(True)
    a2.set_xlabel("calls to the mock help-desk API")
    a2.set_title(f"Agent tool calls ({sup['tool_calls_per_ticket']} per ticket)", fontsize=11)
    o = sup["outcomes"]
    fig.suptitle(f"Support triage, {sup['tickets']} SYNTHETIC tickets: {o.get('auto_replied', 0)} answered "
                 f"automatically, {sup['escalated']} sent to Slack for approval "
                 f"({o.get('approved_and_sent', 0)} approved, {o.get('assigned_to_human', 0)} taken over)",
                 x=0.01, ha="left", fontsize=12.5, weight="bold", color=INK)
    return _save(fig, out, "support_outcomes.png")
