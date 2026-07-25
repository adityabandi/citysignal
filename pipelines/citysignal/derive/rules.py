"""A small safe evaluator for regime rules.

Rules live in config and are written by humans, so they must be readable; they
also decide what the site says about a real city, so they must not be `eval`.
This parses the expression to an AST and walks only the node types a threshold
rule needs — comparisons, boolean operators, numbers, named signals, and two
functions. Anything else raises rather than executing.
"""

from __future__ import annotations

import ast
import operator
from typing import Any, Callable, Mapping

_COMPARISONS: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}


class RuleError(ValueError):
    pass


class RuleEvaluator:
    """Evaluate a rule expression against a bag of named signal values.

    `delta(signal, n)` is the change in a signal over n periods; `available(x)`
    tests whether a signal exists at all. A rule that references a signal with no
    value evaluates to False rather than raising — a city missing a measure is
    not classified by it.
    """

    def __init__(
        self,
        values: Mapping[str, float | None],
        deltas: Mapping[tuple[str, int], float | None] | None = None,
    ) -> None:
        self.values = values
        self.deltas = deltas or {}

    def evaluate(self, expression: str) -> bool:
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            raise RuleError(f"cannot parse rule {expression!r}: {exc}") from exc
        result = self._walk(tree.body)
        return bool(result)

    def _walk(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float, bool)):
                return node.value
            raise RuleError(f"unsupported constant: {node.value!r}")

        if isinstance(node, ast.Name):
            # Rules are written in YAML, where `true` is the natural spelling.
            if node.id in {"true", "True"}:
                return True
            if node.id in {"false", "False"}:
                return False
            if node.id not in self.values:
                raise RuleError(
                    f"rule references unknown signal {node.id!r}; "
                    f"known signals: {', '.join(sorted(self.values))}"
                )
            return self.values.get(node.id)

        if isinstance(node, ast.BoolOp):
            parts = [self._walk(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(self._truthy(p) for p in parts)
            if isinstance(node.op, ast.Or):
                return any(self._truthy(p) for p in parts)
            raise RuleError("unsupported boolean operator")

        if isinstance(node, ast.UnaryOp):
            operand = self._walk(node.operand)
            if isinstance(node.op, ast.Not):
                return not self._truthy(operand)
            if isinstance(node.op, ast.USub):
                return -operand if operand is not None else None
            raise RuleError("unsupported unary operator")

        if isinstance(node, ast.Compare):
            left = self._walk(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._walk(comparator)
                if left is None or right is None:
                    return False  # a missing measure cannot satisfy a threshold
                func = _COMPARISONS.get(type(op))
                if func is None:
                    raise RuleError(f"unsupported comparison: {type(op).__name__}")
                if not func(left, right):
                    return False
                left = right
            return True

        if isinstance(node, ast.Call):
            return self._call(node)

        raise RuleError(f"unsupported expression node: {type(node).__name__}")

    def _call(self, node: ast.Call) -> Any:
        if not isinstance(node.func, ast.Name):
            raise RuleError("only plain function calls are allowed")
        name = node.func.id
        args = [self._walk(a) for a in node.args]

        if name == "abs":
            return abs(args[0]) if args and args[0] is not None else None
        if name == "delta":
            if len(node.args) != 2 or not isinstance(node.args[0], ast.Name):
                raise RuleError("delta(signal, periods) takes a signal name and an integer")
            return self.deltas.get((node.args[0].id, int(args[1])))
        if name == "available":
            if not isinstance(node.args[0], ast.Name):
                raise RuleError("available(signal) takes a signal name")
            return self.values.get(node.args[0].id) is not None
        raise RuleError(f"unknown function {name!r}")

    @staticmethod
    def _truthy(value: Any) -> bool:
        return bool(value) if value is not None else False


def classify(
    ruleset: dict,
    values: Mapping[str, float | None],
    deltas: Mapping[tuple[str, int], float | None] | None = None,
) -> dict[str, Any]:
    """Return the first matching rule. The fallback rule guarantees a result."""
    evaluator = RuleEvaluator(values, deltas)
    for rule in ruleset["rules"]:
        if evaluator.evaluate(rule["when"]):
            return {
                "rule_id": rule["id"],
                "label": rule["label"],
                "reading": (rule.get("reading") or "").strip(),
                "expression": rule["when"],
                "rules_version": ruleset["version"],
            }
    raise RuleError(
        f"{ruleset['version']} has no fallback rule — every ruleset must end with one that always matches"
    )
