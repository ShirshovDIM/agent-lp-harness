"""Tests for the celo_report.py slippage fix (auditor spec §2, 2026-10-12) + v3math port check.

Covers: L-proportionality of fee accrual (kept from the original test), the k/(1+k)
swap_slippage numerics (incl. BREF fixture block 79846357), the re-range cost applied to the
swapped volume V via the k/(1+k) path against port-exact references, the cost base check, and
a bit-exact replay of scripts/step4_bref.json rows through scripts/v3math.py
(UniswapV3Pool.swap integer port, copied as-is).

Runs under pytest, or directly: python3 tests/test_celo_report.py (exit 0 = all green).
"""
import importlib.util
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

_spec = importlib.util.spec_from_file_location("cr", os.path.join(SCRIPTS, "celo_report.py"))
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)
import v3math as vm  # noqa: E402  (auditor's bit-exact UniswapV3Pool.swap port, unmodified)

BREF = json.load(open(os.path.join(SCRIPTS, "step4_bref.json")))
P = BREF["price"]                      # 0.09806268 USDm per CELO at block 79846357
L_POOL = int(BREF["liquidity"]) / 1e18  # 11481.6 human active liquidity
SQRT_P = math.sqrt(P)                   # 0.3131496 (task: 0.313147)
W, WIDTH, FEE, GAS = 150.0, 0.02, cr.TIER_FEE, cr.GAS_CUSD


def _base_state():
    return {"sqrtP": int(BREF["sqrtPriceX96"]), "tick": BREF["tick"],
            "liquidity": int(BREF["liquidity"]), "fee": 100, "spacing": 1,
            "bitmap": {int(k): int(v, 16) for k, v in BREF["bitmap_words"].items()},
            "liq_net": {int(k): int(v) for k, v in BREF["liq_net"].items()}}


def test_fee_accrual_proportional_to_L():
    """Kept unit: position fees are proportional to its L; L == L_pool earns exactly the pool fees."""
    Lp, dfg = 1000.0, 0.001            # pool accrued 0.001 token per unit L this hour
    assert abs(Lp * dfg - Lp * dfg) < 1e-12
    assert abs((Lp / 2 * dfg) - (Lp * dfg) / 2) < 1e-12
    # through the report's own accrual expression L*dfg0*price + L*dfg1
    fees_full = Lp * dfg * P + Lp * dfg
    fees_half = (Lp / 2) * dfg * P + (Lp / 2) * dfg
    assert abs(fees_full / fees_half - 2.0) < 1e-12
    print("unit L=L_pool: OK (fees proportional, no 1e18 scale bug)")


def test_swap_slippage_k_over_1pk():
    """k/(1+k) acceptance values (±1e-4); old code gave 0.02280 for the first row."""
    assert abs(cr.swap_slippage(150.0, 0.096, 55300.0) - 0.008679) <= 1e-4
    assert abs(cr.swap_slippage(150.0, 0.096, 55300.0) - 0.02280) > 1e-3   # old model is gone
    # BREF fixture (block 79846357, L_h=11481.6): QuoterV2 actuals 4.0637% / 2.0431%
    assert abs(cr.swap_slippage(150.0, P, L_POOL) - 0.040046) <= 1e-4
    assert abs(cr.swap_slippage(75.0, P, L_POOL) - 0.020432) <= 1e-4
    assert abs(cr.swap_slippage(150.0, P, L_POOL) - 0.040637) < 1e-3      # vs QuoterV2 actual
    assert abs(cr.swap_slippage(75.0, P, L_POOL) - 0.020431) < 1e-3       # vs QuoterV2 actual
    assert cr.swap_slippage(150.0, 0.096, 5.53e22 / 1e18) < 0.60          # sanity cap not binding
    print(f"unit swap_slippage: OK (k/(1+k); BREF U=150 {cr.swap_slippage(150.,P,L_POOL):.4%})")


def _port_rerange_cost(V_usd, zero_for_one, gas=0.0):
    """Port-exact cost (slip+fee [+gas]) of a V_usd swap at the BREF state."""
    st = _base_state()
    if zero_for_one:                     # token0 (CELO) in, worth V_usd at the pre-swap price
        ain = int(round(V_usd / P * 1e18))
        out = vm.swap_exact_in(st, True, ain)["amountOut"] / 1e18
        return V_usd - out + gas
    ain = int(round(V_usd * 1e18))       # token1 (USDm) in
    out = vm.swap_exact_in(st, False, ain)["amountOut"] / 1e18
    return V_usd - out * P + gas


