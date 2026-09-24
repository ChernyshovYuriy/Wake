"""Phase 0: capture sanitized golden fixtures from the live Hyperliquid /info API.

Each fixture file is ``{"request": <payload>, "response": <json>}`` so that the future
``FixtureTransport`` can index fixtures by canonical request payload.

Sanitization: every wallet address (in requests and responses) is replaced by a
deterministic pseudonym, so fixtures stay internally consistent without committing
third-party addresses. Pseudonyms remain valid ``0x`` + 40-hex addresses.

Usage:  python scripts/capture_fixtures.py [--out tests/fixtures/hl]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

INFO_URL = "https://api.hyperliquid.xyz/info"
EQUITY_DEX = "xyz"
SECOND_DEX = "para"
PROBE_COIN = "xyz:NVDA"
FILL_WINDOW_MS = 3 * 86_400_000
CANDLE_1H_WINDOW_MS = 3 * 86_400_000
CANDLE_1D_WINDOW_MS = 30 * 86_400_000
PAGE_CAP = 2000
MAX_PROBED_USERS = 40
PACING_SECONDS = 2.5
MAX_ATTEMPTS = 8
HTTP_OK = 200
HTTP_TOO_MANY = 429
ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
KEEP_ADDRESS_KEYS = {"hash", "cloid", "deployer", "oracleUpdater", "feeRecipient"}


def pseudonym(address: str) -> str:
    digest = hashlib.sha256(("hlsignals-fixture:" + address.lower()).encode()).hexdigest()
    return "0x" + digest[:40]


def sanitize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: sanitize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, key) for v in value]
    if isinstance(value, str) and key not in KEEP_ADDRESS_KEYS:
        return ADDRESS_RE.sub(lambda m: pseudonym(m.group(0)), value)
    return value


class Info:
    def __init__(self) -> None:
        self._client = httpx.Client(timeout=30)

    def raw(self, payload: dict[str, Any]) -> tuple[int, Any]:
        for attempt in range(MAX_ATTEMPTS):
            resp = self._client.post(INFO_URL, json=payload)
            if resp.status_code == HTTP_TOO_MANY:
                time.sleep(PACING_SECONDS * 2**attempt)
                continue
            time.sleep(PACING_SECONDS)
            try:
                return resp.status_code, resp.json()
            except ValueError:
                return resp.status_code, resp.text
        raise SystemExit(f"rate limited too long on {payload}")

    def post(self, payload: dict[str, Any]) -> Any:
        status, body = self.raw(payload)
        if status != HTTP_OK:
            raise SystemExit(f"HTTP {status} for {payload}: {body!r}")
        return body


def save(out: Path, name: str, request: dict[str, Any], response: Any, status: int = 200) -> None:
    doc: dict[str, Any] = {"request": sanitize(request), "response": sanitize(response)}
    if status != HTTP_OK:
        doc["status"] = status
    path = out / f"{name}.json"
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"wrote {path} ({path.stat().st_size // 1024} KiB)")


def pick_users(info: Info, out: Path, now_ms: int, dir_examples: dict[str, Any]) -> tuple[str, str]:
    """Return (active_user, heavy_user) seen trading PROBE_COIN.

    active_user: has 0 < fills < PAGE_CAP in the fixture window.
    heavy_user: has >= PAGE_CAP fills in the window (exercises the page cap).
    The first xyz clearinghouseState that holds positions is saved immediately (positions
    can close between calls). Every fill seen contributes one example per ``dir`` value.
    """
    trades = info.post({"type": "recentTrades", "coin": PROBE_COIN})
    users: list[str] = []
    for t in trades:
        users.extend(u for u in t["users"] if u not in users)
    active = heavy = None
    positioned = False
    for user in users[:MAX_PROBED_USERS]:
        fills = info.post(
            {"type": "userFillsByTime", "user": user, "startTime": now_ms - FILL_WINDOW_MS}
        )
        for fill in fills:
            dir_examples.setdefault(fill["dir"], fill)
        if len(fills) >= PAGE_CAP:
            heavy = heavy or user
        elif fills:
            active = active or user
        if not positioned:
            payload = {"type": "clearinghouseState", "user": user, "dex": EQUITY_DEX}
            state = info.post(payload)
            if state["assetPositions"]:
                save(out, "clearinghouse_positioned_xyz", payload, state)
                positioned = True
        if active and heavy and positioned:
            return active, heavy
    raise SystemExit("could not find suitable users; rerun later")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/hl"))
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    info = Info()
    now_ms = int(time.time() * 1000)

    simple: dict[str, dict[str, Any]] = {
        "perp_dexs": {"type": "perpDexs"},
        "meta_and_ctxs_xyz": {"type": "metaAndAssetCtxs", "dex": EQUITY_DEX},
        "meta_and_ctxs_para": {"type": "metaAndAssetCtxs", "dex": SECOND_DEX},
        "l2_book_xyz_nvda": {"type": "l2Book", "coin": PROBE_COIN},
        "recent_trades_xyz_nvda": {"type": "recentTrades", "coin": PROBE_COIN},
        "candles_xyz_nvda_1h": {
            "type": "candleSnapshot",
            "req": {
                "coin": PROBE_COIN,
                "interval": "1h",
                "startTime": now_ms - CANDLE_1H_WINDOW_MS,
                "endTime": now_ms,
            },
        },
        "candles_xyz_nvda_1d": {
            "type": "candleSnapshot",
            "req": {
                "coin": PROBE_COIN,
                "interval": "1d",
                "startTime": now_ms - CANDLE_1D_WINDOW_MS,
                "endTime": now_ms,
            },
        },
    }
    for name, payload in simple.items():
        save(out, name, payload, info.post(payload))

    # Error shapes: bare coin without dex prefix -> HTTP 500 body "null"; bad payload -> 422.
    errors: dict[str, dict[str, Any]] = {
        "error_candles_bare_coin": {
            "type": "candleSnapshot",
            "req": {
                "coin": "NVDA",
                "interval": "1h",
                "startTime": now_ms - CANDLE_1H_WINDOW_MS,
                "endTime": now_ms,
            },
        },
        "error_unknown_type": {"type": "doesNotExist"},
    }
    for name, payload in errors.items():
        status, body = info.raw(payload)
        save(out, name, payload, body, status)

    dir_examples: dict[str, Any] = {}
    active, heavy = pick_users(info, out, now_ms, dir_examples)
    save(out, "dir_catalog", {"note": "one real fill per observed dir value"}, dir_examples)
    start = now_ms - FILL_WINDOW_MS
    per_user: dict[str, dict[str, Any]] = {
        "clearinghouse_active_core": {"type": "clearinghouseState", "user": active},
        "fills_active": {
            "type": "userFillsByTime",
            "user": active,
            "startTime": start,
            "endTime": now_ms,
        },
        "fills_heavy_page1": {
            "type": "userFillsByTime",
            "user": heavy,
            "startTime": start,
            "endTime": now_ms,
        },
    }
    for name, payload in per_user.items():
        save(out, name, payload, info.post(payload))

    page1 = json.loads((out / "fills_heavy_page1.json").read_text())["response"]
    page2_req = {
        "type": "userFillsByTime",
        "user": heavy,
        "startTime": page1[-1]["time"],
        "endTime": now_ms,
    }
    save(out, "fills_heavy_page2", page2_req, info.post(page2_req))


if __name__ == "__main__":
    main()
