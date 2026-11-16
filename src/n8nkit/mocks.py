"""Mock help-desk and metrics API (Starlette). SYNTHETIC data only.

The support agent's HTTP tools and the KPI digest's HTTP Request nodes call these endpoints. In docker compose the
app runs as the ``mock-services`` container, so the imported n8n workflows have something real to call. The
simulator calls the same app in-process.

    GET /health
    GET /kb/search?q=...            top 3 knowledge-base articles with a 0-1 relevance score
    GET /orders/{order_id}          order record, 404 for unknown IDs
    GET /metrics/orders?date=...    per-team orders and revenue for one day, with the 7-day average
    GET /metrics/support?date=...   per-team ticket counts and first-response times

If ``MOCK_API_KEY`` is set, every endpoint except /health requires the header ``X-API-Key`` with that value.
"""
from __future__ import annotations

import os
import random
import re
import zlib
from datetime import date, timedelta

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

KB_ARTICLES = [
    {"id": "KB-101", "title": "How refunds work", "tags": "refund money back return credit card charged twice",
     "body": "Refunds go back to the original payment method within 5-7 business days after the return is "
             "received. Duplicate charges are reversed automatically within 3 days."},
    {"id": "KB-102", "title": "Shipping times and tracking", "tags": "shipping delivery tracking late where order "
     "arrive when parcel", "body": "Standard shipping takes 3-5 business days. A tracking link is emailed when "
                                  "the parcel leaves the warehouse."},
    {"id": "KB-103", "title": "Delayed or lost parcels", "tags": "delayed lost missing parcel not arrived carrier",
     "body": "If tracking has not updated for 5 days, we open a trace with the carrier. Lost parcels are "
             "reshipped or refunded once the carrier confirms."},
    {"id": "KB-104", "title": "Resetting your password", "tags": "password reset login forgot locked sign in",
     "body": "Use 'Forgot password' on the sign-in page. The reset link is valid for 30 minutes."},
    {"id": "KB-105", "title": "Two-factor authentication", "tags": "2fa two factor authentication code phone "
     "security account hacked", "body": "If you lost access to your 2FA device, support must verify your "
                                       "identity before 2FA can be reset."},
    {"id": "KB-106", "title": "Invoices and VAT", "tags": "invoice vat receipt billing tax gst company details",
     "body": "Invoices are available under Account > Orders. Company details can be added before checkout."},
    {"id": "KB-107", "title": "Changing or cancelling an order", "tags": "cancel change order address modify",
     "body": "Orders can be changed or cancelled until they are packed, usually within 2 hours of purchase."},
    {"id": "KB-108", "title": "Damaged or wrong item", "tags": "damaged broken wrong item defective replacement",
     "body": "Send a photo of the item and packaging; we ship a replacement at no cost."},
    {"id": "KB-109", "title": "Subscription billing", "tags": "subscription renew billing plan charge cancel "
     "subscription", "body": "Subscriptions renew on the same day each month. Cancel any time from Account > "
                             "Plan; access continues to the end of the period."},
    {"id": "KB-110", "title": "API rate limits", "tags": "api rate limit 429 integration developer error",
     "body": "The public API allows 60 requests per minute per key. Responses with status 429 include a "
             "Retry-After header."},
]

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "my", "is", "to", "and", "of", "for", "i", "it", "on", "in", "me", "was", "not", "have",
         "has", "with", "please", "can", "you", "your", "hi", "hello", "order", "help"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def kb_search(q: str, k: int = 3) -> list[dict]:
    terms = _words(q)
    if not terms:
        return []
    scored = []
    for a in KB_ARTICLES:
        vocab = _words(a["title"] + " " + a["tags"])
        hits = len(terms & vocab)
        if hits:
            scored.append((round(min(1.0, hits / min(len(terms), 3)), 2), a))
    scored.sort(key=lambda t: (-t[0], t[1]["id"]))
    return [{"id": a["id"], "title": a["title"], "body": a["body"], "score": s} for s, a in scored[:k]]


ORDER_STATUSES = ["delivered", "shipped", "processing", "delivered", "delayed", "shipped", "lost"]
BASE_DATE = date(2026, 11, 1)


