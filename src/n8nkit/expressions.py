"""A small evaluator for n8n expressions (``={{ ... }}``), enough to run these templates in the simulator.

n8n expressions are JavaScript. This module parses the subset that workflow parameters normally use: member and
index access, ``$json`` / ``$('Node').item`` / ``$input`` / ``$execution`` / ``$env`` / ``$now``, comparisons,
``&& || ?? !``, ternaries, ``+ - * / %``, array and object literals, single-parameter arrow functions, and the common
string, array, number and Luxon ``DateTime`` methods. Anything outside that subset raises ``UnsupportedExpression``
instead of guessing, and the linter reports such expressions as warnings. Code nodes are not evaluated here; the
simulator runs their JavaScript with Node.js.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


class ExpressionError(Exception):
    """The expression is valid but failed while evaluating (as it would in n8n)."""


class UnsupportedExpression(ExpressionError):
    """The expression uses syntax or a function this evaluator does not implement."""


class _Undefined:
    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:
        return "undefined"

    def __bool__(self) -> bool:
        return False


UNDEFINED = _Undefined()

# --------------------------------------------------------------------------------------------- tokenizer

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+\.\d*|\.\d+|\d+)
  | (?P<str>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")
  | (?P<tmpl>`)
  | (?P<id>[A-Za-z_$][A-Za-z0-9_$]*)
  | (?P<op>===|!==|\?\?|\?\.|=>|==|!=|<=|>=|&&|\|\||[-+*/%<>!?:.,()\[\]{}])
""", re.X)

_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "`": "`", "0": "\0"}


def _unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), m.group(1)), s)


def tokenize(src: str) -> list[tuple[str, Any]]:
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise UnsupportedExpression(f"unexpected character {src[pos]!r} at {pos}")
        pos = m.end()
        kind = m.lastgroup
        text = m.group()
        if kind == "ws":
            continue
        if kind == "tmpl":
            raise UnsupportedExpression("template literals are not supported in expressions")
        if kind == "num":
            out.append(("num", float(text) if "." in text else int(text)))
        elif kind == "str":
            out.append(("str", _unescape(text[1:-1])))
        elif kind == "id":
            out.append(("kw" if text in ("true", "false", "null", "undefined", "typeof") else "id", text))
        else:
            out.append(("op", text))
    out.append(("eof", None))
    return out


# --------------------------------------------------------------------------------------------- parser

_BINARY = {"??": 1, "||": 2, "&&": 3, "==": 6, "!=": 6, "===": 6, "!==": 6, "<": 7, ">": 7, "<=": 7, ">=": 7,
           "+": 9, "-": 9, "*": 10, "/": 10, "%": 10}


