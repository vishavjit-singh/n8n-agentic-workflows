"""Seeded SYNTHETIC payloads for the demo: website leads, support tickets, meeting requests and calendar load.

Names, companies and addresses are invented; every email uses a reserved ``.example`` / ``example.com`` domain.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from . import mocks

FIRST = ["Asha", "Ravi", "Meera", "Arjun", "Nina", "Tomas", "Lena", "Omar", "Priya", "Daniel", "Sara", "Kabir",
         "Elena", "Jonas", "Aisha", "Marco", "Ishaan", "Freya", "Hugo", "Zara"]
LAST = ["Rao", "Mehta", "Kaur", "Silva", "Novak", "Berg", "Haddad", "Iyer", "Costa", "Fischer", "Okafor", "Sethi",
        "Larsen", "Moreau", "Gill", "Tanaka"]
COMPANIES = ["Northwind Labs", "Bluefin Retail", "Cobalt Health", "Juniper Freight", "Lumen Analytics",
             "Orchid Foods", "Pinecrest Legal", "Quarry Studio", "Redwood Clinics", "Saffron Travel",
             "Tidewater Energy", "Umber Logistics", "Vantage Schools", "Willow Insurance", "Zenith Motors"]
THINGS = ["an internal reporting dashboard", "a customer-support chatbot", "a data pipeline for our sales data",
          "an n8n automation for invoice processing", "a lead-scoring model", "a document-search assistant"]
MESSAGES = {
    "purchase": ["We need a quote to build us {thing}. {when}", "Could you send pricing for {thing}? {when}",
                 "We are ready to start a contract for {thing}. {when}"],
    "evaluation": ["Exploring options for {thing}; could you share case studies?",
                   "We are evaluating vendors for {thing}. {when}", "Comparing approaches to {thing} and would "
                   "like to learn more."],
    "support": ["Existing customer here: our dashboard is not working since the last update.",
                "We see an error message when exporting reports. Existing customer, account 4471."],
    "not_a_fit": ["I'm a final-year student looking for an internship in automation.",
                  "Partnership proposal: we could resell your services to our clients."],
    "spam": ["We offer SEO services and backlinks so you rank #1 on Google.",
             "Guaranteed traffic for your website with our crypto rewards program."],
}
WHEN = ["", "We need this ASAP.", "There is a hard deadline by Friday.", "Hoping to start next month.",
        "We are planning this quarter."]
BUDGETS = {"purchase": [0, 3000, 12000, 18000, 30000, 60000], "evaluation": [0, 0, 4000, 10000]}
INTENT_WEIGHTS = {"purchase": 0.32, "evaluation": 0.30, "support": 0.08, "not_a_fit": 0.15, "spam": 0.15}


def _slug(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def leads(n: int = 50, seed: int = 7, duplicates: int = 3, invalid: int = 2) -> list[dict]:
    """``n`` form submissions: ``n - duplicates - invalid`` unique leads, then resubmissions and broken payloads."""
    rng = random.Random(seed)
    base = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)
    out = []
    for i in range(n - duplicates - invalid):
        intent = rng.choices(list(INTENT_WEIGHTS), weights=list(INTENT_WEIGHTS.values()))[0]
        first, last, company = rng.choice(FIRST), rng.choice(LAST), rng.choice(COMPANIES)
        msg = rng.choice(MESSAGES[intent]).format(thing=rng.choice(THINGS), when=rng.choice(WHEN)).strip()
        out.append({
            "name": f"{first} {last}", "email": f"{first.lower()}.{last.lower()}{i}@{_slug(company)}.example",
            "company": company, "company_size": rng.choice([3, 8, 15, 40, 120, 300, 800]),
            "budget_usd": rng.choice(BUDGETS.get(intent, [0])),
            "source": rng.choice(["website", "website", "linkedin", "referral"]), "message": msg,
            "submitted_at": (base + timedelta(minutes=17 * i)).isoformat().replace("+00:00", "Z"),
        })
    for j in range(duplicates):   # the same person submits the same form again (different case, later time)
        again = dict(out[rng.randrange(len(out))])
        again["email"] = again["email"].upper()
        again["submitted_at"] = (base + timedelta(hours=20, minutes=j)).isoformat().replace("+00:00", "Z")
        out.append(again)
    broken = [{"name": "No Email", "company": "Unknown", "message": "Please call me back."},
              {"name": "Empty Message", "email": "empty.message@example.com", "message": "   "}]
    out += broken[:invalid]
    return out


def _order_with(status: str, rng: random.Random) -> str:
    candidates = [f"ORD-{n}" for n in range(10000, 10400) if mocks.order_record(f"ORD-{n}")["status"] == status]
    return rng.choice(candidates)


TICKETS = [
    ("Where is my parcel", "Order {delayed} has not arrived and tracking shows delayed."),
    ("Parcel lost", "Tracking for {lost} has not moved in a week. Is it lost?"),
    ("Delivery time", "Quick question: how long does standard shipping take for {shipped}?"),
    ("Reset password", "How do I reset my password? I forgot it."),
    ("Locked out, 2FA phone lost", "I lost my phone and cannot get the two factor code."),
    ("Account hacked", "Someone logged in to my account and changed my email. Please help."),
    ("Charged twice, refund", "I was charged twice for {delivered}. I want my money back."),
    ("Invoice with VAT details", "Where can I download an invoice with our company VAT number?"),
    ("Cancel order", "Please cancel order {processing}, I ordered the wrong size."),
    ("Damaged item", "The lamp in {delivered} arrived broken. Can I get a replacement?"),
    ("Subscription renewal charge", "Why was my subscription renewed? I want to cancel subscription."),
    ("API returns 429", "Our integration gets 429 errors from the API, what is the rate limit?"),
    ("Chargeback warning", "I will file a chargeback with my bank if the refund for {delivered} is not processed."),
    ("Gift cards", "Do you sell gift cards for overseas friends?"),
    ("Wrong order number", "My order ORD-99999 shows nothing. Where is it?"),
    ("Change delivery address", "Can I change the address for {processing}?"),
]


def tickets(n: int = 24, seed: int = 11) -> list[dict]:
    rng = random.Random(seed)
    customers = [f"customer{k:02d}@shop.example" for k in range(1, 19)]
    out = []
    for i in range(n):
        subject, body = TICKETS[i % len(TICKETS)] if i < len(TICKETS) else rng.choice(TICKETS)
        fill = {s: _order_with(s, rng) for s in ("delayed", "lost", "shipped", "delivered", "processing")}
        out.append({"ticket_id": f"T-{2001 + i}", "email": customers[i] if i < len(customers) else
                    rng.choice(customers[:6]), "subject": subject, "message": body.format(**fill)})
    return out


MEETING_REQUESTS = [
    "30 minutes tomorrow morning to discuss onboarding",
    "An hour on Thursday afternoon to review the proposal",
    "45 minutes on Wednesday afternoon about the data migration",
    "30 min on Wednesday afternoon to discuss the invoice backlog",
    "A quick call sometime to catch up",
    "60 minutes next week to review the roadmap",
    "30 minutes on Friday morning for a sprint demo",
    "45 min tomorrow afternoon to discuss the pilot results",
    "20 minutes on Wednesday afternoon about hiring",
    "Can we talk soon about pricing?",
    "90 minutes on Monday to review the architecture",
    "30 minutes on Friday morning to discuss support metrics",
]


def meetings() -> list[dict]:
    return [{"requester_email": f"requester{i + 1:02d}@client.example", "request_text": t}
            for i, t in enumerate(MEETING_REQUESTS)]


def calendar_load(seed: int = 5) -> list[tuple[str, str, str]]:
    """Existing busy slots for 16-27 Nov 2026 (UTC); Wednesday 18 Nov afternoon is fully booked."""
    rng = random.Random(seed)
    events = []
    day = datetime(2026, 11, 16, tzinfo=timezone.utc)
    for d in range(12):
        date = day + timedelta(days=d)
        if date.weekday() >= 5:
            continue
        for _ in range(rng.randint(1, 3)):
            start = date.replace(hour=rng.choice([9, 10, 11, 13, 14, 15]), minute=rng.choice([0, 30]))
            events.append((start, start + timedelta(minutes=rng.choice([30, 60, 90])), "Existing meeting"))
    wed = datetime(2026, 11, 18, 13, 0, tzinfo=timezone.utc)
    events.append((wed, wed + timedelta(hours=4), "Workshop (blocked)"))
    iso = lambda d: d.isoformat().replace("+00:00", "Z")  # noqa: E731
    return [(iso(s), iso(e), t) for s, e, t in events]
