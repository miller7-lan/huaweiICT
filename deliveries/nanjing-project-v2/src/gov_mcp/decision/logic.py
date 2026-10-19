"""Bounded JSON rule language with strict types and Kleene three-valued logic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
import math
import re
from typing import Any


class Truth(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class RuleError(ValueError):
    pass


def valid_value(value: Any, spec: dict) -> bool:
    kind = spec["kind"]
    if kind == "boolean":
        return type(value) is bool
    if kind == "number":
        try:
            return type(value) in (int, float) and math.isfinite(value)
        except OverflowError:
            return False
    if kind == "date":
        if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return False
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False
    return (kind in {"enum", "string"} and type(value) is str and 0 < len(value) <= 200
            and (kind != "enum" or value in spec["options"]))


def validate_expression(expression: dict, fields: dict[str, dict]) -> None:
    count = 0

    def visit(node, depth):
        nonlocal count
        count += 1
        if depth > 12 or count > 128:
            raise RuleError("EXPRESSION_BUDGET_EXCEEDED")
        if not isinstance(node, dict) or type(node.get("op")) is not str:
            raise RuleError("INVALID_EXPRESSION")
        op = node["op"]
        if op in {"all", "any"}:
            if set(node) != {"op", "args"} or not isinstance(node["args"], list) or not 1 <= len(node["args"]) <= 16:
                raise RuleError("INVALID_COMPOSITE")
            for child in node["args"]:
                visit(child, depth + 1)
            return
        if op == "not":
            if set(node) != {"op", "arg"}:
                raise RuleError("INVALID_NEGATION")
            visit(node["arg"], depth + 1)
            return
        if op == "const":
            if set(node) != {"op", "value"} or type(node["value"]) is not bool:
                raise RuleError("INVALID_CONSTANT")
            return
        if op not in {"eq", "ne", "in", "not_in", "lt", "le", "gt", "ge"} or set(node) != {"op", "field", "value"}:
            raise RuleError("UNSUPPORTED_OPERATOR")
        field = node["field"]
        if type(field) is not str or field not in fields:
            raise RuleError("UNREGISTERED_FIELD")
        spec = fields[field]
        values = node["value"] if op in {"in", "not_in"} else [node["value"]]
        if not isinstance(values, list) or not 1 <= len(values) <= 32 or any(not valid_value(v, spec) for v in values):
            raise RuleError("INVALID_COMPARISON_VALUE")
        if op in {"lt", "le", "gt", "ge"} and spec["kind"] not in {"number", "date"}:
            raise RuleError("UNORDERED_FIELD")

    visit(expression, 0)


def conjunction(values: list[Truth]) -> Truth:
    if Truth.FALSE in values:
        return Truth.FALSE
    return Truth.TRUE if all(v is Truth.TRUE for v in values) else Truth.UNKNOWN


def disjunction(values: list[Truth]) -> Truth:
    if Truth.TRUE in values:
        return Truth.TRUE
    return Truth.FALSE if all(v is Truth.FALSE for v in values) else Truth.UNKNOWN


@dataclass(frozen=True)
class Evaluation:
    result: Truth
    trace: dict
    missing_fields: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


def evaluate(expression: dict, facts: dict, fields: dict[str, dict]) -> Evaluation:
    """Malformed rules become UNKNOWN; never execute or partially trust them."""
    try:
        validate_expression(expression, fields)
    except RuleError as error:
        return Evaluation(Truth.UNKNOWN, {"result": "unknown", "reason": str(error)}, issues=(str(error),))

    def walk(node):
        op = node["op"]
        trace = {"op": op}
        missing, issues = set(), set()
        if op in {"all", "any", "not"}:
            children = [walk(child) for child in (node["args"] if op != "not" else [node["arg"]])]
            states = [child.result for child in children]
            result = (conjunction(states) if op == "all" else disjunction(states) if op == "any"
                      else {Truth.TRUE: Truth.FALSE, Truth.FALSE: Truth.TRUE, Truth.UNKNOWN: Truth.UNKNOWN}[states[0]])
            trace["children"] = [child.trace for child in children]
            for child in children:
                missing.update(child.missing_fields)
                issues.update(child.issues)
        elif op == "const":
            result = Truth.TRUE if node["value"] else Truth.FALSE
            trace["expected"] = node["value"]
        else:
            field, expected = node["field"], node["value"]
            actual = facts.get(field)
            trace.update(field=field, expected=expected)
            if actual is None:
                result = Truth.UNKNOWN
                trace["reason"] = "MISSING_FACT"
                missing.add(field)
            elif not valid_value(actual, fields[field]):
                result = Truth.UNKNOWN
                trace["reason"] = "INVALID_FACT_TYPE"
                missing.add(field)
                issues.add("INVALID_FACT_TYPE:" + field)
            else:
                trace["observed"] = actual
                # Type validation prevents Python's True == 1 from becoming a business match.
                match = {"eq": lambda: actual == expected, "ne": lambda: actual != expected,
                         "in": lambda: actual in expected, "not_in": lambda: actual not in expected,
                         "lt": lambda: actual < expected, "le": lambda: actual <= expected,
                         "gt": lambda: actual > expected, "ge": lambda: actual >= expected}[op]()
                result = Truth.TRUE if match else Truth.FALSE
        trace["result"] = result.value
        return Evaluation(result, trace, tuple(sorted(missing)), tuple(sorted(issues)))

    return walk(expression)