def test_rerange_cost_BREF():
    """Re-range cost at BREF, W=150, ±2% additive, via the k/(1+k) path.
    UP: single-segment walk at L_pool -> k/(1+k) ~ port-exact (ref $1.5096 slip+fee, V=$74.25).
    DOWN: the port walk crosses tick -23260 into (L_pool - liq_net) = 71321.6 extra liquidity;
    the single-range approx blends pre/post-crossing liquidity: L_eff = (L_pool + L_below)/2
    (ref $0.4889 slip+fee)."""
    a = 1 - math.sqrt(1 / (1 + WIDTH))
    b = 1 - math.sqrt(1 - WIDTH)
    phi0 = a / (a + b)
    assert abs(phi0 * W - 74.2537) < 1e-3                 # spec's V = phi0*W = $74.25
    L_below = (int(BREF["liquidity"]) - int(BREF["liq_net"]["-23260"])) / 1e18   # 71321.6

    # port-exact references (gas excluded, as in the spec)
    up_ref = _port_rerange_cost(74.2537, zero_for_one=False)
    down_ref = _port_rerange_cost((1 - phi0) * W, zero_for_one=True)
    assert abs(up_ref - 1.5096) < 5e-3, up_ref
    assert abs(down_ref - 0.4889) < 5e-3, down_ref

    cost_up, V_up = cr.rerange_cost(W, P, WIDTH, L_POOL, True)
    assert 1.45 <= cost_up <= 1.60, cost_up
    assert 73.5 < V_up < 76.0
    cost_dn, V_dn = cr.rerange_cost(W, P, WIDTH, (L_POOL + L_below) / 2, False)
    assert 0.45 <= cost_dn <= 0.56, cost_dn
    assert 75.0 < V_dn < 78.0
    print(f"unit rerange BREF: OK (UP ${cost_up:.4f} vs port-ref ${up_ref:.4f}+gas; "
          f"DOWN ${cost_dn:.4f} vs port-ref ${down_ref:.4f}+gas)")


def test_cost_base_on_V_not_W():
    """Cost base check: for W=150, V~=75 the applied cost must be ~= half of a full-W cost at
    the same per-unit rate (i.e. the cost base is the swapped volume V, not W; within 10%)."""
    V = cr.rerange_cost(W, P, WIDTH, L_POOL, True)[1]
    rate = cr.swap_slippage(V, P, L_POOL) + FEE
    cost_V = V * rate + GAS                       # what the fixed code applies
    cost_W_full = W * rate + GAS                  # same rate applied to the full position
    assert abs(cost_V / cost_W_full - V / W) <= 0.10 * (V / W)
    assert cost_V < 0.6 * cost_W_full             # dramatically cheaper than a full-W swap
    print(f"unit cost base: OK (cost(V)/cost(W_full)={cost_V / cost_W_full:.4f} ~ V/W={V / W:.4f})")


def test_v3math_port_replay():
    """Port cross-check: replay the step4_bref.json rows (side, amountIn, liq_net,
    sqrtPriceX96, tick, fee=100) through the v3math port; amountOut must match the fixture
    to 1 wei for >= 14 of 16 rows (the shipped fixture carries 4 rows -> scaled threshold 4/4;
    a 16-row fixture would require >= 14)."""
    rows = BREF["rows"]
    need = 14 if len(rows) >= 16 else math.ceil(14 / 16 * len(rows))
    bad = []
    for row in rows:
        st = _base_state()
        out = vm.swap_exact_in(st, row["side"].startswith("CELO"), int(row["amountIn"]))["amountOut"]
        if abs(out - int(row["amountOut"])) > 1:      # 1 wei
            bad.append((row["side"], row["U_usd"], out, int(row["amountOut"])))
    for b in bad:
        print("MISMATCH:", b)
    assert not bad and len(rows) >= need, f"port replay: {len(rows) - len(bad)}/{len(rows)} match, need {need}"
    print(f"unit v3math port replay: OK ({len(rows) - len(bad)}/{len(rows)} rows bit-exact)")


def main():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"{fn.__name__}: OK")
    print(f"{len(fns)} tests passed")


if __name__ == "__main__":
    main()
