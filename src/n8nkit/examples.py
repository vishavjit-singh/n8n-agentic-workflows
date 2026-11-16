"""A deliberately broken copy of the lead workflow, built in code, to show what the linter catches.

The bad values (a fake key, a token-looking header) are assembled at runtime so that no secret-shaped string is
committed to the repository.
"""
from __future__ import annotations

import copy

from .model import Workflow

FAKE_KEY = "sk-" + "EXAMPLEONLY" * 3          # matches the OpenAI-style pattern; not a real key


def broken_lead_workflow(lead: Workflow) -> Workflow:
    raw = copy.deepcopy(lead.raw)
    raw["id"] = "BrokenLeadCopy01"
    raw["name"] = "Lead intake (broken copy)"
    raw["active"] = True                                              # export-settings (warning)
    raw["settings"].pop("errorWorkflow", None)                        # error-workflow
    nodes = {n["name"]: n for n in raw["nodes"]}
    nodes["Append CRM row"].pop("retryOnFail", None)                  # retries
    nodes["Lead webhook"]["parameters"]["path"] = "support-ticket"    # webhook-paths (clashes with 02)
    nodes["OpenAI Chat Model"]["typeVersion"] = 9                     # node-versions
    nodes["Reject invalid"]["name"] = "Accept lead"                   # unique-names + dangling connection
    nodes["Alert sales on Slack"]["parameters"]["text"] += " cc sales.team@northwind-labs.io"   # emails
    nodes["Alert sales on Slack"]["credentials"]["slackApi"]["data"] = {"token": "inline"}     # credentials
    nodes["Score lead"]["parameters"]["mode"] = "runOnceForEachItem"
    nodes["Draft nurture reply"]["parameters"]["subject"] = "={{ `Thanks ${$json.name}` }}"   # expressions (warn)
    raw["nodes"].append({                                              # secrets + orphans
        "parameters": {"method": "POST", "url": "https://crm.example.com/api/leads", "sendHeaders": True,
                       "headerParameters": {"parameters": [{"name": "Authorization", "value": f"Bearer {FAKE_KEY}"}]},
                       "options": {}},
        "id": "0f0e0d0c-0b0a-4909-8807-060504030201", "name": "Push to CRM API",
        "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1680, 520],
        "retryOnFail": True, "maxTries": 3, "waitBetweenTries": 1000})
    return Workflow(raw, lead.path.with_name("broken-lead-copy.json") if lead.path else None)
