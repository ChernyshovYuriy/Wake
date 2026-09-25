"""AST enforcement of the Single-Home Registry (IMPLEMENTATION_PLAN.md §5) and CLAUDE.md
rules 4-5. Each rule is also run against a planted violation so it cannot pass vacuously."""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from hlsignals.domain.direction import FORCED_CLOSE_DIRS, NON_PERP_DIRS, PERP_DIR_SIGN

SRC = Path(__file__).parents[2] / "src" / "hlsignals"
CLOCK = "core/clock.py"
SYMBOLS = "domain/symbols.py"
DIRECTION = "domain/direction.py"
# Package prefixes ("x/") or single modules ("x.py"); each must match a real source file.
PURE_PACKAGES = (
    "core/",
    "domain/",
    "signals/",
    "wallets/scoring/",
    "wallets/filters.py",
    "session/",
    "backtest/asof.py",
)
IO_MODULES = frozenset(
    {
        "httpx",
        "requests",
        "socket",
        "ssl",
        "http",
        "urllib",
        "ftplib",
        "smtplib",
        "sqlite3",
        "websockets",
        "asyncio",
        "io",
        "os",
        "pathlib",
        "glob",
        "shutil",
        "tempfile",
        "tomllib",
        "pickle",
        "shelve",
        "subprocess",
    }
)
# Methods that touch the file system whatever object they are called on (Path and friends).
IO_METHODS = frozenset(
    {
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "open",
        "mkdir",
        "unlink",
        "touch",
        "iterdir",
        "glob",
        "rglob",
    }
)
# Fully qualified clock/sleep entry points. Bare "datetime.now"/"date.today" cover code that
# uses the name without an import we can see.
FORBIDDEN_TIME = frozenset(
    {
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
        "datetime.now",
        "datetime.utcnow",
        "datetime.today",
        "date.today",
        "time.time",
        "time.time_ns",
        "time.monotonic",
        "time.monotonic_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
        "time.process_time",
        "time.process_time_ns",
        "time.sleep",
        "asyncio.sleep",
    }
)
DIR_LITERALS = frozenset(PERP_DIR_SIGN) | NON_PERP_DIRS | FORCED_CLOSE_DIRS
SPLITTERS = frozenset({"split", "rsplit", "partition", "rpartition"})
FINDERS = frozenset({"index", "rindex", "find", "rfind"})
DEX_PREFIX = re.compile(r"[a-z0-9]+:")  # "xyz:" glued to a coin
COIN_SUFFIX = re.compile(r":[A-Za-z0-9]+")  # ":NVDA" glued to a dex
PLACEHOLDER_COLON = re.compile(r"(%[sr]|\{[^{}]*\}):(%[sr]|\{[^{}]*\})")  # "%s:%s", "{}:{}"
SYMBOLS_RULE = "only domain/symbols.py builds/splits 'dex:coin'"
CLOCK_RULE = "only core/clock.py reads the clock or sleeps"
DIR_RULE = "only domain/direction.py interprets fill dir"
IO_RULE = "pure packages perform no I/O"
DIR_READ_RULE = "only domain/direction.py reads Fill.dir"

Rule = Callable[[ast.AST], Iterator[str]]


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """Local name -> the qualified name it was imported as."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = (
                    a.name if a.asname else a.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    return ".".join([node.id, *reversed(parts)])


def reads_time(tree: ast.AST) -> Iterator[str]:
    """Clock reads and sleeps under any import spelling: aliases, qualified names, from-imports."""
    aliases = _import_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                if f"{node.module}.{a.name}" in FORBIDDEN_TIME:
                    yield f"line {node.lineno}: from {node.module} import {a.name}"
        elif isinstance(node, ast.Attribute) and (dotted := _dotted(node)) is not None:
            head, _, rest = dotted.partition(".")
            resolved = f"{aliases.get(head, head)}.{rest}"
            if resolved in FORBIDDEN_TIME:
                yield f"line {node.lineno}: {dotted} ({resolved})"


def builds_symbol_strings(tree: ast.AST) -> Iterator[str]:
    """':' used to join or split dex and coin, by any route: ``+``, f-strings, ``%``,
    ``.format``, ``join``, a named separator, ``split``/``partition`` (also ``sep=``),
    ``index``/``find``, or a constant already glued to one side (``"xyz:" + coin``)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            if any(_is_colon(o) or _is_glued_part(o) for o in (node.left, node.right)):
                yield f"line {node.lineno}: '+' with ':'"
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            if _is_colon_template(node.left):
                yield f"line {node.lineno}: '%' template joined with ':'"
        elif isinstance(node, ast.JoinedStr):
            if any(isinstance(v, ast.FormattedValue) for v in node.values) and any(
                _is_colon(v) for v in node.values
            ):
                yield f"line {node.lineno}: f-string joined with ':'"
        elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            if _is_colon(node.value):
                yield f"line {node.lineno}: named ':' separator"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            yield from _symbol_calls(node, node.func.attr)


