"""``python -m n8nkit <command>``: validate, render, simulate, docs, demo, serve-mocks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import config


def _load(paths: tuple[str, ...]):
    from .model import Workflow, load_all

    if not paths:
        return load_all(config.WORKFLOWS)
    out = []
    for p in map(Path, paths):
        out += load_all(p) if p.is_dir() else [Workflow.load(p)]
    return out


@click.group()
def cli():
    """Tooling for the n8n workflow templates in workflows/."""


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--strict", is_flag=True, help="Fail on warnings too.")
@click.option("--json", "as_json", is_flag=True, help="Print findings as JSON.")
def validate(paths, strict, as_json):
    """Lint workflow exports (default: workflows/)."""
    from .lint import ERROR, RULES, lint

    wfs = _load(paths)
    findings = lint(wfs)
    if as_json:
        click.echo(json.dumps([f.as_dict() for f in findings], indent=2))
    else:
        for f in findings:
            click.echo(str(f))
        n_err = sum(f.severity == ERROR for f in findings)
        click.echo(f"{len(wfs)} workflows, {len(RULES)} rules: {n_err} errors, {len(findings) - n_err} warnings")
    if any(f.severity == ERROR or strict for f in findings):
        sys.exit(1)


@cli.command()
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option("--out", default=str(config.IMAGES / "workflows"), show_default=True)
def render(paths, out):
    """Draw each workflow to a PNG."""
    from .render import render_all

    for p in render_all(_load(paths), Path(out)):
        click.echo(f"wrote {p}")


@cli.command()
@click.argument("workflow")
@click.option("--payload", type=click.File("r"), help="JSON body for a webhook workflow ('-' for stdin).")
@click.option("--at", "at", default=None, help="Simulated current time, ISO 8601 (default 2026-11-16T09:00Z).")
@click.option("--json", "as_json", is_flag=True, help="Print the full execution record as JSON.")
def simulate(workflow, payload, at, as_json):
    """Run WORKFLOW (slug or id) in the local simulator with fake services."""
    from datetime import datetime

    from .engine import Simulator

    now = datetime.fromisoformat(at.replace("Z", "+00:00")) if at else None
    sim = Simulator(_load(()), now=now)
    wf = sim.workflow(workflow)
    if any(t.type.endswith(".webhook") for t in wf.triggers()):
        exe = sim.webhook(wf.slug, json.load(payload) if payload else {})
    else:
        exe = sim.schedule(wf.slug)
    if as_json:
        click.echo(json.dumps(exe.to_dict(), indent=2))
    else:
        click.echo(exe.trace_text())
        if exe.error_execution:
            click.echo("\n" + exe.error_execution.trace_text())
    sys.exit(0 if exe.status == "success" else 1)


@cli.command()
@click.option("--out", default=str(config.WORKFLOW_DOCS), show_default=True)
def docs(out):
    """Write docs/workflows/<slug>.md for every workflow."""
    from .demo import doc_samples
    from .docsgen import write_docs

    wfs = _load(())
    for p in write_docs(wfs, Path(out), doc_samples(wfs)):
        click.echo(f"wrote {p}")


@cli.command()
def demo():
    """Validate, render, simulate the SYNTHETIC scenarios, draw the charts and write the docs."""
    from .demo import run, summary_text

    result = run(config.WORKFLOWS, config.IMAGES, config.WORKFLOW_DOCS, config.REPORTS, log=click.echo)
    text = summary_text(result)
    (config.REPORTS / "summary.txt").write_text(text + "\n")
    click.echo("\n" + text)
    click.echo("\n--- sample trace: hot lead\n" + result["leads"]["sample_trace"])
    click.echo("\n--- sample trace: escalated ticket\n" + result["support"]["sample_trace"])
    click.echo("\n--- sample trace: failure drill\n" + result["failure_drill"]["sample_trace"] + "\n"
               + result["failure_drill"]["sample_error_trace"])


@cli.command("serve-mocks")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8080, show_default=True)
def serve_mocks(host, port):
    """Serve the mock help-desk/metrics API (SYNTHETIC data)."""
    import uvicorn

    uvicorn.run("n8nkit.mocks:app", host=host, port=port)


def main():
    cli()
