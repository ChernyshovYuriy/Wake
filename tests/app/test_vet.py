from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hlsignals.app.cli import live_vetter, main
from hlsignals.app.config import ScoringSettings, Settings, VetSettings, WalletFilterSettings
from hlsignals.app.vet import VetResult, WalletVetter, render_vet
from hlsignals.app.wiring import build_wallet_filter_chain, build_wallet_scorer
from hlsignals.core.clock import MS_PER_DAY, Clock, FakeClock, from_ms
from hlsignals.core.errors import RetryableError
from hlsignals.domain.models import Candle, Fill, WalletRecord
from hlsignals.domain.symbols import Symbol
from tests.factories import (
    AAPL,
    BTC,
    NVDA,
    OTHER_WALLET,
    T0_MS,
    WALLET,
    make_candle_series,
    make_trips,
    make_wallet_record,
)

NOW = from_ms(T0_MS) + timedelta(days=40)
EQUITIES = frozenset({NVDA, AAPL})


@dataclass
class History:
    fills: list[Fill]
    truncated: bool = False

    def __iter__(self) -> Iterator[Fill]:
        return iter(self.fills)


@dataclass
class FakeGateway:
    fills: dict[str, list[Fill] | Exception]
    fill_requests: list[tuple[str, datetime, datetime | None]] = field(default_factory=list)
    candle_requests: list[Symbol] = field(default_factory=list)

    def user_fills_by_time(self, user: str, start: datetime, end: datetime | None) -> History:
        self.fill_requests.append((user, start, end))
        result = self.fills[user]
        if isinstance(result, Exception):
            raise result
        return History(result, truncated=len(result) > 100)

    def candle_snapshot(
        self, symbol: Symbol, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        self.candle_requests.append(symbol)
        return make_candle_series([100.0] * 3, symbol=symbol)


def swing_fills(wallet: str, n: int = 12) -> list[Fill]:
    trips = make_trips([0.02, -0.01, 0.03] * (n // 3), hold_ms=2 * MS_PER_DAY)
    return [replace(f, wallet=wallet) for f in trips]


def vetter(gateway: FakeGateway) -> WalletVetter:
    return WalletVetter(
        gateway=gateway,
        clock=FakeClock(NOW),
        equities=EQUITIES,
        chain=build_wallet_filter_chain(WalletFilterSettings()),
        scorer=build_wallet_scorer(ScoringSettings()),
        settings=VetSettings(lookback_days=90.0),
    )


def test_vet_scores_accepted_wallet_and_fetches_candles_for_traded_equities() -> None:
    fills = swing_fills(WALLET) + [f for f in make_trips([0.5], symbol=BTC)]
    gateway = FakeGateway({WALLET: sorted(fills, key=lambda f: f.time_ms)})
    (result,) = vetter(gateway).vet_all([make_wallet_record()])
    assert result.rejection is None
    assert result.error is None
    assert result.scored is not None
    assert result.scored.trust > 0
    assert result.n_trips == 12
    assert gateway.candle_requests == [NVDA]  # crypto ignored entirely
    user, start, end = gateway.fill_requests[0]
    assert (user, end) == (WALLET, NOW)
    assert NOW - start == timedelta(days=90)


def test_rejected_wallet_still_scored_with_reason() -> None:
    gateway = FakeGateway({WALLET: swing_fills(WALLET, n=3)})
    (result,) = vetter(gateway).vet_all([make_wallet_record()])
    assert result.rejection == ("min_sample", "3 scored round trips < 10")
    assert result.scored is not None


def test_api_error_on_one_wallet_does_not_stop_the_rest() -> None:
    gateway = FakeGateway(
        {WALLET: RetryableError("down", status=503), OTHER_WALLET: swing_fills(OTHER_WALLET)}
    )
    results = vetter(gateway).vet_all(
        [make_wallet_record(), make_wallet_record(address=OTHER_WALLET)]
    )
    assert [r.record.address for r in results] == [OTHER_WALLET, WALLET]  # errors last
    assert results[1].error is not None
    assert "down" in results[1].error


def test_ordering_accepted_by_trust_then_rejected() -> None:
    strong, weak = "0x" + "11" * 20, "0x" + "22" * 20
    gateway = FakeGateway(
        {
            strong: swing_fills(strong, n=30),
            weak: swing_fills(weak, n=12),
            WALLET: swing_fills(WALLET, n=3),
        }
    )
    records = [make_wallet_record(address=a) for a in (WALLET, weak, strong)]
    assert [r.record.address for r in vetter(gateway).vet_all(records)] == [strong, weak, WALLET]


def test_truncated_history_is_flagged() -> None:
    gateway = FakeGateway({WALLET: swing_fills(WALLET, n=102)})
    (result,) = vetter(gateway).vet_all([make_wallet_record()])
    assert result.truncated


def test_render_explains_everything() -> None:
    gateway = FakeGateway(
        {WALLET: swing_fills(WALLET), OTHER_WALLET: swing_fills(OTHER_WALLET, n=3)}
    )
    text = render_vet(
        vetter(gateway).vet_all([make_wallet_record(), make_wallet_record(address=OTHER_WALLET)])
    )
    assert WALLET in text
    assert OTHER_WALLET in text
    assert "ACCEPTED" in text
    assert "REJECTED min_sample: 3 scored round trips < 10" in text
    for name in (
        "hit_rate",
        "drawdown",
        "consistency",
        "horizon_fit",
        "trust",
        "confidence",
        "decay",
    ):
        assert name in text
    assert "wins=" in text  # evidence is shown


def test_render_error_and_empty() -> None:
    result = VetResult(make_wallet_record(), None, None, "RetryableError: down", 0, 0, False)
    assert "ERROR RetryableError: down" in render_vet([result])
    assert render_vet([]) == "no wallets to vet\n"


# --- CLI --------------------------------------------------------------------------------


class RecordingVetter:
    def __init__(self) -> None:
        self.records: list[WalletRecord] = []

    def vet_all(self, records: list[WalletRecord]) -> list[VetResult]:
        self.records = list(records)
        return [
            VetResult(r, None, ("min_sample", "0 scored round trips < 10"), None, 0, 0, False)
            for r in records
        ]


def run_cli(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str, RecordingVetter]:
    fake = RecordingVetter()
    code = main(argv, make_vetter=lambda settings, clock: fake, clock=FakeClock(NOW))
    out, err = capsys.readouterr()
    return code, out, err, fake


def test_cli_vet_wallet_args(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _, fake = run_cli(["vet", "--wallet", "0x" + "AB" * 20, "--wallet", WALLET], capsys)
    assert code == 0
    assert [r.address for r in fake.records] == ["0x" + "ab" * 20, WALLET]
    assert "REJECTED min_sample" in out


def test_cli_vet_wallets_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "w.toml"
    path.write_text(
        f'[[wallets]]\naddress = "{WALLET}"\nscore = 70.0\n\n[[wallets]]\naddress = "bad"\n'
    )
    code, _, err, fake = run_cli(["vet", "--wallets-file", str(path)], capsys)
    assert code == 0
    assert [(r.address, r.raw_score) for r in fake.records] == [(WALLET, 70.0)]
    assert "skipped entry" in err


def test_cli_invalid_wallet_is_config_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err, _ = run_cli(["vet", "--wallet", "0x123"], capsys)
    assert code == 2
    assert "0x123" in err


def test_cli_missing_wallets_file_is_source_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err, _ = run_cli(["vet", "--wallets-file", str(tmp_path / "none.toml")], capsys)
    assert code == 3
    assert "not found" in err


def test_cli_requires_some_wallet(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        run_cli(["vet"], capsys)


def test_cli_lookback_override_reaches_settings(capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[float] = []

    def make(settings: Settings, clock: Clock) -> RecordingVetter:
        seen.append(settings.vet.lookback_days)
        return RecordingVetter()

    main(
        ["vet", "--wallet", WALLET, "--lookback-days", "30"], make_vetter=make, clock=FakeClock(NOW)
    )
    assert seen == [30.0]


def test_live_vetter_builds_from_default_settings() -> None:
    assert isinstance(live_vetter(Settings(), FakeClock(NOW)), WalletVetter)


def test_cli_api_error_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    class Failing:
        def vet_all(self, records: list[WalletRecord]) -> list[VetResult]:
            raise RetryableError("rate limited", status=429)

    code = main(
        ["vet", "--wallet", WALLET], make_vetter=lambda s, c: Failing(), clock=FakeClock(NOW)
    )
    assert code == 4
    assert "rate limited" in capsys.readouterr().err
