"""HistoricalSignalSource: rebuilds, as of time t and only through an AsOfView, what the
live pipeline sees, then runs the signal engine.

What is reconstructed, and how honestly:
- wallets: vetted and scored on their fills within the lookback before t; a prior score
  is used only if it was taken by t;
- positions: net size per symbol from the fills (LotBook), since there is no historical
  clearinghouseState. Positions opened before the lookback and never traded inside it are
  invisible (documented limitation);
- market context: price and 24h volume from perp candles closed by t. Open interest has
  no history: the current snapshot's contract count is used, valued at the price at t
  (an approximation that only scales the flow component and the OI filter).

Inputs are cached per t, so re-running with another engine (a walk-forward grid) costs
only the corroborate/combine step.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from hlsignals.backtest.asof import AsOfView
from hlsignals.core.chain import FilterChain
from hlsignals.core.clock import MS_PER_DAY, MS_PER_HOUR, from_ms, to_ms
from hlsignals.core.errors import AdapterError
from hlsignals.domain.candles import price_at
from hlsignals.domain.models import (
    Candle,
    MarketCtx,
    Position,
    ScoredWallet,
    TickerSignal,
)
from hlsignals.domain.symbols import Symbol
from hlsignals.session.calendar import SessionCalendar
from hlsignals.signals.engine import SignalEngine
from hlsignals.signals.inputs import TickerInputs
from hlsignals.wallets.scoring.slice import EquitySlice

_DAY_MS = MS_PER_DAY


class Scorer(Protocol):
    def score(self, wallet: EquitySlice) -> ScoredWallet: ...


class _ViewCandles(Mapping[Symbol, tuple[Candle, ...]]):
    """Candles in (start, t] for the symbols a wallet traded, read lazily from the view."""

    def __init__(self, view: AsOfView, symbols: frozenset[Symbol], start_ms: int) -> None:
        self._view = view
        self._symbols = symbols
        self._start_ms = start_ms

    def __getitem__(self, symbol: Symbol) -> tuple[Candle, ...]:
        if symbol not in self._symbols:
            raise KeyError(symbol)
        return self._view.candles_between(symbol, self._start_ms, self._view.t_ms)

    def __iter__(self) -> Iterator[Symbol]:
        return iter(sorted(self._symbols))

    def __len__(self) -> int:
        return len(self._symbols)


@dataclass(frozen=True, slots=True)
class _Accepted:
    scored: ScoredWallet
    wallet: EquitySlice


class HistoricalSignalSource:
    def __init__(
        self,
        *,
        equities: frozenset[Symbol],
        wallet_chain: FilterChain[EquitySlice],
        scorer: Scorer,
        engine: SignalEngine,
        market_chain: FilterChain[MarketCtx],
        calendar: SessionCalendar,
        lookback_days: float,
        signal_candle_hours: float,
        cache: dict[int, list[TickerInputs]] | None = None,
        excluded: dict[str, str] | None = None,
    ) -> None:
        self._equities = equities
        self._wallet_chain = wallet_chain
        self._scorer = scorer
        self._engine = engine
        self._market_chain = market_chain
        self._calendar = calendar
        self._lookback_ms = int(lookback_days * _DAY_MS)
        self._signal_ms = int(signal_candle_hours * MS_PER_HOUR)
        self._cache = cache if cache is not None else {}
        # Wallets whose history could not be interpreted (address -> first error), for the
        # report. Exclusion itself is decided per view from the fills visible at t: a record
        # consulted across views would carry a later view's knowledge into earlier ones.
        self.excluded_wallets: dict[str, str] = excluded if excluded is not None else {}

    def with_engine(self, engine: SignalEngine) -> HistoricalSignalSource:
        """The same reconstruction (and cache) with different signal parameters."""
        return HistoricalSignalSource(
            equities=self._equities,
            wallet_chain=self._wallet_chain,
            scorer=self._scorer,
            engine=engine,
            market_chain=self._market_chain,
            calendar=self._calendar,
            lookback_days=self._lookback_ms / _DAY_MS,
            signal_candle_hours=self._signal_ms / MS_PER_HOUR,
            cache=self._cache,
            excluded=self.excluded_wallets,
        )

    def signals_at(self, view: AsOfView) -> Sequence[TickerSignal]:
        return [self._engine.evaluate(inputs) for inputs in self.inputs_at(view)]

    def inputs_at(self, view: AsOfView) -> list[TickerInputs]:
        cached = self._cache.get(view.t_ms)
        if cached is None:
            cached = self._cache[view.t_ms] = self._build(view)
        return cached

    def _build(self, view: AsOfView) -> list[TickerInputs]:
        accepted = self._accepted(view)
        markets = [m for s, snap in view.markets.items() if (m := self._market(view, s, snap))]
        wallets = {a.scored.address: a.scored for a in accepted}
        as_of = from_ms(view.t_ms)
        last_close = to_ms(self._calendar.last_close(as_of))
        session_open = self._calendar.is_open(as_of)
        return [
            TickerInputs(
                symbol=market.symbol,
                market=market,
                wallets=wallets,
                positions=tuple(self._positions(accepted, market, view.t_ms)),
                fills=tuple(
                    f for a in accepted for f in a.wallet.fills if f.symbol == market.symbol
                ),
                candles=view.candles_between(market.symbol, view.t_ms - self._signal_ms, view.t_ms),
                as_of_ms=view.t_ms,
                last_close_ms=last_close,
                session_open=session_open,
            )
            for market in self._market_chain.run(markets).accepted
        ]

    def _accepted(self, view: AsOfView) -> list[_Accepted]:
        start = view.t_ms - self._lookback_ms
        accepted = []
        for record in view.records:
            fills = view.fills_between(record.address, start, view.t_ms)
            traded = frozenset(f.symbol for f in fills if f.symbol in self._equities)
            try:
                wallet = EquitySlice.from_history(
                    record.address,
                    fills,
                    positions=(),
                    equities=self._equities,
                    as_of_ms=view.t_ms,
                    candles=_ViewCandles(view, traded, start),
                    raw_score=view.score(record),
                )
            except AdapterError as exc:
                self.excluded_wallets.setdefault(record.address, str(exc))
                continue
            if self._wallet_chain.evaluate(wallet) is None:
                accepted.append(_Accepted(self._scorer.score(wallet), wallet))
        return accepted

    @staticmethod
    def _market(view: AsOfView, symbol: Symbol, snapshot: MarketCtx) -> MarketCtx | None:
        candles = view.candles(symbol)
        mark = price_at(candles, view.t_ms)
        if mark is None:
            return None
        day_ago = view.t_ms - _DAY_MS
        volume = sum(c.close * c.volume for c in candles if c.close_ms > day_ago)
        return MarketCtx(
            symbol=symbol,
            mark_px=mark,
            oracle_px=mark,
            prev_day_px=price_at(candles, day_ago) or mark,
            mid_px=None,
            open_interest=snapshot.open_interest,  # no OI history: current contract count
            day_ntl_vlm=volume,
            funding=0.0,
            is_delisted=False,
        )

    @staticmethod
    def _positions(accepted: Sequence[_Accepted], market: MarketCtx, t_ms: int) -> list[Position]:
        mark = Decimal(str(market.mark_px))
        return [
            Position(
                wallet=a.scored.address,
                symbol=market.symbol,
                size=size,
                entry_px=mark,  # unknown historically; only the size is used by signals
                position_value=abs(size) * mark,
                unrealized_pnl=Decimal(0),
                time_ms=t_ms,
            )
            for a in accepted
            if (size := a.wallet.net_sizes.get(market.symbol, Decimal(0))) != 0
        ]
