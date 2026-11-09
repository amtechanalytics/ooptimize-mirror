"""Statistics against known values (certificate E11)."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
import math

from ooptpaper import stats


def test_wilson_known_value():
    p, lo, hi = stats.wilson(5, 10)
    assert p == 0.5 and abs(lo - 0.2366) < 1e-4 and abs(hi - 0.7634) < 1e-4
    assert math.isnan(stats.wilson(0, 0)[0])
    assert stats.wilson(0, 20)[1] == 0.0


def test_mcnemar_exact():
    assert abs(stats.mcnemar_exact(0, 6) - 0.03125) < 1e-12
    assert stats.mcnemar_exact(3, 3) == 1.0
    assert stats.mcnemar_exact(0, 0) == 1.0


def test_chi2_sf_closed_forms():
    for x in (0.5, 2.0, 7.3):
        assert abs(stats._chi2_sf(x, 2) - math.exp(-x / 2)) < 1e-12
        assert abs(stats._chi2_sf(x, 1) - math.erfc(math.sqrt(x / 2))) < 1e-12
    assert abs(stats._chi2_sf(7.814727903, 3) - 0.05) < 1e-6        # chi2(3) 95th percentile
    assert abs(stats._chi2_sf(9.487729037, 4) - 0.05) < 1e-6        # chi2(4) 95th percentile


def test_cochran_q_textbook():
    # 12 subjects x 3 conditions; Q computed by hand: col sums 9, 4, 2; row sums give Q = 6.5333
    m = [[1, 1, 0]] * 2 + [[1, 0, 0]] * 5 + [[1, 1, 1]] * 1 + [[0, 0, 0]] * 2 + [[1, 1, 1]] + [[0, 1, 0]]
    q, p, df = stats.cochran_q(m)
    col = [sum(r[j] for r in m) for j in range(3)]
    row = [sum(r) for r in m]
    t = sum(col)
    expect = 2 * (3 * sum(c * c for c in col) - t * t) / (3 * t - sum(x * x for x in row))
    assert abs(q - expect) < 1e-12 and df == 2 and abs(p - math.exp(-q / 2)) < 1e-12
    assert stats.cochran_q([[1, 1, 1], [0, 0, 0]])[1] == 1.0


def test_holm_is_monotone_and_capped():
    adj = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert abs(adj["a"] - 0.03) < 1e-12 and abs(adj["c"] - 0.06) < 1e-12 and abs(adj["b"] - 0.06) < 1e-12
    assert stats.holm({"x": 0.9, "y": 0.8})["x"] == 1.0


def test_bootstrap_deterministic():
    a = stats.bootstrap_mean([1, 2, 3, 4, 5])
    assert a == stats.bootstrap_mean([1, 2, 3, 4, 5]) and a[1] <= 3 <= a[2]
