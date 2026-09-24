"""Paid wallet sources, enabled only when their API key is present in the environment.

The vendors' response formats have not been verified against live responses, so no
vendor client ships yet. Each source takes a ``fetch`` function (api key -> RawWallets)
supplied when an integration is written. With a key but no integration the source raises
SourceError instead of guessing. Without a key it is disabled, which is reported as a
diagnostic, not a failure. The key is never stored on the source.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from hlsignals.core.errors import AdapterError, SourceError, TransportError
from hlsignals.wallets.sources.base import Loaded, RawWallet, WalletSource

VendorFetch = Callable[[str], Sequence[RawWallet]]


class ApiKeyedSource(WalletSource):
    env_var: str = ""

    def __init__(self, env: Mapping[str, str], fetch: VendorFetch | None, name: str) -> None:
        super().__init__(name)
        self._env = env
        self._fetch = fetch

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r}, env_var={self.env_var!r})"

    def _load_raw(self) -> Loaded:
        key = self._env.get(self.env_var)
        if not key:
            return Loaded((), (f"disabled: {self.env_var} is not set",))
        if self._fetch is None:
            raise SourceError(
                f"{self.name}: vendor integration not implemented (response format unverified)"
            )
        try:
            return Loaded(self._fetch(key))
        except TransportError as exc:
            raise SourceError(f"{self.name}: request failed: {exc}") from exc

    def _adapt(self, item: Any) -> RawWallet:
        if not isinstance(item, RawWallet):
            raise AdapterError(f"vendor fetch returned {type(item).__name__}, not RawWallet")
        return item


class NansenSource(ApiKeyedSource):
    env_var = "NANSEN_API_KEY"

    def __init__(
        self, env: Mapping[str, str], fetch: VendorFetch | None, name: str = "nansen"
    ) -> None:
        super().__init__(env, fetch, name)


class ApifySource(ApiKeyedSource):
    env_var = "APIFY_API_TOKEN"

    def __init__(
        self, env: Mapping[str, str], fetch: VendorFetch | None, name: str = "apify"
    ) -> None:
        super().__init__(env, fetch, name)
