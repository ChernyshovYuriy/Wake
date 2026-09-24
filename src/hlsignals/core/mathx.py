"""Small numeric helpers shared by scoring, signals and backtests. All reject NaN/inf."""

from __future__ import annotations

import math


def _require_finite(*values: float) -> None:
    for v in values:
        if not math.isfinite(v):
            raise ValueError(f"expected a finite number, got {v}")


def clamp(x: float, lo: float, hi: float) -> float:
    _require_finite(x, lo, hi)
    if lo > hi:
        raise ValueError(f"lo ({lo}) must not exceed hi ({hi})")
    return min(max(x, lo), hi)


def safe_div(a: float, b: float, default: float) -> float:
    """a / b, or ``default`` when b is zero or the quotient overflows."""
    _require_finite(a, b, default)
    if b == 0:
        return default
    q = a / b
    return q if math.isfinite(q) else default


def wilson_lower_bound(wins: int, n: int, z: float) -> float:
    """Lower bound of the Wilson score interval for a binomial proportion."""
    _require_finite(z)
    if n < 0 or wins < 0 or wins > n:
        raise ValueError(f"need 0 <= wins <= n, got wins={wins}, n={n}")
    if z <= 0:
        raise ValueError(f"z must be positive, got {z}")
    if n == 0:
        return 0.0
    p = wins / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return max(0.0, (centre - margin) / (1 + z2 / n))


def shrinkage(n: int, k: float) -> float:
    """Sample-size confidence n / (n + k): 0 with no samples, approaches 1 as n grows."""
    _require_finite(k)
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return n / (n + k)


def half_life_decay(age_days: float, half_life_days: float) -> float:
    """Weight 0.5 ** (age / half_life): 1 at age 0, 0.5 at one half-life."""
    _require_finite(age_days, half_life_days)
    if age_days < 0:
        raise ValueError(f"age must be non-negative, got {age_days}")
    if half_life_days <= 0:
        raise ValueError(f"half-life must be positive, got {half_life_days}")
    return float(0.5 ** (age_days / half_life_days))
