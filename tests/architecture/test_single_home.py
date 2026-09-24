"""AST enforcement of the Single-Home Registry (IMPLEMENTATION_PLAN.md §5) and CLAUDE.md
rules 4-5. Each rule is also run against a planted violation so it cannot pass vacuously."""

from __future__ import annotations

import ast
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from hlsignals.domain.direction import NON_PERP_DIRS, PERP_DIR_SIGN

SRC = Path(__file__).parents[2] / "src" / "hlsignals"
CLOCK = "core/clock.py"
SYMBOLS = "domain/symbols.py"
DIRECTION = "domain/direction.py"
PURE_PACKAGES = ("core/", "domain/", "signals/", "wallets/scoring/", "wallets/filters/", "session/")
IO_MODULES = frozenset(
    {"httpx", "requests", "socket", "urllib", "sqlite3", "websockets", "io", "shutil", "subprocess"}
)
TIME_ATTRS = frozenset({"now", "utcnow", "today", "time", "sleep", "monotonic", "perf_counter"})
TIME_OWNERS = frozenset({"datetime", "date", "time"})
DIR_LITERALS = frozenset(PERP_DIR_SIGN) | NON_PERP_DIRS

Rule = Callable[[ast.AST], Iterator[str]]


def reads_time(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in TIME_ATTRS
            and isinstance(node.value, ast.Name)
            and node.value.id in TIME_OWNERS
        ):
            yield f"line {node.lineno}: {node.value.id}.{node.attr}"
        if isinstance(node, ast.ImportFrom) and node.module == "time":
            names = {a.name for a in node.names} & TIME_ATTRS
            if names:
                yield f"line {node.lineno}: from time import {sorted(names)}"


def builds_symbol_strings(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == ":":
            yield f"line {node.lineno}: ':' literal"


def interprets_dir(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in DIR_LITERALS:
            yield f"line {node.lineno}: dir literal {node.value!r}"


def imports_non_stdlib(tree: ast.AST) -> Iterator[str]:
    for module, line in _imported_modules(tree):
        if module not in sys.stdlib_module_names and module not in {"__future__", "hlsignals"}:
            yield f"line {line}: imports {module}"


def does_io(tree: ast.AST) -> Iterator[str]:
    for module, line in _imported_modules(tree):
        if module in IO_MODULES:
            yield f"line {line}: imports {module}"
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "open"
        ):
            yield f"line {node.lineno}: open()"


def _imported_modules(tree: ast.AST) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0], node.lineno


def sources() -> Iterator[tuple[str, ast.AST]]:
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(), filename=str(path))


# (rule, applies-to predicate over the relative path)
RULES: dict[str, tuple[Rule, Callable[[str], bool]]] = {
    "only core/clock.py reads the clock or sleeps": (reads_time, lambda p: p != CLOCK),
    "only domain/symbols.py builds/splits 'dex:coin'": (
        builds_symbol_strings,
        lambda p: p != SYMBOLS,
    ),
    "only domain/direction.py interprets fill dir": (interprets_dir, lambda p: p != DIRECTION),
    "core imports stdlib only": (imports_non_stdlib, lambda p: p.startswith("core/")),
    "pure packages perform no I/O": (does_io, lambda p: p.startswith(PURE_PACKAGES)),
}

PLANTED = {
    "only core/clock.py reads the clock or sleeps": "x = datetime.now()",
    "only domain/symbols.py builds/splits 'dex:coin'": "s = dex + ':' + coin",
    "only domain/direction.py interprets fill dir": "if d == 'Open Long': pass",
    "core imports stdlib only": "import httpx",
    "pure packages perform no I/O": "data = open('f').read()",
}


@pytest.mark.parametrize("rule_name", sorted(RULES))
def test_rule_holds(rule_name: str) -> None:
    rule, applies = RULES[rule_name]
    violations = [
        f"{path} {hit}" for path, tree in sources() if applies(path) for hit in rule(tree)
    ]
    assert violations == []


@pytest.mark.parametrize("rule_name", sorted(RULES))
def test_rule_detects_planted_violation(rule_name: str) -> None:
    rule, _ = RULES[rule_name]
    assert list(rule(ast.parse(PLANTED[rule_name])))


def test_sources_found() -> None:
    assert any(path == CLOCK for path, _ in sources())
