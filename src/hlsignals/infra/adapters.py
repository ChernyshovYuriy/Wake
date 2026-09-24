"""Raw Hyperliquid JSON -> domain models (Adapter). Field names: docs/api-notes.md §3, §6.

Every shape problem (missing required key, wrong type, unparsable number, domain
validation failure) raises AdapterError naming the field. Unknown extra keys are ignored;
optional keys (``cloid``, ``builderFee``, ``liquidation``, null ``midPx``) are allowed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

from hlsignals.core.errors import AdapterError
from hlsignals.domain.address import normalize_address
from hlsignals.domain.models import (
    BookLevel,
    Candle,
    Dex,
    Fill,
    L2Book,
    MarketCtx,
    Position,
    Side,
    TapeTrade,
)
from hlsignals.domain.symbols import Symbol

_META_CTX_PAIR = 2
_BOOK_SIDES = 2
_COUNTERPARTIES = 2


def _field(raw: Any, key: str) -> Any:
    if not isinstance(raw, Mapping):
        raise AdapterError(f"expected an object with {key!r}, got {type(raw).__name__}")
    if key not in raw:
        raise AdapterError(f"missing required field {key!r}")
    return raw[key]


def _list(raw: Any, what: str) -> list[Any]:
    if not isinstance(raw, list):
        raise AdapterError(f"expected a list of {what}, got {type(raw).__name__}")
    return raw


def _decimal(raw: Any, key: str) -> Decimal:
    value = _field(raw, key)
    try:
        return Decimal(str(value)) if isinstance(value, str | int) else _bad(key, value)
    except InvalidOperation as exc:
        raise AdapterError(f"field {key!r} is not a number: {value!r}") from exc


def _float(raw: Any, key: str) -> float:
    return float(_decimal(raw, key))


def _optional_float(raw: Any, key: str) -> float | None:
    return None if raw.get(key) is None else _float(raw, key)


def _int(raw: Any, key: str) -> int:
    value = _field(raw, key)
    if isinstance(value, bool) or not isinstance(value, int):
        return _bad(key, value)
    return value


def _bool(raw: Any, key: str) -> bool:
    value = _field(raw, key)
    return value if isinstance(value, bool) else _bad(key, value)


def _str(raw: Any, key: str) -> str:
    value = _field(raw, key)
    return value if isinstance(value, str) else _bad(key, value)


def _bad(key: str, value: Any) -> NoReturn:
    raise AdapterError(f"field {key!r} has unexpected value {value!r}")


def _symbol(raw: Any, key: str) -> Symbol:
    try:
        return Symbol.parse(_str(raw, key))
    except AdapterError as exc:
        raise AdapterError(f"invalid symbol in {key!r}: {exc}") from exc


def _side(raw: Any) -> Side:
    code = _str(raw, "side")
    if code not in Side._value2member_map_:
        raise AdapterError(f"field 'side' has unexpected value {code!r}")
    return Side(code)


def _build[T](what: str, factory: Callable[[], T]) -> T:
    """Run a domain constructor, turning its validation errors into AdapterError."""
    try:
        return factory()
    except ValueError as exc:
        raise AdapterError(f"{what}: {exc}") from exc


def adapt_perp_dexs(raw: Any) -> list[Dex]:
    """``perpDexs``; entry 0 is ``null`` (the core dex) and is skipped."""
    entries = _list(raw, "dexes")
    return [
        Dex(name=_str(entry, "name"), full_name=_str(entry, "fullName"))
        for entry in entries
        if entry is not None
    ]


def adapt_markets(raw: Any, dex: str) -> list[MarketCtx]:
    """``metaAndAssetCtxs`` for one dex: ``[meta, ctxs]`` aligned by index."""
    pair = _list(raw, "[meta, ctxs]")
    if len(pair) != _META_CTX_PAIR:
        raise AdapterError(f"expected [meta, ctxs], got {len(pair)} elements")
    universe = _list(_field(pair[0], "universe"), "universe entries")
    ctxs = _list(pair[1], "asset contexts")
    if len(universe) != len(ctxs):
        raise AdapterError(f"meta/ctx length mismatch: {len(universe)} vs {len(ctxs)}")
    return [_market(meta, ctx, dex) for meta, ctx in zip(universe, ctxs, strict=True)]


def _market(meta: Any, ctx: Any, dex: str) -> MarketCtx:
    symbol = _symbol(meta, "name")
    if symbol.dex != dex:
        raise AdapterError(f"market {symbol} does not belong to dex {dex!r}")
    return _build(
        str(symbol),
        lambda: MarketCtx(
            symbol=symbol,
            mark_px=_float(ctx, "markPx"),
            oracle_px=_float(ctx, "oraclePx"),
            prev_day_px=_float(ctx, "prevDayPx"),
            mid_px=_optional_float(ctx, "midPx"),
            open_interest=_float(ctx, "openInterest"),
            day_ntl_vlm=_float(ctx, "dayNtlVlm"),
            funding=_float(ctx, "funding"),
            is_delisted=bool(meta.get("isDelisted", False)),
        ),
    )


def adapt_positions(raw: Any, wallet: str) -> list[Position]:
    """``clearinghouseState``: one Position per ``assetPositions`` entry."""
    owner = normalize_address(wallet)
    time_ms = _int(raw, "time")
    entries = _list(_field(raw, "assetPositions"), "asset positions")
    return [_position(_field(entry, "position"), owner, time_ms) for entry in entries]


def _position(raw: Any, wallet: str, time_ms: int) -> Position:
    symbol = _symbol(raw, "coin")
    return _build(
        str(symbol),
        lambda: Position(
            wallet=wallet,
            symbol=symbol,
            size=_decimal(raw, "szi"),
            entry_px=_decimal(raw, "entryPx"),
            position_value=_decimal(raw, "positionValue"),
            unrealized_pnl=_decimal(raw, "unrealizedPnl"),
            time_ms=time_ms,
        ),
    )


def adapt_fills(raw: Any, wallet: str) -> list[Fill]:
    """``userFillsByTime`` page. Non-perp fills are kept; callers drop them by symbol."""
    owner = normalize_address(wallet)
    return [_fill(entry, owner) for entry in _list(raw, "fills")]


def _fill(raw: Any, wallet: str) -> Fill:
    liquidation = raw.get("liquidation")
    liquidated = (
        normalize_address(_str(liquidation, "liquidatedUser")) if liquidation is not None else None
    )
    return _build(
        f"fill tid={raw.get('tid')}",
        lambda: Fill(
            wallet=wallet,
            symbol=_symbol(raw, "coin"),
            px=_decimal(raw, "px"),
            sz=_decimal(raw, "sz"),
            side=_side(raw),
            dir=_str(raw, "dir"),
            time_ms=_int(raw, "time"),
            tid=_int(raw, "tid"),
            hash=_str(raw, "hash"),
            crossed=_bool(raw, "crossed"),
            closed_pnl=_decimal(raw, "closedPnl"),
            fee=_decimal(raw, "fee"),
            start_position=_decimal(raw, "startPosition"),
            liquidated_user=liquidated,
        ),
    )


def adapt_candles(raw: Any) -> list[Candle]:
    """``candleSnapshot``: OHLCV strings, ``t``/``T`` open/close ms, ``n`` trade count.
    Returned sorted by close time (``price_at`` relies on it)."""
    return sorted((_candle(entry) for entry in _list(raw, "candles")), key=lambda c: c.close_ms)


def _candle(raw: Any) -> Candle:
    return _build(
        f"candle t={raw.get('t') if isinstance(raw, Mapping) else raw}",
        lambda: Candle(
            symbol=_symbol(raw, "s"),
            interval=_str(raw, "i"),
            open_ms=_int(raw, "t"),
            close_ms=_int(raw, "T"),
            open=_float(raw, "o"),
            high=_float(raw, "h"),
            low=_float(raw, "l"),
            close=_float(raw, "c"),
            volume=_float(raw, "v"),
            n_trades=_int(raw, "n"),
        ),
    )


def adapt_l2_book(raw: Any) -> L2Book:
    """``l2Book``: ``levels = [bids, asks]``, each level ``{px, sz, n}``."""
    levels = _list(_field(raw, "levels"), "book sides")
    if len(levels) != _BOOK_SIDES:
        raise AdapterError(f"expected 2 book sides in 'levels', got {len(levels)}")
    bids, asks = (tuple(_level(lv) for lv in _list(side, "levels")) for side in levels)
    return _build(
        "l2Book",
        lambda: L2Book(_symbol(raw, "coin"), _int(raw, "time"), bids, asks),
    )


def _level(raw: Any) -> BookLevel:
    return _build(
        "book level",
        lambda: BookLevel(_decimal(raw, "px"), _decimal(raw, "sz"), _int(raw, "n")),
    )


def adapt_tape_trades(raw: Any) -> list[TapeTrade]:
    """``recentTrades`` / WS ``trades``: ``users = [buyer, seller]`` (docs/api-notes.md §9)."""
    return [_tape_trade(entry) for entry in _list(raw, "trades")]


def _tape_trade(raw: Any) -> TapeTrade:
    users = _field(raw, "users")
    if not isinstance(users, list) or len(users) != _COUNTERPARTIES:
        raise AdapterError(f"field 'users' must be [buyer, seller], got {users!r}")
    buyer, seller = (normalize_address(str(u)) for u in users)
    return _build(
        f"trade tid={raw.get('tid')}",
        lambda: TapeTrade(
            symbol=_symbol(raw, "coin"),
            side=_side(raw),
            px=_decimal(raw, "px"),
            sz=_decimal(raw, "sz"),
            time_ms=_int(raw, "time"),
            tid=_int(raw, "tid"),
            buyer=buyer,
            seller=seller,
        ),
    )
