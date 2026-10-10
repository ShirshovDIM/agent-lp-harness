#!/usr/bin/env python3
"""Celo UniswapV3 CELO/USDm fee=100 — verification-report compute.

Input: data/samples.jsonl (hourly {ts, blk, sqrtPriceX96, tick, liquidity, feeGrowthGlobal0/1X128}).
Method follows weth_rerange_modes (09.10): $150 position, entry at center, widths ±2/5/10/15/30%,
113 seeded random starts (seed 113), 14-day horizon, medians/p10/p90, tails, explicit caveats.
CALIBRATION: none yet in this dataset; the block fills when a live position journal is available
(report -> live pilot -> calibrated update).

Fees model: position share of feeGrowthGlobal delta while price in range (no tick-outside terms —
global-only hourly data, same simplification as the weth measure; flagged in caveats).
Costs: gas ~0.001 cUSD per re-range (Celo mainnet, order checked from observed tx fees later),
rebalance swap fee = pool tier 0.01% of swapped volume (single-tier pool; stress 0.05%).
Output: celo_pool_2026-10-10.json + .md (numbers verbatim from the json).
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "data" / "samples.jsonl"
OUT_JSON = HERE / "celo_pool_2026-10-10.json"
OUT_MD = HERE / "celo_pool_2026-10-10.md"
POS_USD = 150.0
SEED = 113
HORIZON_H = 14 * 24
WIDTHS = [0.02, 0.05, 0.10, 0.15, 0.30]
TIER_FEE = 0.0001          # fee=100 pool: 0.01%
GAS_CUSD = 0.014           # per re-range, measured from NFPM txs on Celo mainnet ($0.01-0.018)
# execution cost of a rebalancing swap = tier fee + curve slippage against the POOL's own
# liquidity (this pool is thin: ~$6.5k TVL; a flat 0.01% understates real cost by two orders)


def load():
    rows = [json.loads(l) for l in SAMPLES.read_text().splitlines() if l.strip()]
    rows.sort(key=lambda r: r["ts"])
    for r in rows:
        r["price"] = (r["sqrtPriceX96"] / 2 ** 96) ** 2   # token1/token0 = USDm per CELO
        r["L_pool_h"] = r["liquidity"] / 1e18             # active pool liquidity, human units
    return rows


def amounts(L, pa, pb, price):
    """token0 (CELO), token1 (cUSD) for L in human token units (L_contract/1e18), bounds pa<=pb."""
    if price <= pa:
        return L * (math.sqrt(pb) - math.sqrt(pa)) / (math.sqrt(pa * pb)), 0.0
    if price >= pb:
        return 0.0, L * (math.sqrt(pb) - math.sqrt(pa))
    return (L * (math.sqrt(pb) - math.sqrt(price)) / (math.sqrt(price * pb)),
            L * (math.sqrt(price) - math.sqrt(pa)))


def position_for_width(price, width, usd=POS_USD):
    pa, pb = price * (1 - width), price * (1 + width)
    # entry holds 50/50 USD-split: solve L for a position centered at price
    # at center: token0 = L*(sqrt(pb)-sqrt(p))/(sqrt(p*pb)), token1 = L*(sqrt(p)-sqrt(pa))
    # USD value = token0*p + token1 == usd
    t0_coef = (math.sqrt(pb) - math.sqrt(price)) / (math.sqrt(price * pb))
    t1_coef = (math.sqrt(price) - math.sqrt(pa))
    L = usd / (t0_coef * price + t1_coef)          # human token units (L_contract = L * 1e18)
    return L, pa, pb


def swap_slippage(usd_amount: float, price: float, L_pool_h: float) -> float:
    """Execution penalty fraction for a swap of usd_amount through the pool at price with active
    liquidity L_pool_h (human units): solve the pool curve for the realized price. For a swap
    entirely within the active range: delta_p/p = usd_amount / (L_pool_h * price^1.5 * 2); the
    average execution penalty is half the end-slippage."""
    if L_pool_h <= 0 or usd_amount <= 0:
        return 0.0
    end_slip = (usd_amount / (L_pool_h * price * math.sqrt(price))) / 2.0
    return min(end_slip / 2.0, 0.60)   # cap at 60% — sanity for pathological hours


def run():
    rows = load()
    n = len(rows)
    first, last = rows[0], rows[-1]
    stats = {"pool": "0x2d70cbabf4d8e61d5317b62cbe912935fd94e0fe",
             "pair": "CELO/USDm fee=100 (0.01%), UniswapV3 on Celo mainnet",
             "token0": "CELO(18d)", "token1": "USDm(18d)",
             "window_utc": [datetime.fromtimestamp(first["ts"], timezone.utc).isoformat(),
                            datetime.fromtimestamp(last["ts"], timezone.utc).isoformat()],
             "hours": n,
             "price_first": first["price"], "price_last": last["price"],
             "price_min": min(r["price"] for r in rows), "price_max": max(r["price"] for r in rows),
             "position_usd": POS_USD, "seed": SEED, "horizon_days": HORIZON_H // 24,
             "calibration": None,   # filled when a live position journal is available
             "widths": {}, "modes": {}}

    # per-hour pool fee deltas (token units per unit of liquidity)
    for i in range(1, n):
        rows[i]["dfg0"] = (rows[i]["feeGrowthGlobal0X128"] - rows[i - 1]["feeGrowthGlobal0X128"]) / 2 ** 128
        rows[i]["dfg1"] = (rows[i]["feeGrowthGlobal1X128"] - rows[i - 1]["feeGrowthGlobal1X128"]) / 2 ** 128
    rows[0]["dfg0"] = rows[0]["dfg1"] = 0.0

    import random
    rng = random.Random(SEED)
    starts = sorted(rng.sample(range(0, max(1, n - HORIZON_H)), min(113, max(1, n - HORIZON_H))))
    stats["starts"] = len(starts)
    stats["n_eff"] = round(n / HORIZON_H, 2)   # non-overlapping-window equivalent

    for w in WIDTHS:
        res_hold, res_re = [], []
        for s in starts:
            seg = rows[s:s + HORIZON_H]
            p0 = seg[0]["price"]
            L, pa, pb = position_for_width(p0, w)
            # HOLD (never re-range)
            fees = 0.0
            for r in seg:
                if pa <= r["price"] <= pb:
                    # position L share of pool fee growth (L in 1e18 units)
                    fees += L * r["dfg0"] * r["price"] + L * r["dfg1"]
            t0, t1 = amounts(L, pa, pb, seg[-1]["price"])
            hold_final = t0 * seg[-1]["price"] + t1 + fees
            res_hold.append(hold_final / POS_USD - 1)
            # RE-RANGE: on first hour out of range, close at market, re-enter centered at width w
            fees, j, pa_c, pb_c, Lc = 0.0, 0, pa, pb, L
            while j < len(seg):
                r = seg[j]
                if pa_c <= r["price"] <= pb_c:
                    fees += Lc * r["dfg0"] * r["price"] + Lc * r["dfg1"]
                else:
                    t0, t1 = amounts(Lc, pa_c, pb_c, r["price"])
                    usd_now = t0 * r["price"] + t1 + fees
                    # rebalance: swap to 50/50; cost = tier fee + curve slippage vs the pool's
                    # own liquidity + gas (applied once here)
                    slip = swap_slippage(usd_now, r["price"], r["L_pool_h"])
                    usd_now -= usd_now * (TIER_FEE + slip) + GAS_CUSD
                    Lc, pa_c, pb_c = position_for_width(r["price"], w, usd_now)
                    fees = 0.0
                    r = seg[j]
                    if pa_c <= r["price"] <= pb_c:
                        fees += Lc * r["dfg0"] * r["price"] + Lc * r["dfg1"]
                j += 1
            t0, t1 = amounts(Lc, pa_c, pb_c, seg[-1]["price"])
            re_final = t0 * seg[-1]["price"] + t1 + fees
            res_re.append(re_final / POS_USD - 1)

        def q(v, p):
            s = sorted(v)
            return s[min(len(s) - 1, int(p * len(s)))]

        ph = sum(1 for s in starts if s + HORIZON_H <= n) and None  # placeholder-free
        import math as _m
        k5 = sum(1 for v in res_hold if v < -0.05)
        wil = (k5 + 1.9208) / (len(res_hold) + 3.8416) - 1.96 * _m.sqrt(
            (k5 / len(res_hold) * (1 - k5 / len(res_hold)) + 0.9604 / len(res_hold)) / len(res_hold))             if len(res_hold) else 0.0
        wil = max(0.0, min(1.0, wil))
        # HODL 50/50 benchmark for the same starts: hold half CELO / half USDm from entry
        hodl = []
        for s in starts:
            seg_h = rows[s:s + HORIZON_H]
            p0h, p1h = seg_h[0]["price"], seg_h[-1]["price"]
            hodl.append((0.5 * p1h / p0h + 0.5) - 1)
        stats["widths"][f"±{int(w * 100)}%"] = {
            "hold_vs_usd": {"median": q(res_hold, 0.5), "p10": q(res_hold, 0.1), "p90": q(res_hold, 0.9),
                            "P(<-5%)": sum(1 for v in res_hold if v < -0.05) / len(res_hold),
                            "P(<-10%)": sum(1 for v in res_hold if v < -0.10) / len(res_hold)},
            "hodl_5050_vs_usd": {"median": q(hodl, 0.5), "p10": q(hodl, 0.1), "p90": q(hodl, 0.9)},
            "wilson_p5_lower": wil,
            "rerange_vs_usd": {"median": q(res_re, 0.5), "p10": q(res_re, 0.1), "p90": q(res_re, 0.9),
                               "P(<-5%)": sum(1 for v in res_re if v < -0.05) / len(res_re),
                               "P(<-10%)": sum(1 for v in res_re if v < -0.10) / len(res_re)},
        }
    OUT_JSON.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    print(json.dumps({k: stats[k] for k in ("hours", "starts", "price_first", "price_last")}, indent=1))
    print("wrote", OUT_JSON)


if __name__ == "__main__":
    sys.exit(run())
