#!/usr/bin/env python3
"""Перестройка WETH-USDC CL50 (Aerodrome Slipstream F3) после выхода из диапазона: обмен против «без обмена».
Deterministic replay of re-range policy modes; no LLM.

Режимы (база ±10 %, размер 150 USD, вход по центру):
  A   — по выходу закрыть, обменять до пропорции и встать по центру ±10 % (как живой пилот).
  B1  — по выходу без обмена: весь WETH в окно [P, P*1.222] над ценой (та же лог-ширина, что ±10 %), весь USDC — в
        [P/1.222, P] под ценой; B1_narrow — окно [P, P*1.10] (вдвое уже: концентрация выше);
        ждём возврата цены (наград нет). Перестановка — только когда окно пройдено насквозь.
  B2a — по выходу с минимальным обменом: окно той же ширины, что ±10 % (hi/lo = 1.1/0.9), цена у края на a %
        внутрь (a = 1/2/3 %); обменивается только доля, нужная для этой пропорции.
  C   — не перестраивать: ждать возврата в исходное окно.
Учёт по фактическим количествам монет: позиция по формулам CL, награды = L * dRGG / 2^128 (только в диапазоне),
AERO -> USD по часовой цене; обмен 0.03 % от обменянного объёма, газ 0.03 USD за перестройку.
База: 113 случайных стартов (seed 113) на горизонтах 14 и 30 дней по часовым семплам локального коллектора.
Выход: JSON в stdout (числа отчёта дословно оттуда).
USAGE: python3 weth_rerange_modes.py [DATA_DIR]
"""
import glob
import json
import math
import random
import statistics
import sys
from pathlib import Path

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "data/pool_samples")
SIZE = 150.0
W = 0.10
TS = 50                       # tickSpacing
SWAP_PCT = 0.0003
GAS = 0.03
PARK = 0.067
N_STARTS, SEED = 113, 113
HORIZONS = (14, 30)
Q96, Q128, DEC = 2 ** 96, 2 ** 128, 1e12       # token0 WETH(18), token1 USDC(6)
RATIO = 1.1 / 0.9                               # лог-ширина окна ±10 %


def load():
    rows = []
    for f in sorted(glob.glob(str(DATA / "samples_*.jsonl"))):
        for line in open(f, errors="replace"):
            if line.strip():
                r = json.loads(line)
                if r.get("rewardGrowthGlobalX128") and r.get("aero_sqrtPriceX96"):
                    rows.append(r)
    rows.sort(key=lambda r: r["ts"])
    out, seen = [], set()
    for r in rows:
        if r["ts"] in seen:
            continue
        seen.add(r["ts"])
        raw = (int(r["sqrtPriceX96"]) / Q96) ** 2                   # USDCraw за WETHraw
        out.append({"ts": r["ts"], "raw": raw, "tick": r["tick"],
                    "aero": 1.0 / ((int(r["aero_sqrtPriceX96"]) / Q96) ** 2 * 1e-12),
                    "rgg": int(r["rewardGrowthGlobalX128"], 16)})
    return out


def tick_of(raw):
    return math.log(raw) / math.log(1.0001)


def amounts(L, raw, lo_t, hi_t):
    """(WETHraw, USDCraw) позиции L при цене raw."""
    sp, sl, sh = math.sqrt(raw), math.sqrt(1.0001 ** lo_t), math.sqrt(1.0001 ** hi_t)
    sp = min(max(sp, sl), sh)
    return L * (1 / sp - 1 / sh), L * (sp - sl)


def per_l(raw, lo_t, hi_t):
    x, y = amounts(1.0, raw, lo_t, hi_t)
    return x, y, x * raw + y


class Pos:
    def __init__(self):
        self.L, self.lo, self.hi = 0.0, 0, 0
        self.side = "center"          # для B1: 'above' — окно над ценой (держим WETH), 'below' — под ценой (USDC)


def mint(pos, value_usdc_raw, raw, lo_t, hi_t):
    _, _, v = per_l(raw, lo_t, hi_t)
    pos.L, pos.lo, pos.hi = value_usdc_raw / v, lo_t, hi_t


