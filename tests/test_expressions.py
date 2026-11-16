from datetime import datetime, timezone

import pytest

from n8nkit import expressions as ex
from n8nkit.engine import NodeRef


def scope(**kw):
    base = {"$json": {"score": 72, "tier": "hot", "name": " Asha ", "tags": ["a", "b"], "body": {"email": "X@Y.Z"}},
            "$now": ex.DateTimeValue(datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)), "$env": {"BASE": "http://m"}}
    base.update(kw)
    return base


CASES = [
    ("$json.score >= 70", True),
    ("$json.score > 70 && $json.tier === 'warm'", False),
    ("$json.missing ?? 'fallback'", "fallback"),
    ("$json.score >= 70 ? 'hot' : 'cold'", "hot"),
    ("$json.name.trim().toLowerCase()", "asha"),
    ("$json.tags.join(', ')", "a, b"),
    ("$json.tags.map(t => t.toUpperCase()).length", 2),
    ("$json['body'].email.toLowerCase()", "x@y.z"),
    ("$json.body?.missing?.deeper", ex.UNDEFINED),
    ("(0.1 * 3).toFixed(2)", "0.30"),
    ("1 + '2'", "12"),
    ("'5' == 5 && '5' !== 5", True),
]


def test_javascript_subset():
    for src, expected in CASES:
        assert ex.evaluate(src, scope()) == expected, src


def test_typed_vs_interpolated_rendering():
    s = scope()
    assert ex.render("={{ $json.score }}", s) == 72                       # whole expression keeps its type
    assert ex.render("=Score: {{ $json.score }}/100", s) == "Score: 72/100"
    assert ex.render("={{ { \"status\": \"ok\", \"tier\": $json.tier } }}", s) == {"status": "ok", "tier": "hot"}
    assert ex.render("plain text", s) == "plain text"                     # no leading '=' means a literal
    assert ex.render("={{ $env.BASE }}/kb/search", s) == "http://m/kb/search"


def test_luxon_dates():
    s = scope()
    assert ex.render("={{ $now.minus({ days: 1 }).toFormat('yyyy-MM-dd') }}", s) == "2026-11-15"
    assert ex.render("={{ $now.toISO() }}", s) == "2026-11-16T09:00:00.000Z"
    assert ex.render("={{ DateTime.fromISO('2026-11-19T17:00:00Z').plus({ days: 3 }).toISO() }}", s) == \
        "2026-11-22T17:00:00.000Z"
    assert ex.evaluate("$now.toFormat('cccc')", s) == "Monday"


def test_node_references():
    items = [{"json": {"lead_id": "L1"}}, {"json": {"lead_id": "L2"}}]
    s = scope(**{"$": lambda name: NodeRef(items, 1)})
    assert ex.evaluate("$('Score lead').item.json.lead_id", s) == "L2"
    assert ex.evaluate("$('Score lead').first().json.lead_id", s) == "L1"
    assert ex.evaluate("$('Score lead').all().length", s) == 2


def test_errors_are_explicit():
    with pytest.raises(ex.ExpressionError, match="Cannot read properties of undefined"):
        ex.evaluate("$json.missing.deeper", scope())
    with pytest.raises(ex.UnsupportedExpression):
        ex.parse("`template ${literal}`")
    with pytest.raises(ex.UnsupportedExpression):
        ex.evaluate("$json.name.localeCompare('b')", scope())
