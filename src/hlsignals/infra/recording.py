"""RecordingTransport (Decorator): records every request/response so a live run can be
replayed exactly by FixtureTransport. Saved fixtures are sanitized: every wallet address
becomes a deterministic pseudonym (still a valid 0x + 40-hex address), consistently in
requests and responses, so replayed requests match the recorded ones.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from hlsignals.core.errors import TransportError
from hlsignals.infra.transport import HTTP_OK, Payload, Transport, canonical_key

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
# Keys whose 0x values are not wallets (tx hashes, order ids, dex operator addresses).
_KEEP_KEYS = frozenset({"hash", "cloid", "deployer", "oracleUpdater", "feeRecipient"})
_NETWORK_ERROR_STATUS = 599  # a failure with no HTTP status (timeout, connection)


def pseudonym(address: str) -> str:
    digest = hashlib.sha256(("hlsignals-fixture:" + address.lower()).encode()).hexdigest()
    return "0x" + digest[:40]


def sanitize(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {k: sanitize(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, key) for v in value]
    if isinstance(value, str) and key not in _KEEP_KEYS:
        return _ADDRESS_RE.sub(lambda m: pseudonym(m.group(0)), value)
    return value


class RecordingTransport(Transport):
    def __init__(self, inner: Transport) -> None:
        self._inner = inner
        self._records: dict[str, dict[str, Any]] = {}

    def post(self, payload: Payload) -> Any:
        key = canonical_key(payload)
        try:
            response = self._inner.post(payload)
        except TransportError as exc:
            status = exc.status or _NETWORK_ERROR_STATUS
            self._records.setdefault(
                key, {"request": dict(payload), "response": str(exc), "status": status}
            )
            raise
        self._records.setdefault(key, {"request": dict(payload), "response": response})
        return response

    def __len__(self) -> int:
        return len(self._records)

    def save(self, directory: Path) -> int:
        """Write one sanitized fixture file per distinct request; returns the count."""
        directory.mkdir(parents=True, exist_ok=True)
        for i, doc in enumerate(self._records.values()):
            clean = sanitize(doc)
            if clean.get("status", HTTP_OK) == HTTP_OK:
                clean.pop("status", None)
            name = f"{i:05d}_{clean['request'].get('type', 'request')}.json"
            (directory / name).write_text(json.dumps(clean, indent=1, sort_keys=True) + "\n")
        return len(self._records)
