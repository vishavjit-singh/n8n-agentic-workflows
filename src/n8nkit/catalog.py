"""What n8nkit knows about each n8n node type: its kind, the typeVersions it accepts, its outputs and credentials.

The versions listed are the ones these templates use and that n8n 1.x ships. A node type that is not in this
catalog is reported by the linter, so a typo in ``type`` is caught before an import.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TRIGGER, AI, LOGIC, INTEGRATION, NOTE = "trigger", "ai", "logic", "integration", "note"

AI_CONNECTION_TYPES = ("ai_languageModel", "ai_tool", "ai_memory", "ai_outputParser")
CONNECTION_TYPES = ("main",) + AI_CONNECTION_TYPES


@dataclass(frozen=True)
class NodeSpec:
    label: str
    kind: str
    versions: tuple[float, ...]
    external: bool = False            # calls a service outside n8n, so it should retry
    credential_types: tuple[str, ...] = ()
    sub_node: str | None = None       # connection type a sub-node emits (ai_languageModel, ai_tool, ...)
    accepts: tuple[str, ...] = field(default_factory=tuple)  # AI connection types a root node accepts
    trigger: bool = False


B, LC = "n8n-nodes-base.", "@n8n/n8n-nodes-langchain."

NODE_TYPES: dict[str, NodeSpec] = {
    B + "webhook": NodeSpec("Webhook", TRIGGER, (1, 1.1, 2, 2.1), trigger=True),
    B + "scheduleTrigger": NodeSpec("Schedule Trigger", TRIGGER, (1, 1.1, 1.2), trigger=True),
    B + "errorTrigger": NodeSpec("Error Trigger", TRIGGER, (1,), trigger=True),
    B + "manualTrigger": NodeSpec("Manual Trigger", TRIGGER, (1,), trigger=True),
    B + "respondToWebhook": NodeSpec("Respond to Webhook", LOGIC, (1, 1.1)),
    B + "if": NodeSpec("If", LOGIC, (2, 2.1, 2.2)),
    B + "switch": NodeSpec("Switch", LOGIC, (3, 3.1, 3.2)),
    B + "set": NodeSpec("Edit Fields (Set)", LOGIC, (3.3, 3.4)),
    B + "code": NodeSpec("Code", LOGIC, (2,)),
    B + "merge": NodeSpec("Merge", LOGIC, (3,)),
    B + "splitInBatches": NodeSpec("Loop Over Items", LOGIC, (3,)),
    B + "wait": NodeSpec("Wait", LOGIC, (1.1,)),
    B + "noOp": NodeSpec("No Operation", LOGIC, (1,)),
    B + "stickyNote": NodeSpec("Sticky Note", NOTE, (1,)),
    B + "httpRequest": NodeSpec("HTTP Request", INTEGRATION, (4.1, 4.2), external=True,
                                credential_types=("httpHeaderAuth", "httpBasicAuth", "oAuth2Api")),
    B + "googleSheets": NodeSpec("Google Sheets", INTEGRATION, (4.4, 4.5), external=True,
                                 credential_types=("googleSheetsOAuth2Api", "googleApi")),
    B + "gmail": NodeSpec("Gmail", INTEGRATION, (2, 2.1), external=True, credential_types=("gmailOAuth2",)),
    B + "googleCalendar": NodeSpec("Google Calendar", INTEGRATION, (1.1, 1.2), external=True,
                                   credential_types=("googleCalendarOAuth2Api",)),
    B + "slack": NodeSpec("Slack", INTEGRATION, (2.1, 2.2), external=True,
                          credential_types=("slackApi", "slackOAuth2Api")),
    LC + "agent": NodeSpec("AI Agent", AI, (1.6, 1.7), external=True,
                           accepts=("ai_languageModel", "ai_tool", "ai_memory", "ai_outputParser")),
    LC + "chainLlm": NodeSpec("Basic LLM Chain", AI, (1.4, 1.5), external=True,
                              accepts=("ai_languageModel", "ai_outputParser")),
    LC + "lmChatOpenAi": NodeSpec("OpenAI Chat Model", AI, (1, 1.1), credential_types=("openAiApi",),
                                  sub_node="ai_languageModel"),
    LC + "toolHttpRequest": NodeSpec("HTTP Request Tool", AI, (1.1,), credential_types=("httpHeaderAuth",),
                                     sub_node="ai_tool"),
    LC + "memoryBufferWindow": NodeSpec("Window Buffer Memory", AI, (1.2, 1.3), sub_node="ai_memory"),
    LC + "outputParserStructured": NodeSpec("Structured Output Parser", AI, (1.2,), sub_node="ai_outputParser"),
}


def spec(node_type: str) -> NodeSpec | None:
    return NODE_TYPES.get(node_type)


def kind_of(node_type: str) -> str:
    s = NODE_TYPES.get(node_type)
    if s:
        return s.kind
    if node_type.startswith(LC):
        return AI
    return INTEGRATION if node_type.startswith(B) else LOGIC


def short_type(node_type: str) -> str:
    s = NODE_TYPES.get(node_type)
    return s.label if s else node_type.rsplit(".", 1)[-1]