class _Parser:
    def __init__(self, src: str):
        self.toks = tokenize(src)
        self.i = 0

    def peek(self, k: int = 0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def accept(self, op: str) -> bool:
        if self.peek() == ("op", op):
            self.i += 1
            return True
        return False

    def expect(self, op: str):
        if not self.accept(op):
            raise UnsupportedExpression(f"expected {op!r}, got {self.peek()[1]!r}")

    def parse(self):
        node = self.expr()
        if self.peek()[0] != "eof":
            raise UnsupportedExpression(f"unexpected {self.peek()[1]!r}")
        return node

    def expr(self):
        # single-parameter arrow function: x => body
        if self.peek()[0] == "id" and self.peek(1) == ("op", "=>"):
            name = self.next()[1]
            self.next()
            return ("arrow", name, self.expr())
        cond = self.binary(0)
        if self.accept("?"):
            a = self.expr()
            self.expect(":")
            b = self.expr()
            return ("cond", cond, a, b)
        return cond

    def binary(self, min_prec: int):
        left = self.unary()
        while True:
            t = self.peek()
            prec = _BINARY.get(t[1]) if t[0] == "op" else None
            if prec is None or prec <= min_prec:
                return left
            self.next()
            right = self.binary(prec)
            left = ("logic" if t[1] in ("&&", "||", "??") else "bin", t[1], left, right)

    def unary(self):
        t = self.peek()
        if t in (("op", "!"), ("op", "-"), ("op", "+"), ("kw", "typeof")):
            self.next()
            return ("unary", t[1], self.unary())
        return self.postfix(self.primary())

    def postfix(self, node):
        while True:
            if self.accept("."):
                t = self.next()
                if t[0] not in ("id", "kw"):
                    raise UnsupportedExpression("expected a property name after '.'")
                node = ("member", node, t[1], False)
            elif self.accept("?."):
                if self.accept("["):
                    node = ("index", node, self.expr(), True)
                    self.expect("]")
                else:
                    node = ("member", node, self.next()[1], True)
            elif self.accept("["):
                node = ("index", node, self.expr(), False)
                self.expect("]")
            elif self.accept("("):
                args = []
                if not self.accept(")"):
                    while True:
                        args.append(self.expr())
                        if self.accept(")"):
                            break
                        self.expect(",")
                node = ("call", node, args)
            else:
                return node

    def primary(self):
        kind, val = self.next()
        if kind in ("num", "str"):
            return ("lit", val)
        if kind == "kw":
            return ("lit", {"true": True, "false": False, "null": None, "undefined": UNDEFINED}[val])
        if kind == "id":
            return ("id", val)
        if (kind, val) == ("op", "("):
            node = self.expr()
            self.expect(")")
            return node
        if (kind, val) == ("op", "["):
            items = []
            if not self.accept("]"):
                while True:
                    items.append(self.expr())
                    if self.accept("]"):
                        break
                    self.expect(",")
                    if self.accept("]"):
                        break
            return ("arr", items)
        if (kind, val) == ("op", "{"):
            pairs = []
            if not self.accept("}"):
                while True:
                    k = self.next()
                    if k[0] not in ("id", "str", "kw", "num"):
                        raise UnsupportedExpression("unsupported object key")
                    self.expect(":")
                    pairs.append((str(k[1]), self.expr()))
                    if self.accept("}"):
                        break
                    self.expect(",")
                    if self.accept("}"):
                        break
            return ("obj", pairs)
        raise UnsupportedExpression(f"unexpected token {val!r}")


_CACHE: dict[str, Any] = {}


def parse(src: str):
    if src not in _CACHE:
        _CACHE[src] = _Parser(src).parse()
    return _CACHE[src]


# --------------------------------------------------------------------------------------------- values

class DateTimeValue:
    """The parts of Luxon's DateTime that expressions in these templates use."""

    _WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    _MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    def __init__(self, dt: datetime):
        self.dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def __repr__(self) -> str:
        return f"DateTime({self.to_iso()})"

    def to_iso(self) -> str:
        s = self.dt.isoformat(timespec="milliseconds")
        return s.replace("+00:00", "Z")

    def millis(self) -> float:
        return self.dt.timestamp() * 1000

    def _shift(self, sign: int, d: Any) -> "DateTimeValue":
        if not isinstance(d, dict):
            raise UnsupportedExpression("plus/minus take an object such as {days: 1}")
        delta = timedelta(weeks=d.get("weeks", 0), days=d.get("days", 0), hours=d.get("hours", 0),
                          minutes=d.get("minutes", 0), seconds=d.get("seconds", 0))
        return DateTimeValue(self.dt + sign * delta)

    def to_format(self, fmt: str) -> str:
        out, i = [], 0
        dt = self.dt
        tokens = [("yyyy", f"{dt.year:04d}"), ("cccc", self._WEEKDAYS[dt.weekday()]),
                  ("ccc", self._WEEKDAYS[dt.weekday()][:3]), ("LLL", self._MONTHS[dt.month - 1]),
                  ("MM", f"{dt.month:02d}"), ("dd", f"{dt.day:02d}"), ("HH", f"{dt.hour:02d}"),
                  ("mm", f"{dt.minute:02d}"), ("ss", f"{dt.second:02d}"), ("M", str(dt.month)), ("d", str(dt.day))]
        while i < len(fmt):
            if fmt[i] == "'":
                j = fmt.index("'", i + 1)
                out.append(fmt[i + 1:j])
                i = j + 1
                continue
            for tok, val in tokens:
                if fmt.startswith(tok, i):
                    out.append(val)
                    i += len(tok)
                    break
            else:
                out.append(fmt[i])
                i += 1
        return "".join(out)

    def js_get(self, name: str):
        methods: dict[str, Callable] = {
            "toISO": self.to_iso, "toString": self.to_iso, "toJSON": self.to_iso,
            "toFormat": self.to_format, "toMillis": self.millis, "valueOf": self.millis,
            "plus": lambda d: self._shift(1, d), "minus": lambda d: self._shift(-1, d),
            "startOf": lambda unit: DateTimeValue(self.dt.replace(hour=0, minute=0, second=0, microsecond=0))
            if unit == "day" else _unsupported(f"startOf({unit!r})"),
        }
        if name in methods:
            return methods[name]
        props = {"year": self.dt.year, "month": self.dt.month, "day": self.dt.day, "hour": self.dt.hour,
                 "minute": self.dt.minute, "weekday": self.dt.isoweekday()}
        if name in props:
            return props[name]
        raise UnsupportedExpression(f"DateTime.{name} is not supported")

    @staticmethod
    def from_iso(s: str) -> "DateTimeValue":
        try:
            return DateTimeValue(datetime.fromisoformat(str(s).replace("Z", "+00:00")))
        except ValueError as e:
            raise ExpressionError(f"invalid ISO date {s!r}") from e


def _unsupported(what: str):
    raise UnsupportedExpression(f"{what} is not supported")


class JSObject:
    """A host object exposing a fixed set of properties to expressions."""

    def __init__(self, props: dict[str, Any], label: str = "object"):
        self.props = props
        self.label = label

    def js_get(self, name: str):
        if name in self.props:
            return self.props[name]
        raise UnsupportedExpression(f"{self.label}.{name} is not supported")


def to_number(v: Any) -> float:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if v is None:
        return 0
    if v is UNDEFINED:
        return math.nan
    if isinstance(v, DateTimeValue):
        return v.millis()
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return 0
        try:
            return int(s) if re.fullmatch(r"-?\d+", s) else float(s)
        except ValueError:
            return math.nan
    return math.nan


def truthy(v: Any) -> bool:
    if v is None or v is UNDEFINED or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0 and not (isinstance(v, float) and math.isnan(v))
    if isinstance(v, str):
        return v != ""
    return True


def to_js_string(v: Any) -> str:
    if v is None:
        return "null"
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return ",".join("" if x is None or x is UNDEFINED else to_js_string(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    if isinstance(v, DateTimeValue):
        return v.to_iso()
    return str(v)


def _js_type(v: Any) -> str:
    if v is UNDEFINED:
        return "undefined"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if callable(v):
        return "function"
    return "object"


def strict_equals(a: Any, b: Any) -> bool:
    ta, tb = _js_type(a), _js_type(b)
    if ta != tb:
        return False
    if ta == "object" and (isinstance(a, (dict, list)) or isinstance(b, (dict, list))):
        return a is b
    return a == b


def loose_equals(a: Any, b: Any) -> bool:
    if (a is None or a is UNDEFINED) and (b is None or b is UNDEFINED):
        return True
    if a is None or a is UNDEFINED or b is None or b is UNDEFINED:
        return False
    if _js_type(a) == _js_type(b):
        return strict_equals(a, b)
    return to_number(a) == to_number(b)


def _round(x: float) -> float:
    return math.floor(x + 0.5)


def _to_fixed(n: float, digits: int = 0) -> str:
    return f"{round(n + 0.0, int(digits)):.{int(digits)}f}"


def _callback(fn):
    if not callable(fn):
        raise ExpressionError("callback is not a function")
    return fn


def _string_member(s: str, name: str):
    m = {
        "length": len(s),
        "toLowerCase": lambda: s.lower(), "toUpperCase": lambda: s.upper(), "trim": lambda: s.strip(),
        "includes": lambda x: to_js_string(x) in s, "startsWith": lambda x: s.startswith(to_js_string(x)),
        "endsWith": lambda x: s.endswith(to_js_string(x)), "indexOf": lambda x: s.find(to_js_string(x)),
        "split": lambda sep=UNDEFINED: [s] if sep is UNDEFINED else (list(s) if sep == "" else s.split(sep)),
        "slice": lambda a=0, b=None: s[int(a):None if b in (None, UNDEFINED) else int(b)],
        "substring": lambda a=0, b=None: s[int(a):None if b in (None, UNDEFINED) else int(b)],
        "replace": lambda a, b: s.replace(to_js_string(a), to_js_string(b), 1),
        "replaceAll": lambda a, b: s.replace(to_js_string(a), to_js_string(b)),
        "padStart": lambda n, c=" ": s.rjust(int(n), c[:1] or " "),
        "toString": lambda: s,
    }
    if name in m:
        return m[name]
    raise UnsupportedExpression(f"String.{name} is not supported")


def _array_member(a: list, name: str):
    m = {
        "length": len(a),
        "join": lambda sep=",": to_js_string(sep).join("" if x is None or x is UNDEFINED else to_js_string(x)
                                                       for x in a),
        "includes": lambda x: any(strict_equals(x, y) for y in a),
        "slice": lambda s=0, e=None: a[int(s):None if e in (None, UNDEFINED) else int(e)],
        "map": lambda fn: [_callback(fn)(x) for x in a],
        "filter": lambda fn: [x for x in a if truthy(_callback(fn)(x))],
        "some": lambda fn: any(truthy(_callback(fn)(x)) for x in a),
        "every": lambda fn: all(truthy(_callback(fn)(x)) for x in a),
        "find": lambda fn: next((x for x in a if truthy(_callback(fn)(x))), UNDEFINED),
        "indexOf": lambda x: next((i for i, y in enumerate(a) if strict_equals(x, y)), -1),
        "concat": lambda *xs: a + [y for x in xs for y in (x if isinstance(x, list) else [x])],
    }
    if name in m:
        return m[name]
    raise UnsupportedExpression(f"Array.{name} is not supported")


def get_member(obj: Any, name: Any, optional: bool = False):
    if obj is None or obj is UNDEFINED:
        if optional:
            return UNDEFINED
        raise ExpressionError(f"Cannot read properties of {to_js_string(obj)} (reading '{to_js_string(name)}')")
    if hasattr(obj, "js_get"):
        return obj.js_get(str(name))
    if isinstance(obj, dict):
        return obj.get(str(name), UNDEFINED)
    if isinstance(obj, list):
        if isinstance(name, (int, float)) and not isinstance(name, bool):
            i = int(name)
            return obj[i] if 0 <= i < len(obj) else UNDEFINED
        return _array_member(obj, str(name))
    if isinstance(obj, str):
        if isinstance(name, (int, float)) and not isinstance(name, bool):
            i = int(name)
            return obj[i] if 0 <= i < len(obj) else UNDEFINED
        return _string_member(obj, str(name))
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if name == "toFixed":
            return lambda d=0: _to_fixed(obj, d)
        if name == "toString":
            return lambda: to_js_string(obj)
        raise UnsupportedExpression(f"Number.{name} is not supported")
    if isinstance(obj, bool) and name == "toString":
        return lambda: to_js_string(obj)
    raise UnsupportedExpression(f"property {name!r} on {type(obj).__name__} is not supported")


def _json_stringify(v: Any, _replacer=None, indent=None) -> str:
    def conv(x):
        if x is UNDEFINED:
            return None
        if isinstance(x, DateTimeValue):
            return x.to_iso()
        if isinstance(x, dict):
            return {k: conv(v) for k, v in x.items() if v is not UNDEFINED}
        if isinstance(x, list):
            return [conv(v) for v in x]
        return x
    if isinstance(indent, (int, float)) and indent:
        return json.dumps(conv(v), indent=int(indent), ensure_ascii=False)
    return json.dumps(conv(v), separators=(",", ":"), ensure_ascii=False)


def builtins() -> dict[str, Any]:
    return {
        "Math": JSObject({"round": _round, "floor": math.floor, "ceil": math.ceil, "abs": abs,
                          "min": lambda *a: min(to_number(x) for x in a), "PI": math.pi,
                          "max": lambda *a: max(to_number(x) for x in a)}, "Math"),
        "JSON": JSObject({"stringify": _json_stringify, "parse": json.loads}, "JSON"),
        "String": to_js_string, "Number": to_number, "Boolean": truthy,
        "parseInt": lambda s, base=10: int(float(to_number(s))), "parseFloat": lambda s: float(to_number(s)),
        "Object": JSObject({"keys": lambda o: list(o), "values": lambda o: list(o.values()),
                            "entries": lambda o: [[k, v] for k, v in o.items()]}, "Object"),
        "Array": JSObject({"isArray": lambda x: isinstance(x, list)}, "Array"),
        "DateTime": JSObject({"fromISO": DateTimeValue.from_iso}, "DateTime"),
    }


# --------------------------------------------------------------------------------------------- evaluator

def _eval(node, scope: dict[str, Any]):
    t = node[0]
    if t == "lit":
        return node[1]
    if t == "id":
        if node[1] in scope:
            return scope[node[1]]
        raise UnsupportedExpression(f"{node[1]} is not defined")
    if t == "member":
        return get_member(_eval(node[1], scope), node[2], node[3])
    if t == "index":
        return get_member(_eval(node[1], scope), _eval(node[2], scope), node[3])
    if t == "call":
        fn = _eval(node[1], scope)
        if not callable(fn):
            raise ExpressionError("expression is not a function")
        return fn(*[_eval(a, scope) for a in node[2]])
    if t == "arr":
        return [_eval(x, scope) for x in node[1]]
    if t == "obj":
        return {k: _eval(v, scope) for k, v in node[1]}
    if t == "arrow":
        name, body = node[1], node[2]
        return lambda x=UNDEFINED, *_: _eval(body, {**scope, name: x})
    if t == "cond":
        return _eval(node[2] if truthy(_eval(node[1], scope)) else node[3], scope)
    if t == "unary":
        v = _eval(node[2], scope)
        if node[1] == "!":
            return not truthy(v)
        if node[1] == "typeof":
            return _js_type(v)
        n = to_number(v)
        return -n if node[1] == "-" else n
    if t == "logic":
        a = _eval(node[2], scope)
        if node[1] == "&&":
            return _eval(node[3], scope) if truthy(a) else a
        if node[1] == "||":
            return a if truthy(a) else _eval(node[3], scope)
        return a if a is not None and a is not UNDEFINED else _eval(node[3], scope)
    if t == "bin":
        op, a, b = node[1], _eval(node[2], scope), _eval(node[3], scope)
        if op == "+":
            if isinstance(a, (str, list, dict)) or isinstance(b, (str, list, dict)):
                return to_js_string(a) + to_js_string(b)
            return to_number(a) + to_number(b)
        if op in ("-", "*", "/", "%"):
            x, y = to_number(a), to_number(b)
            if op == "-":
                return x - y
            if op == "*":
                return x * y
            if y == 0:
                return math.nan if op == "%" or x == 0 else math.copysign(math.inf, x)
            return x / y if op == "/" else math.fmod(x, y)
        if op == "===":
            return strict_equals(a, b)
        if op == "!==":
            return not strict_equals(a, b)
        if op == "==":
            return loose_equals(a, b)
        if op == "!=":
            return not loose_equals(a, b)
        if isinstance(a, str) and isinstance(b, str):
            x, y = a, b
        else:
            x, y = to_number(a), to_number(b)
        return {"<": x < y, ">": x > y, "<=": x <= y, ">=": x >= y}[op]
    raise UnsupportedExpression(f"unknown node {t}")


def evaluate(src: str, scope: dict[str, Any]) -> Any:
    return _eval(parse(src), {**builtins(), **scope})


_SEGMENT = re.compile(r"\{\{(.*?)\}\}", re.S)


def is_expression(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("=")


def expression_bodies(value: str) -> list[str]:
    """The JavaScript inside each ``{{ }}`` of an n8n parameter value."""
    return [m.group(1).strip() for m in _SEGMENT.finditer(value[1:])] if is_expression(value) else []


def render(value: Any, scope: dict[str, Any]) -> Any:
    """Resolve one parameter value the way n8n does.

    ``"={{ expr }}"`` returns the expression's value with its type; any other text around the braces makes the
    result a string. Values that do not start with ``=`` are literals and are returned unchanged.
    """
    if not is_expression(value):
        return value
    body = value[1:]
    whole = _SEGMENT.fullmatch(body.strip())
    if whole and body.count("{{") == 1:
        out = evaluate(whole.group(1).strip(), scope)
        return out.to_iso() if isinstance(out, DateTimeValue) else out

    def sub(m):
        v = evaluate(m.group(1).strip(), scope)
        if isinstance(v, (dict, list)):
            return _json_stringify(v)
        return "" if v is None or v is UNDEFINED else to_js_string(v)
    return _SEGMENT.sub(sub, body)


def render_deep(obj: Any, scope: dict[str, Any]) -> Any:
    """``render`` applied to every string inside nested parameters."""
    if isinstance(obj, dict):
        return {k: render_deep(v, scope) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_deep(v, scope) for v in obj]
    return render(obj, scope)


def to_plain(v: Any) -> Any:
    """Convert evaluator values to JSON-safe Python (undefined dropped, DateTime as ISO)."""
    if v is UNDEFINED:
        return None
    if isinstance(v, DateTimeValue):
        return v.to_iso()
    if isinstance(v, dict):
        return {k: to_plain(x) for k, x in v.items() if x is not UNDEFINED}
    if isinstance(v, list):
        return [to_plain(x) for x in v]
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v
