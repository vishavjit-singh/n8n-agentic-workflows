"""A minimal JSON Schema check for the Structured Output Parser schemas in the templates.

Supports ``type`` (including lists), ``properties``, ``required``, ``additionalProperties: false``, ``enum``,
``items``, ``minimum``/``maximum``, ``minLength``/``maxLength`` and ``minItems``/``maxItems``. The simulator runs every
fake LLM answer through the parser's own schema, so the fakes cannot drift from the contract in the workflow.
"""
from __future__ import annotations

from typing import Any

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(ValueError):
    pass


def errors(value: Any, schema: dict, path: str = "$") -> list[str]:
    out: list[str] = []
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, v in enumerate(value):
                out += errors(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for k in schema.get("required") or []:
            if k not in value:
                out.append(f"{path}: missing required '{k}'")
        for k, v in value.items():
            if k in props:
                out += errors(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected property '{k}'")
    return out


def validate(value: Any, schema: dict) -> None:
    errs = errors(value, schema)
    if errs:
        raise SchemaError("; ".join(errs))