def order_record(order_id: str) -> dict | None:
    m = re.fullmatch(r"ORD-(\d{5})", order_id.strip().upper())
    if not m or not 10000 <= int(m.group(1)) <= 19999:
        return None
    n = int(m.group(1))
    status = ORDER_STATUSES[n % len(ORDER_STATUSES)]
    placed = BASE_DATE + timedelta(days=n % 10)
    return {"order_id": f"ORD-{n}", "status": status, "carrier": ["DHL", "BlueDart", "FedEx"][n % 3],
            "total": round(20 + (n * 37) % 400 + (n % 100) / 100, 2), "currency": "USD",
            "placed_on": placed.isoformat(),
            "expected_delivery": (placed + timedelta(days=5)).isoformat(),
            "synthetic": True}


TEAMS = {"north": 5200.0, "south": 4100.0, "east": 6100.0, "west": 3600.0}


def _rng(*parts) -> random.Random:
    return random.Random(zlib.crc32("|".join(map(str, parts)).encode()))


def order_metrics(day: str) -> dict:
    rows = []
    for team, base in TEAMS.items():
        r = _rng("orders", day, team)
        avg = round(base * r.uniform(0.95, 1.05), 2)
        shock = r.choice([1.0] * 6 + [0.7, 0.75, 1.25])          # occasional drop or spike
        revenue = round(avg * shock * r.uniform(0.9, 1.1), 2)
        orders = max(1, int(revenue / r.uniform(45, 60)))
        refunds = sum(r.random() < (0.09 if r.random() < 0.15 else 0.025) for _ in range(orders))
        rows.append({"team": team, "lead_email": f"{team}.lead@example.com", "orders": orders,
                     "revenue": revenue, "revenue_7d_avg": avg, "refunds": refunds})
    return {"date": day, "orders_by_team": rows, "synthetic": True}


def support_metrics(day: str) -> dict:
    rows = []
    for team in TEAMS:
        r = _rng("support", day, team)
        avg = round(r.uniform(25, 45), 1)
        frt = round(avg * r.choice([1.0] * 6 + [1.4, 1.6, 0.8]) * r.uniform(0.9, 1.1), 1)
        opened = r.randint(30, 90)
        rows.append({"team": team, "tickets_opened": opened, "tickets_closed": opened - r.randint(-10, 15),
                     "first_response_min": frt, "first_response_7d_avg": avg})
    return {"date": day, "support_by_team": rows, "synthetic": True}


def _date_param(request: Request) -> str | None:
    d = request.query_params.get("date", "")
    try:
        return date.fromisoformat(d).isoformat()
    except ValueError:
        return None


def create_app(api_key: str | None = None) -> Starlette:
    key = api_key if api_key is not None else os.environ.get("MOCK_API_KEY") or None

    def guard(fn):
        async def wrapped(request: Request):
            if key and request.headers.get("x-api-key") != key:
                return JSONResponse({"error": "missing or wrong X-API-Key"}, status_code=401)
            return await fn(request)
        return wrapped

    async def health(_request):
        return JSONResponse({"status": "ok", "auth": bool(key)})

    async def search(request: Request):
        q = request.query_params.get("q", "")
        if not q.strip():
            return JSONResponse({"error": "query parameter q is required"}, status_code=400)
        return JSONResponse({"query": q, "results": kb_search(q)})

    async def order(request: Request):
        rec = order_record(request.path_params["order_id"])
        if rec is None:
            return JSONResponse({"error": "order not found"}, status_code=404)
        return JSONResponse(rec)

    async def metrics(request: Request):
        day = _date_param(request)
        if day is None:
            return JSONResponse({"error": "date=YYYY-MM-DD is required"}, status_code=400)
        kind = request.path_params["kind"]
        if kind == "orders":
            return JSONResponse(order_metrics(day))
        if kind == "support":
            return JSONResponse(support_metrics(day))
        return JSONResponse({"error": "unknown metrics"}, status_code=404)

    return Starlette(routes=[
        Route("/health", health),
        Route("/kb/search", guard(search)),
        Route("/orders/{order_id}", guard(order)),
        Route("/metrics/{kind}", guard(metrics)),
    ])


app = create_app()