def center_ticks(raw):
    lo = int(tick_of(raw * (1 - W)) // TS * TS)
    hi = (int(tick_of(raw * (1 + W)) // TS) + 1) * TS
    return lo, hi


def swap_cost_usd(x_have, y_have, x_need, y_need, raw):
    moved = abs(x_need - x_have) * raw / 1e6 if x_need > x_have else abs(y_need - y_have) / 1e6
    return moved * SWAP_PCT


def simulate(S, i0, i1, mode, a=0.02, b1_ratio=RATIO):
    s0 = S[i0]
    pos = Pos()
    lo, hi = center_ticks(s0["raw"])
    mint(pos, SIZE * 1e6, s0["raw"], lo, hi)
    x0, y0 = amounts(pos.L, s0["raw"], lo, hi)                     # холд исходных монет
    rew_aero = rew_usd = costs = 0.0
    reranges = 0
    hours_in = hours_out = 0
    waits, wait_start = [], None                                   # B1: эпизоды ожидания (ч)
    for h in range(i0 + 1, i1 + 1):
        s, p = S[h], S[h - 1]
        inr = pos.lo <= s["tick"] < pos.hi
        if inr:
            d = (s["rgg"] - p["rgg"]) * pos.L / Q128 / 1e18
            rew_aero += d
            rew_usd += d * s["aero"]
            hours_in += 1
            if wait_start is not None:
                waits.append(h - wait_start)
                wait_start = None
            continue
        hours_out += 1
        if mode == "C":
            continue
        below = s["tick"] < pos.lo
        if mode == "B1" and ((pos.side == "above" and below) or (pos.side == "below" and not below)):
            if wait_start is None:
                wait_start = h
            continue                                               # ждём возврата в своё окно
        x, y = amounts(pos.L, s["raw"], pos.lo, pos.hi)
        val = x * s["raw"] + y
        if mode == "A":
            lo, hi = center_ticks(s["raw"])
        elif mode == "B1":
            if below:                                              # весь WETH -> окно над ценой
                lo = (math.floor(s["tick"] / TS) + 1) * TS
                hi = (int(tick_of(s["raw"] * b1_ratio) // TS) + 1) * TS
                pos.side = "above"
            else:                                                  # весь USDC -> окно под ценой
                hi = math.floor(s["tick"] / TS) * TS
                lo = int(tick_of(s["raw"] / b1_ratio) // TS * TS)
                pos.side = "below"
            if wait_start is not None:
                waits.append(h - wait_start)
            wait_start = h
        else:                                                      # B2: цена у края на a внутрь
            if below:
                lo = int(tick_of(s["raw"] * (1 - a)) // TS * TS)
                hi = (int(tick_of(s["raw"] * (1 - a) * RATIO) // TS) + 1) * TS
            else:
                hi = (int(tick_of(s["raw"] * (1 + a)) // TS) + 1) * TS
                lo = int(tick_of(s["raw"] * (1 + a) / RATIO) // TS * TS)
        px, py, pv = per_l(s["raw"], lo, hi)
        need_x, need_y = px * val / pv, py * val / pv
        c = swap_cost_usd(x, y, need_x, need_y, s["raw"]) + GAS
        costs += c
        mint(pos, val - c * 1e6, s["raw"], lo, hi)
        reranges += 1
    if wait_start is not None:
        waits.append(i1 - wait_start)                              # цензурировано концом окна
    e = S[i1]
    x, y = amounts(pos.L, e["raw"], pos.lo, pos.hi)
    lp_usd = (x * e["raw"] + y) / 1e6
    hodl = (x0 * e["raw"] + y0) / 1e6
    days = (e["ts"] - s0["ts"]) / 86400
    equity = lp_usd + rew_usd
    return {"ret_usdc": (equity - SIZE) / SIZE, "vs_hodl_usd": equity - hodl, "lp_vs_hodl_usd": lp_usd + costs - hodl,
            "rew_usd": rew_usd, "costs_usd": costs, "reranges": reranges, "in_range": hours_in / max(1, hours_in + hours_out),
            "days_no_rew": hours_out / 24, "waits_h": waits, "days": days, "eth_ret": e["raw"] / s0["raw"] - 1}


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))]


def summarize(rs):
    ret = [r["ret_usdc"] for r in rs]
    days = statistics.mean(r["days"] for r in rs)
    apr = [x * 365 / r["days"] for x, r in zip(ret, rs)]
    waits = [w / 24 for r in rs for w in r["waits_h"]]
    out = {"n": len(rs), "days": round(days, 2),
           "ret_med_pct": round(100 * statistics.median(ret), 2), "ret_p10_pct": round(100 * q(ret, 0.1), 2),
           "ret_p90_pct": round(100 * q(ret, 0.9), 2),
           "apr_med_pct": round(100 * statistics.median(apr), 1), "apr_p10_pct": round(100 * q(apr, 0.1), 1),
           "beat_parking_share": round(sum(x > PARK * r["days"] / 365 for x, r in zip(ret, rs)) / len(rs), 3),
           "p_lt_m5": round(sum(x < -0.05 for x in ret) / len(rs), 3),
           "p_lt_m10": round(sum(x < -0.10 for x in ret) / len(rs), 3),
           "vs_hodl_med_usd": round(statistics.median(r["vs_hodl_usd"] for r in rs), 2),
           "vs_hodl_p10_usd": round(q([r["vs_hodl_usd"] for r in rs], 0.1), 2),
           "rew_med_usd": round(statistics.median(r["rew_usd"] for r in rs), 2),
           "lp_vs_hodl_med_usd": round(statistics.median(r["lp_vs_hodl_usd"] for r in rs), 2),
           "costs_med_usd": round(statistics.median(r["costs_usd"] for r in rs), 3),
           "reranges_med": statistics.median(r["reranges"] for r in rs),
           "in_range_med": round(statistics.median(r["in_range"] for r in rs), 3),
           "days_no_rew_mean": round(statistics.mean(r["days_no_rew"] for r in rs), 2),
           "days_no_rew_med": round(statistics.median(r["days_no_rew"] for r in rs), 2),
           "days_no_rew_p90": round(q([r["days_no_rew"] for r in rs], 0.9), 2)}
    if waits:
        out.update({"wait_episodes": len(waits), "wait_mean_d": round(statistics.mean(waits), 2),
                    "wait_med_d": round(statistics.median(waits), 2), "wait_p90_d": round(q(waits, 0.9), 2)})
    return out


def main():
    S = load()
    res = {"samples": len(S), "from": S[0]["ts"], "to": S[-1]["ts"],
           "eth_from": round(S[0]["raw"] * DEC, 1), "eth_to": round(S[-1]["raw"] * DEC, 1), "horizons": {}}
    for H in HORIZONS:
        end_ts = S[-1]["ts"] - H * 86400
        idx = [i for i, s in enumerate(S) if s["ts"] <= end_ts]
        if not idx:
            continue
        rng = random.Random(SEED)
        starts = sorted(rng.sample(idx, min(N_STARTS, len(idx))))
        ends = []
        for i0 in starts:
            t1 = S[i0]["ts"] + H * 86400
            ends.append(next(j for j in range(i0, len(S)) if S[j]["ts"] >= t1))
        modes = {"A": dict(mode="A"), "B1": dict(mode="B1"), "B1_narrow": dict(mode="B1", b1_ratio=1 + W), "C": dict(mode="C")}
        for a in (0.01, 0.02, 0.03):
            modes[f"B2_a{int(a * 100)}"] = dict(mode="B2", a=a)
        hz = {"n_starts": len(starts), "start_span_days": round((S[idx[-1]]["ts"] - S[idx[0]]["ts"]) / 86400, 1),
              "eth_ret_med_pct": round(100 * statistics.median(S[j]["raw"] / S[i]["raw"] - 1 for i, j in zip(starts, ends)), 2),
              "modes": {}}
        runs = {name: [simulate(S, i0, i1, **kw) for i0, i1 in zip(starts, ends)] for name, kw in modes.items()}
        hit = [k for k, r in enumerate(runs["A"]) if r["reranges"] > 0]        # старты, где выбор режима важен
        hz["starts_with_exit"] = len(hit)
        for name, rs in runs.items():
            sm = summarize(rs)
            if name != "A":
                d = [(r["ret_usdc"] - a["ret_usdc"]) * SIZE for r, a in zip(rs, runs["A"])]
                dh = [d[k] for k in hit]
                sm["vs_A_all"] = {"mean_usd": round(statistics.mean(d), 3), "share_better": round(sum(x > 0.005 for x in d) / len(d), 3),
                                  "share_worse": round(sum(x < -0.005 for x in d) / len(d), 3)}
                if dh:
                    sm["vs_A_exit_starts"] = {"n": len(dh), "mean_usd": round(statistics.mean(dh), 2),
                                              "med_usd": round(statistics.median(dh), 2), "p10_usd": round(q(dh, 0.1), 2),
                                              "p90_usd": round(q(dh, 0.9), 2), "share_better": round(sum(x > 0 for x in dh) / len(dh), 3)}
            if hit:
                sub = [rs[k] for k in hit]
                sm["exit_starts"] = {k: v for k, v in summarize(sub).items()
                                     if k in ("ret_med_pct", "ret_p10_pct", "p_lt_m5", "p_lt_m10", "rew_med_usd",
                                              "lp_vs_hodl_med_usd", "in_range_med", "days_no_rew_mean", "days_no_rew_med",
                                              "days_no_rew_p90", "wait_mean_d", "wait_med_d", "wait_p90_d", "reranges_med")}
            hz["modes"][name] = sm
        res["horizons"][f"{H}d"] = hz
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