def _symbol_calls(node: ast.Call, method: str) -> Iterator[str]:
    first = node.args[0] if node.args else None
    if method == "join" and _is_colon(node.func.value):  # type: ignore[attr-defined]
        yield f"line {node.lineno}: ':'.join"
    if method == "format" and _is_colon_template(node.func.value):  # type: ignore[attr-defined]
        yield f"line {node.lineno}: .format template joined with ':'"
    sep = next((k.value for k in node.keywords if k.arg == "sep"), None)
    if method in SPLITTERS and (_is_colon(first) or _is_colon(sep)):
        yield f"line {node.lineno}: .{method}(':')"
    if method in FINDERS and _is_colon(first):
        yield f"line {node.lineno}: .{method}(':')"


def _is_colon(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == ":"


def _is_glued_part(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and bool(DEX_PREFIX.fullmatch(node.value) or COIN_SUFFIX.fullmatch(node.value))
    )


def _is_colon_template(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and bool(PLACEHOLDER_COLON.search(node.value))
    )


def _dir_fragments() -> frozenset[str]:
    """Word-boundary prefixes and suffixes of every dir literal ("Open", "Close ", " Long",
    "Liquidated Isolated", ...): what prefix/suffix matching on dir would use."""
    fragments: set[str] = set()
    for literal in DIR_LITERALS:
        words = literal.split(" ")
        for i in range(1, len(words)):
            head, tail = " ".join(words[:i]), " ".join(words[i:])
            fragments |= {head, head + " ", tail, " " + tail}
    return frozenset(f for f in fragments if f.strip() not in {"", ">"})


DIR_FRAGMENTS = _dir_fragments()


def interprets_dir(tree: ast.AST) -> Iterator[str]:
    """dir literals, dir fragments used for prefix/suffix/containment tests, and constant
    concatenations that spell a dir literal."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value in DIR_LITERALS:
            yield f"line {node.lineno}: dir literal {node.value!r}"
        elif isinstance(node, ast.BinOp) and _folded(node) in DIR_LITERALS:
            yield f"line {node.lineno}: dir literal assembled as {_folded(node)!r}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"startswith", "endswith"} and node.args:
                arg = node.args[0]
                options = arg.elts if isinstance(arg, ast.Tuple) else [arg]
                if any(_is_dir_fragment(o) for o in options):
                    yield f"line {node.lineno}: dir matched with .{node.func.attr}"
        elif (
            isinstance(node, ast.Compare)
            and _is_dir_fragment(node.left)
            and any(isinstance(op, ast.In | ast.NotIn) for op in node.ops)
        ):
            yield f"line {node.lineno}: dir fragment tested with 'in'"


def reads_fill_dir(tree: ast.AST) -> Iterator[str]:
    """``x.dir`` or ``getattr(x, "dir")`` read anywhere: dir is interpreted only by
    domain/direction.py, which rejects unknown values. Code that reads dir itself could act on
    a value nobody validated (AUDIT.md F1-3). Constructing a Fill (``dir=``) is not a read."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "dir"
            and isinstance(node.ctx, ast.Load)
        ):
            yield f"line {node.lineno}: .dir read"
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "dir"
        ):
            yield f"line {node.lineno}: getattr(..., 'dir')"


