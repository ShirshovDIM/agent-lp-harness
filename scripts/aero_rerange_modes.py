#!/usr/bin/env python3
"""Перестройка USDC-AERO CL2000 (Aerodrome Slipstream, live pilot) после выхода из диапазона: обмен против «без обмена».
Аналог weth_rerange_modes.py; без LLM, детерминированно.

Пул 0xBE00…A4c1: token0 USDC(6), token1 AERO(18), tickSpacing 2000 (шаг сетки ×1.2214 по цене, ≈22 %).
raw = AEROraw за USDCraw; AERO_USD = 1 / (raw * 1e-12). Тик выше — AERO дешевле. Выше окна позиция целиком в AERO,
ниже окна — целиком в USDC. Стоимость в USDCraw = x + y / raw.

Режимы (размер 150 USD; A/B1/B2/C входят одинаково — по правилу A):
  A       — живое правило пилота: ширина w = clamp(2·σ6, 2 %, 15 %), σ6 = sqrt(Σ r²) по 6 часовым лог-доходностям
            (живой движок берёт свечи внешнего источника, здесь — часовые цены пула); диапазон [P/(1+w), P/(1−w)] наружу
            до сетки 2000; по выходу закрыть, обменять до пропорции, встать по центру.
  A_fix30 — то же с фиксированной w = 30 % (вход и перестройка), как пилот открыт 05.10.
  B1      — по выходу без обмена: всё, что на руках, в окно из n клеток вплотную к цене с «своей» стороны
            (AERO — под тиком, USDC — над тиком); n = число клеток окна A в этот час. Ждём возврата (наград нет).
            Сетка 2000: до ближайшей линии сетки от 0 до 22 % цены — столько AERO должен отыграть до первой награды.
  B1_1    — как B1, но окно в одну клетку.
  B2      — малый обмен: окно из n клеток, содержащее цену, с краем на ближайшей линии сетки со «своей» стороны.
            Тонкого «a % от края» на сетке 2000 нет: расстояние до края задаёт сетка (0–22 %), меняется доля обмена.
  C       — не перестраивать, ждать возврата в исходное окно.
  R70     — справка: вход ±70 % (как A-3), без перестроек.
Учёт: позиция по формулам CL, награды = L·ΔRGG/2^128/1e18 AERO только в диапазоне, AERO → USD по часовой цене;
обмен SWAP_PCT от обменянного объёма (база 0.05 % CL100, стресс 0.6 % — разница CL100 и CL2000 при входе 05.10),
газ 0.03 USD за перестройку. 113 случайных стартов (seed 113), горизонты 14 и 30 дней.
Данные: samples_*.jsonl (aero_sqrtPriceX96) + aero_rgg.jsonl (rgg пула USDC-AERO по тем же блокам).
Блок "mirror" — синтетический путь с отражённой лог-ценой AERO (в окне данных AERO почти только рос: падающих
стартов с выходом нет), поток наград тот же. Это проверка чувствительности, а не история.
USAGE: python3 aero_rerange_modes.py [DATA_DIR]   → JSON в stdout
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
TS = 2000
GAS = 0.03
SWAP_BASE, SWAP_STRESS = 0.0005, 0.006
PARK = 0.067
N_STARTS, SEED = 113, 113
HORIZONS = (14, 30)
Q96, Q128 = 2 ** 96, 2 ** 128
VOL_N, VOL_K, VOL_MIN, VOL_MAX = 6, 2.0, 0.02, 0.15
# calibration anchor: public on-chain data only (ClaimRewards events of tokenId below over the window);
# position L/range are withheld (run-time args for reproduction)
ANCHOR = {"token_id": 77587480, "t0": 1791201318, "t1": 1791548537, "claimed_aero": 1.084258}


def load():
    rgg = {}
    for line in (DATA / "aero_rgg.jsonl").open(errors="replace"):
        if line.strip():
            r = json.loads(line)
            rgg[r["blk"]] = int(r["rgg"], 16)
    out, seen = [], set()
    rows = []
    for f in sorted(glob.glob(str(DATA / "samples_*.jsonl"))):
        for line in open(f, errors="replace"):
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["ts"])
    for r in rows:
        if r["ts"] in seen or r["blk"] not in rgg or not r.get("aero_sqrtPriceX96"):
            continue
        seen.add(r["ts"])
        raw = (int(r["aero_sqrtPriceX96"]) / Q96) ** 2
        out.append({"ts": r["ts"], "blk": r["blk"], "raw": raw, "tick": math.floor(math.log(raw) / math.log(1.0001)),
                    "aero": 1.0 / (raw * 1e-12), "rgg": rgg[r["blk"]]})
    return out


def tick_of(raw):
    return math.log(raw) / math.log(1.0001)


def amounts(L, raw, lo_t, hi_t):
    """(USDCraw, AEROraw) позиции L при цене raw."""
    sp, sl, sh = math.sqrt(raw), math.sqrt(1.0001 ** lo_t), math.sqrt(1.0001 ** hi_t)
    sp = min(max(sp, sl), sh)
    return L * (1 / sp - 1 / sh), L * (sp - sl)


def per_l(raw, lo_t, hi_t):
    x, y = amounts(1.0, raw, lo_t, hi_t)
    return x, y, x + y / raw


def vol_width(S, h):
    r = [math.log(S[j]["raw"] / S[j - 1]["raw"]) for j in range(max(1, h - VOL_N + 1), h + 1)]
    if len(r) < VOL_N:
        return 0.10
    return min(max(VOL_K * math.sqrt(sum(x * x for x in r)), VOL_MIN), VOL_MAX)


def center_ticks(raw, w):
    """Как ticks_for живого a3_multi: AERO в token1, диапазон по цене AERO/USDC [px/(1+w), px/(1−w)] наружу до сетки."""
    lo = math.floor(tick_of(raw / (1 + w)) / TS) * TS
    hi = math.ceil(tick_of(raw / (1 - w)) / TS) * TS
    return lo, hi


class Pos:
    def __init__(self):
        self.L, self.lo, self.hi, self.side = 0.0, 0, 0, "center"


def mint(pos, value_raw, raw, lo_t, hi_t):
    _, _, v = per_l(raw, lo_t, hi_t)
    pos.L, pos.lo, pos.hi = value_raw / v, lo_t, hi_t


def simulate(S, i0, i1, mode, swap_pct=SWAP_BASE):
    s0 = S[i0]
    pos = Pos()
    if mode == "A_fix30":
        lo, hi = center_ticks(s0["raw"], 0.30)
    elif mode == "R70":
        lo, hi = center_ticks(s0["raw"], 0.70)
    else:
        lo, hi = center_ticks(s0["raw"], vol_width(S, i0))
    mint(pos, SIZE * 1e6, s0["raw"], lo, hi)
    x0, y0 = amounts(pos.L, s0["raw"], lo, hi)
    rew_usd = costs = 0.0
    reranges = hours_in = hours_out = 0
    waits, wait_start = [], None
    for h in range(i0 + 1, i1 + 1):
        s, p = S[h], S[h - 1]
        if pos.lo <= s["tick"] < pos.hi:
            rew_usd += (s["rgg"] - p["rgg"]) * pos.L / Q128 / 1e18 * s["aero"]
            hours_in += 1
            if wait_start is not None:
                waits.append(h - wait_start)
                wait_start = None
            continue
        hours_out += 1
        if mode in ("C", "R70"):
            continue
        below = s["tick"] < pos.lo                    # ниже окна: всё в USDC (AERO подорожал)
        if mode in ("B1", "B1_1") and ((pos.side == "above" and below) or (pos.side == "below" and not below)):
            if wait_start is None:
                wait_start = h
            continue
        x, y = amounts(pos.L, s["raw"], pos.lo, pos.hi)
        val = x + y / s["raw"]
        if mode == "A_fix30":
            lo, hi = center_ticks(s["raw"], 0.30)
        else:
            lo, hi = center_ticks(s["raw"], vol_width(S, h))
        n = max(1, (hi - lo) // TS)
        if mode in ("B1", "B1_1"):
            n = 1 if mode == "B1_1" else n
            if below:                                 # USDC -> окно над тиком
                lo = (math.floor(s["tick"] / TS) + 1) * TS
                hi = lo + n * TS
                pos.side = "above"
            else:                                     # AERO -> окно под тиком
                hi = math.floor(s["tick"] / TS) * TS
                lo = hi - n * TS
                pos.side = "below"
            if wait_start is not None:
                waits.append(h - wait_start)
            wait_start = h
        elif mode == "B2":
            g = math.floor(s["tick"] / TS) * TS        # клетка, где сейчас цена: [g, g+TS)
            if below:                                 # держим USDC -> нижний край на линии под ценой
                lo, hi = g, g + n * TS
            else:                                     # держим AERO -> верхний край на линии над ценой
                lo, hi = g + TS - n * TS, g + TS
        px, py, pv = per_l(s["raw"], lo, hi)
        need_x = px * val / pv
        moved = abs(need_x - x)                       # USDCraw-эквивалент обмена в любую сторону
        c = moved / 1e6 * swap_pct + GAS
        costs += c
        mint(pos, val - c * 1e6, s["raw"], lo, hi)
        reranges += 1
    if wait_start is not None:
        waits.append(i1 - wait_start)
    e = S[i1]
    x, y = amounts(pos.L, e["raw"], pos.lo, pos.hi)
    lp_usd = (x + y / e["raw"]) / 1e6
    hodl = (x0 + y0 / e["raw"]) / 1e6
    equity = lp_usd + rew_usd
    return {"ret_usdc": (equity - SIZE) / SIZE, "vs_hodl_usd": equity - hodl, "lp_vs_hodl_usd": lp_usd + costs - hodl,
            "rew_usd": rew_usd, "costs_usd": costs, "reranges": reranges, "in_range": hours_in / max(1, hours_in + hours_out),
            "days_no_rew": hours_out / 24, "waits_h": waits, "days": (e["ts"] - s0["ts"]) / 86400,
            "aero_ret": e["aero"] / s0["aero"] - 1, "cells0": (pos.hi - pos.lo) // TS}


def q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))]


def summarize(rs):
    ret = [r["ret_usdc"] for r in rs]
    waits = [w / 24 for r in rs for w in r["waits_h"]]
    out = {"n": len(rs), "days": round(statistics.mean(r["days"] for r in rs), 2),
           "ret_med_pct": round(100 * statistics.median(ret), 2), "ret_p10_pct": round(100 * q(ret, 0.1), 2),
           "ret_p90_pct": round(100 * q(ret, 0.9), 2),
           "beat_parking_share": round(sum(x > PARK * r["days"] / 365 for x, r in zip(ret, rs)) / len(rs), 3),
           "p_lt_m5": round(sum(x < -0.05 for x in ret) / len(rs), 3),
           "p_lt_m10": round(sum(x < -0.10 for x in ret) / len(rs), 3),
           "vs_hodl_med_usd": round(statistics.median(r["vs_hodl_usd"] for r in rs), 2),
           "vs_hodl_p10_usd": round(q([r["vs_hodl_usd"] for r in rs], 0.1), 2),
           "rew_med_usd": round(statistics.median(r["rew_usd"] for r in rs), 2),
           "lp_vs_hodl_med_usd": round(statistics.median(r["lp_vs_hodl_usd"] for r in rs), 2),
           "costs_med_usd": round(statistics.median(r["costs_usd"] for r in rs), 3),
           "reranges_med": statistics.median(r["reranges"] for r in rs),
           "reranges_mean": round(statistics.mean(r["reranges"] for r in rs), 2),
           "in_range_med": round(statistics.median(r["in_range"] for r in rs), 3),
           "days_no_rew_mean": round(statistics.mean(r["days_no_rew"] for r in rs), 2),
           "days_no_rew_med": round(statistics.median(r["days_no_rew"] for r in rs), 2),
           "days_no_rew_p90": round(q([r["days_no_rew"] for r in rs], 0.9), 2)}
    if waits:
        out.update({"wait_episodes": len(waits), "wait_mean_d": round(statistics.mean(waits), 2),
                    "wait_med_d": round(statistics.median(waits), 2), "wait_p90_d": round(q(waits, 0.9), 2)})
    return out


def diff_stats(d):
    return {"n": len(d), "mean_usd": round(statistics.mean(d), 2), "med_usd": round(statistics.median(d), 2),
            "p10_usd": round(q(d, 0.1), 2), "p90_usd": round(q(d, 0.9), 2),
            "share_better": round(sum(x > 0 for x in d) / len(d), 3)} if d else {"n": 0}


def anchor(S):
    """Модель наград на живом окне пилота против суммы claimed из журнала."""
    lo, hi = ANCHOR["range"]
    idx = [i for i, s in enumerate(S) if ANCHOR["t0"] <= s["ts"] <= ANCHOR["t1"]]
    if len(idx) < 2:
        return None
    a = sum((S[i]["rgg"] - S[i - 1]["rgg"]) * ANCHOR["L"] / Q128 / 1e18
            for i in idx[1:] if lo <= S[i]["tick"] < hi)
    return {"model_aero": round(a, 4), "claimed_aero": ANCHOR["claimed_aero"], "ratio": round(a / ANCHOR["claimed_aero"], 4),
            "from": S[idx[0]]["ts"], "to": S[idx[-1]]["ts"]}


def mirror(S):
    """Синтетика «AERO падает»: лог-цена отражена относительно первой точки (tick' = 2·tick0 − tick), поток RGG тот же."""
    r0 = S[0]["raw"]
    out = []
    for s in S:
        raw = r0 * r0 / s["raw"]
        out.append({**s, "raw": raw, "tick": math.floor(tick_of(raw)), "aero": 1.0 / (raw * 1e-12)})
    return out


def run_all(S):
    res = {"aero_from": round(S[0]["aero"], 4), "aero_to": round(S[-1]["aero"], 4),
           "aero_min": round(min(s["aero"] for s in S), 4), "aero_max": round(max(s["aero"] for s in S), 4), "horizons": {}}
    modes = {"A": dict(mode="A"), "A_fix30": dict(mode="A_fix30"), "B1": dict(mode="B1"), "B1_1": dict(mode="B1_1"),
             "B2": dict(mode="B2"), "C": dict(mode="C"), "R70": dict(mode="R70")}
    for H in HORIZONS:
        end_ts = S[-1]["ts"] - H * 86400
        idx = [i for i, s in enumerate(S) if s["ts"] <= end_ts and i >= VOL_N]
        if not idx:
            continue
        starts = sorted(random.Random(SEED).sample(idx, min(N_STARTS, len(idx))))
        ends = [next(j for j in range(i0, len(S)) if S[j]["ts"] >= S[i0]["ts"] + H * 86400) for i0 in starts]
        hz = {"n_starts": len(starts), "start_span_days": round((S[idx[-1]]["ts"] - S[idx[0]]["ts"]) / 86400, 1),
              "aero_ret_med_pct": round(100 * statistics.median(S[j]["aero"] / S[i]["aero"] - 1 for i, j in zip(starts, ends)), 2),
              "aero_fell_starts": sum(S[j]["aero"] < S[i]["aero"] for i, j in zip(starts, ends)), "modes": {}}
        runs = {nm: [simulate(S, i0, i1, **kw) for i0, i1 in zip(starts, ends)] for nm, kw in modes.items()}
        stress = {nm: [simulate(S, i0, i1, swap_pct=SWAP_STRESS, **kw) for i0, i1 in zip(starts, ends)]
                  for nm, kw in modes.items() if nm in ("A", "A_fix30", "B1", "B2")}
        hit = [k for k, r in enumerate(runs["A"]) if r["reranges"] > 0]
        fell = [k for k in hit if runs["A"][k]["aero_ret"] < 0]
        fell10 = [k for k in hit if runs["A"][k]["aero_ret"] < -0.10]
        rose = [k for k in hit if runs["A"][k]["aero_ret"] >= 0]
        hz.update({"starts_with_exit": len(hit), "exit_aero_fell": len(fell), "exit_aero_fell10": len(fell10),
                   "entry_cells_A_med": statistics.median(r["cells0"] for r in runs["A"])})
        for nm, rs in runs.items():
            sm = summarize(rs)
            if nm != "A":
                d = [(r["ret_usdc"] - a["ret_usdc"]) * SIZE for r, a in zip(rs, runs["A"])]
                sm["vs_A_exit_starts"] = diff_stats([d[k] for k in hit])
                sm["vs_A_exit_aero_fell"] = diff_stats([d[k] for k in fell])
                sm["vs_A_exit_aero_fell10"] = diff_stats([d[k] for k in fell10])
                sm["vs_A_exit_aero_rose"] = diff_stats([d[k] for k in rose])
                if nm in stress:
                    ds = [(r["ret_usdc"] - a["ret_usdc"]) * SIZE for r, a in zip(stress[nm], stress["A"])]
                    sm["vs_A_exit_starts_swap06"] = diff_stats([ds[k] for k in hit])
            if hit:
                sm["exit_starts"] = {k: v for k, v in summarize([rs[k] for k in hit]).items()
                                     if k in ("ret_med_pct", "ret_p10_pct", "ret_p90_pct", "p_lt_m5", "p_lt_m10", "rew_med_usd",
                                              "vs_hodl_med_usd", "vs_hodl_p10_usd", "lp_vs_hodl_med_usd", "costs_med_usd",
                                              "in_range_med", "days_no_rew_mean", "days_no_rew_med", "days_no_rew_p90",
                                              "wait_mean_d", "wait_med_d", "wait_p90_d", "reranges_med", "reranges_mean")}
            if fell:
                sm["exit_aero_fell"] = {k: v for k, v in summarize([rs[k] for k in fell]).items()
                                        if k in ("ret_med_pct", "ret_p10_pct", "p_lt_m5", "p_lt_m10", "days_no_rew_med")}
            hz["modes"][nm] = sm
        res["horizons"][f"{H}d"] = hz
    return res


def main():
    S = load()
    res = {"samples": len(S), "from": S[0]["ts"], "to": S[-1]["ts"], "anchor": anchor(S),
           "actual": run_all(S), "mirror": run_all(mirror(S))}
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
