"""Statistics (stdlib only): Wilson intervals, exact two-sided McNemar, Cochran's Q, Holm, bootstrap."""
from __future__ import annotations

import math
import random
from typing import Sequence


def wilson(k: int, n: int, z: float = 1.959963985) -> tuple[float, float, float]:
    if n <= 0:
        return float("nan"), float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def _binom_cdf(k: int, n: int) -> float:
    return sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * _binom_cdf(min(b, c), n))


def _chi2_sf(x: float, df: int) -> float:
    """Survival function of chi-square for integer df (closed forms; df=2 -> exp(-x/2))."""
    if x <= 0:
        return 1.0
    if df % 2 == 0:
        s, term = 0.0, 1.0
        for i in range(df // 2):
            if i > 0:
                term *= (x / 2) / i
            s += term
        return math.exp(-x / 2) * s
    # odd df: series via erfc
    s = math.erfc(math.sqrt(x / 2))
    term = math.sqrt(2 * x / math.pi) * math.exp(-x / 2)
    for i in range(1, (df - 1) // 2 + 1):
        if i > 1:
            term *= x / (2 * i - 1)
        s += term
    return min(1.0, max(0.0, s))


def cochran_q(matrix: Sequence[Sequence[int]]) -> tuple[float, float, int]:
    rows = [list(map(int, r)) for r in matrix]
    if not rows:
        return float("nan"), float("nan"), 0
    k = len(rows[0])
    col = [sum(r[j] for r in rows) for j in range(k)]
    row = [sum(r) for r in rows]
    t = sum(col)
    den = k * t - sum(x * x for x in row)
    if den == 0:
        return 0.0, 1.0, k - 1
    q = (k - 1) * (k * sum(c * c for c in col) - t * t) / den
    return q, _chi2_sf(q, k - 1), k - 1


def holm(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, run, out = len(items), 0.0, {}
    for i, (key, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[key] = run
    return out


def bootstrap_mean(xs: Sequence[float], reps: int = 2000, seed: int = 20261106) -> tuple[float, float, float]:
    xs = [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return float("nan"), float("nan"), float("nan")
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(rng.choice(xs) for _ in range(n)) / n for _ in range(reps))
    return sum(xs) / n, means[int(0.025 * reps)], means[min(reps - 1, int(0.975 * reps))]