def _is_dir_fragment(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and (
        node.value in DIR_FRAGMENTS or node.value in DIR_LITERALS
    )


def _folded(node: ast.AST) -> str | None:
    """The value of a '+' chain of string constants, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left), _folded(node.right)
        if left is not None and right is not None:
            return left + right
    return None


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
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in IO_METHODS
        ):
            yield f"line {node.lineno}: .{node.func.attr}()"


def _imported_modules(tree: ast.AST) -> Iterator[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module.split(".")[0], node.lineno


# Explicit, greppable opt-out for a ':' that is not a dex:coin symbol (e.g. host:port).
# Honoured by the symbol rule only; every other rule has no opt-out.
SYMBOL_EXEMPT = "# not-a-symbol"


def sources() -> Iterator[tuple[str, ast.AST]]:
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(), filename=str(path))


def exempt(path: str, hit: str) -> bool:
    line_no = int(hit.split()[1].rstrip(":"))
    return SYMBOL_EXEMPT in (SRC / path).read_text().splitlines()[line_no - 1]


# (rule, applies-to predicate over the relative path)
RULES: dict[str, tuple[Rule, Callable[[str], bool]]] = {
    CLOCK_RULE: (reads_time, lambda p: p != CLOCK),
    SYMBOLS_RULE: (builds_symbol_strings, lambda p: p != SYMBOLS),
    DIR_RULE: (interprets_dir, lambda p: p != DIRECTION),
    DIR_READ_RULE: (reads_fill_dir, lambda p: p != DIRECTION),
    "core imports stdlib only": (imports_non_stdlib, lambda p: p.startswith("core/")),
    IO_RULE: (does_io, lambda p: p.startswith(PURE_PACKAGES)),
}


# (rule name, planted source that must be detected). Beyond the obvious spelling, each rule
# gets the variants that slipped through before (AUDIT.md F2-2..F2-5): aliases, qualified
# names, other formatting routes, prefix/suffix matching, other I/O modules.
PLANTED = [
    (CLOCK_RULE, "x = datetime.now()"),
    (CLOCK_RULE, "from time import sleep"),
    (CLOCK_RULE, "from datetime import datetime as dt\nx = dt.now()"),
    (CLOCK_RULE, "import datetime\nx = datetime.datetime.now()"),
    (CLOCK_RULE, "import datetime as d\nx = d.date.today()"),
    (CLOCK_RULE, "import time\nx = time.time_ns()"),
    (CLOCK_RULE, "import time as t\nx = t.time()"),
    (CLOCK_RULE, "from time import monotonic_ns as tick"),
    (CLOCK_RULE, "import asyncio\nasync def f():\n    await asyncio.sleep(1)"),
    (CLOCK_RULE, "from asyncio import sleep"),
    (SYMBOLS_RULE, "s = dex + ':' + coin"),
    (SYMBOLS_RULE, "s = f'{dex}:{coin}'"),
    (SYMBOLS_RULE, "s = ':'.join([dex, coin])"),
    (SYMBOLS_RULE, "dex, coin = s.split(':')"),
    (SYMBOLS_RULE, "dex, _, coin = s.partition(':')"),
    (SYMBOLS_RULE, "s = 'xyz:' + coin"),
    (SYMBOLS_RULE, "s = dex + ':NVDA'"),
    (SYMBOLS_RULE, "s = '%s:%s' % (dex, coin)"),
    (SYMBOLS_RULE, "s = '{}:{}'.format(dex, coin)"),
    (SYMBOLS_RULE, "SEP = ':'\ns = dex + SEP + coin"),
    (SYMBOLS_RULE, "parts = s.split(sep=':')"),
    (SYMBOLS_RULE, "dex = s[: s.index(':')]"),
    (SYMBOLS_RULE, "i = s.find(':')"),
    (DIR_RULE, "if d == 'Open Long': pass"),
    (DIR_RULE, "x = d.startswith('Close')"),
    (DIR_RULE, "x = d.endswith('Long')"),
    (DIR_RULE, "x = d.startswith(('Open', 'Close'))"),
    (DIR_RULE, "x = 'Short' in d"),
    (DIR_RULE, "x = d.startswith('Liquidated')"),
    (DIR_RULE, "SIGN = {'Open ' + 'Long': 1}"),
    (DIR_READ_RULE, "d = fill.dir"),
    (DIR_READ_RULE, "n = sum(1 for f in fills if f.dir)"),
    (DIR_READ_RULE, "d = getattr(fill, 'dir')"),
    ("core imports stdlib only", "import httpx"),
    (IO_RULE, "data = open('f').read()"),
    (IO_RULE, "from pathlib import Path\nx = Path('f').read_text()"),
    (IO_RULE, "x = config_path.read_bytes()"),
    (IO_RULE, "import os\nx = os.listdir('.')"),
    (IO_RULE, "import tomllib"),
    (IO_RULE, "import glob"),
    (IO_RULE, "import asyncio"),
]

# (rule name, source that must NOT be flagged): legitimate code that resembles a violation.
CLEAN = [
    (CLOCK_RULE, "from datetime import time\nopen_at = time(9, 30)"),
    (CLOCK_RULE, "x = clock.now()\nself._sleeper.sleep(1)"),
    (CLOCK_RULE, "from datetime import datetime, UTC\nx = datetime.fromtimestamp(0, UTC)"),
    (CLOCK_RULE, "t = fill.time_ms"),
    (SYMBOLS_RULE, "json.dumps(x, separators=(',', ':'))"),
    (SYMBOLS_RULE, "d = {'a': 1}"),
    (SYMBOLS_RULE, "msg = f'value: {x}'"),
    (SYMBOLS_RULE, "msg = f'{a}: {b}'"),
    (SYMBOLS_RULE, "label = 'note: ' + text"),
    (SYMBOLS_RULE, "stamp = when.strftime('%H:%M')"),
    (SYMBOLS_RULE, "msg = '{:.2f} h'.format(x)"),
    (DIR_RULE, "side = 'long'"),
    (DIR_RULE, "x = PositionSide.LONG"),
    (DIR_RULE, "x = name.startswith('Longest')"),
    (DIR_RULE, "x = 'Long' + ' term'"),
    (DIR_READ_RULE, "fill = Fill(dir=raw['dir'])"),
    (DIR_READ_RULE, "bad = replace(fill, dir='Mystery')"),
    (DIR_READ_RULE, "names = dir(module)"),
    (IO_RULE, "import math\nfrom collections.abc import Mapping\nx = sorted(values)"),
    (IO_RULE, "x = calendar.is_open(t)"),
]


@pytest.mark.parametrize("rule_name", sorted(RULES))
def test_rule_holds(rule_name: str) -> None:
    rule, applies = RULES[rule_name]
    violations = [
        f"{path} {hit}"
        for path, tree in sources()
        if applies(path)
        for hit in rule(tree)
        if not (rule_name == SYMBOLS_RULE and exempt(path, hit))
    ]
    assert violations == []


def test_exemption_marker_is_rare_and_only_on_real_non_symbol_colons() -> None:
    marked = [
        (path, line)
        for path, _ in sources()
        for line in (SRC / path).read_text().splitlines()
        if SYMBOL_EXEMPT in line
    ]
    assert len(marked) <= 3, marked  # an escape hatch, not a habit


@pytest.mark.parametrize(("rule_name", "source"), PLANTED)
def test_rule_detects_planted_violation(rule_name: str, source: str) -> None:
    rule, _ = RULES[rule_name]
    assert list(rule(ast.parse(source)))


def test_every_rule_has_a_planted_violation() -> None:
    assert {name for name, _ in PLANTED} == set(RULES)


@pytest.mark.parametrize(("rule_name", "source"), CLEAN)
def test_rule_ignores_look_alikes(rule_name: str, source: str) -> None:
    rule, _ = RULES[rule_name]
    assert list(rule(ast.parse(source))) == []


def test_sources_found() -> None:
    assert any(path == CLOCK for path, _ in sources())


@pytest.mark.parametrize("entry", PURE_PACKAGES)
def test_every_pure_entry_matches_a_source(entry: str) -> None:
    """A pure entry that matches no file silently exempts that code from the I/O rule."""
    assert any(path.startswith(entry) for path, _ in sources())
