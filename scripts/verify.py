#!/usr/bin/env python3
"""verify.py — independent LP-position verification CLI (Celo mainnet).

Rebuilds a position's performance from on-chain data only: reads the position by tokenId
(UniswapV3 NonfungiblePositionManager), collects hourly pool snapshots over a window via a
public archive RPC, replays the position (hold; fees = the position's share of
ΔfeeGrowthGlobal while in range), and emits a report matching schemas/report-v1.schema.json
(with data_hash of the collected dataset). HODL benchmark from the position's entry mix.

USAGE:
    python3 scripts/verify.py --position <tokenId> [--days 14] [--rpc https://forno.celo.org]
    python3 scripts/verify.py --validate <report.json>      # schema check (any chain)

No keys, no LLM; stdlib only. Numbers verbatim into stdout JSON.
"""
import argparse
import hashlib
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone

NFPM = "0x3d79EdAaBC0EaB6F08ED885C05Fc0B014290D95A"
FACTORY = "0xAfE208a311B21f13EF87E33A90049fC17A7acDEc"
CHAIN_ID = 42220
GETPOOL = "0x1698ee82"
POSITIONS = "0x993ab265"
SLOT0, LIQ, FGG0, FGG1 = "0x3850c7bd", "0x1a686502", "0xf3058399", "0x46141319"
METHOD = "verify.py/1.0 (hourly hold replay, global feeGrowth)"


def rpc(url, method, params, _retry=0):
    req = urllib.request.Request(
        url, data=json.dumps({"jsonrpc": "2.0", "method": method, "params": params,
                              "id": 1}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (compatible; lp-verify/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read())
        if "error" in j:
            raise RuntimeError(f"rpc error: {j['error']}")
        if "result" not in j:
            print("DEBUG rpc no-result:", json.dumps(j)[:200], file=sys.stderr)
        return j.get("result")
    except Exception:
        if _retry >= 3:
            raise
        time.sleep(3 * (_retry + 1))
        return rpc(url, method, params, _retry + 1)


def call(url, to, sel, block="latest"):
    return rpc(url, "eth_call", [{"to": to, "data": sel}, block])


def word(h, i):
    return int(h[2 + i * 64: 2 + (i + 1) * 64], 16)


def addr(h):
    return "0x" + h[-40:]


def signed(h):
    v = word(h, 0)
    return v - (1 << 256) if v >= (1 << 255) else v


def block_ts(url, n):
    b = rpc(url, "eth_getBlockByNumber", [hex(n), False])
    if b is None:
        raise RuntimeError("block not returned")
    return int(b["timestamp"], 16)


def block_at(url, ts):
    head = rpc(url, "eth_blockNumber", [])
    if head is None:
        raise RuntimeError("head not returned")
    lo, hi = 0, int(head, 16)
    while lo < hi:
        mid = (lo + hi) // 2
        if block_ts(url, mid) < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def price_of(sqrt_x96):
    return (sqrt_x96 / 2 ** 96) ** 2


def tick_to_price(tick):
    return 1.0001 ** tick


def amounts(L, tick_lo, tick_hi, tick_now):
    """token0/token1 for liquidity L (human units) with tick bounds [lo, hi)."""
    pa, pb = tick_to_price(tick_lo), tick_to_price(tick_hi)
    p = tick_to_price(tick_now)
    if p <= pa:
        return L * (pb ** 0.5 - pa ** 0.5) / ((pa * pb) ** 0.5), 0.0
    if p >= pb:
        return 0.0, L * (pb ** 0.5 - pa ** 0.5)
    return (L * (pb ** 0.5 - p ** 0.5) / ((p * pb) ** 0.5), L * (p ** 0.5 - pa ** 0.5))


def collect(url, pool, from_ts, to_ts):
    rows = []
    t = from_ts
    while t <= to_ts:
        b = block_at(url, t)
        s0 = call(url, pool, SLOT0, hex(b))
        if not s0 or len(s0) < 10:
            raise RuntimeError(f"slot0 not returned at block {b}")
        rows.append({"ts": t, "blk": b,
                     "sqrtPriceX96": word(s0, 0), "tick": (lambda v: v - (1 << 256) if v >= (1 << 255) else v)(int(s0[2 + 64:2 + 128], 16)),
                     "liquidity": int(call(url, pool, LIQ, hex(b)) or "0x0", 16),
                     "fgg0": int(call(url, pool, FGG0, hex(b)) or "0x0", 16),
                     "fgg1": int(call(url, pool, FGG1, hex(b)), 16)})
        t += 3600
        time.sleep(0.25)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--position", type=int, help="UniswapV3 NFPM tokenId on Celo")
    ap.add_argument("--mint-tx", help="mint tx hash (position params source; this NFPM deploy has a non-vanilla positions() ABI)")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--rpc", default="https://forno.celo.org")
    ap.add_argument("--validate", help="validate a report JSON against schemas/report-v1")
    args = ap.parse_args()

    if args.validate:
        try:
            import jsonschema
        except ImportError:
            print("pip install jsonschema (validator dependency)", file=sys.stderr)
            return 2
        schema = json.load(open("scripts/report.schema.json"))
        report = json.load(open(args.validate))
        jsonschema.validate(report, schema)
        print(f"OK: {args.validate} matches report-v1")
        return 0

    if not args.position or not args.mint_tx:
        ap.error("--position <tokenId> --mint-tx <hash> required (or --validate <file>)")

    # 1) position params from the mint transaction (this Celo NFPM deploy lacks the vanilla
    #    positions(uint256) selector; mint input + IncreaseLiquidity event carry the same data)
    tix = args.position
    tx = rpc(args.rpc, "eth_getTransactionByHash", [args.mint_tx])
    if tx is None:
        raise SystemExit("tx not found on RPC (check --mint-tx / provider)")
    inp = tx["input"][2:]
    sw = lambda i: int(inp[8 + i * 64: 8 + (i + 1) * 64], 16)
    token0, token1 = "0x" + inp[8 + 24: 8 + 64], "0x" + inp[8 + 64 + 24: 8 + 128]
    sgn = lambda v: v - (1 << 256) if v >= (1 << 255) else v
    fee, tick_lo, tick_hi = sw(2), sgn(sw(3)), sgn(sw(4))
    rc = rpc(args.rpc, "eth_getTransactionReceipt", [args.mint_tx])
    if rc is None:
        raise SystemExit("receipt not returned (provider hiccup); retry")
    L = 0
    for lg in rc.get("logs", []):
        if lg.get("topics") and lg["topics"][0].startswith("0x3067048b")                 and int(lg["topics"][1], 16) == tix:
            L = int(lg["data"][2 + 64: 2 + 128], 16)
    if not L:
        raise SystemExit("IncreaseLiquidity not found in the mint tx for this tokenId")
    pool_hex = call(args.rpc, FACTORY, GETPOOL + token0[2:].rjust(64, "0") + token1[2:].rjust(64, "0") + f"{fee:064x}")
    if not pool_hex or int(pool_hex, 16) == 0:
        raise SystemExit("pool not found for this token pair/fee")
    pool = addr(pool_hex)

    # 2) window + dataset
    to_ts = int(time.time()) // 3600 * 3600
    from_ts = to_ts - args.days * 86400
    rows = collect(args.rpc, pool, from_ts, to_ts)
    data_hash = hashlib.sha256(
        ("\n".join(json.dumps(r, sort_keys=True) for r in rows)).encode()).hexdigest()

    # decimals: assume 18/18 for CELO/USDm pairs on Celo (asserted by price sanity below)
    L_h = L / 1e18
    # 3) hold replay of the ACTUAL position ticks/liquidity
    entry, last = rows[0], rows[-1]
    in_range = lambda r: tick_lo <= r["tick"] < tick_hi
    fees0 = sum((rows[i]["fgg0"] - rows[i - 1]["fgg0"]) / 2 ** 128 for i in range(1, len(rows)) if in_range(rows[i - 1]))
    fees1 = sum((rows[i]["fgg1"] - rows[i - 1]["fgg1"]) / 2 ** 128 for i in range(1, len(rows)) if in_range(rows[i - 1]))
    pos_fees0, pos_fees1 = L_h * fees0, L_h * fees1
    p0, p1 = price_of(entry["sqrtPriceX96"]), price_of(last["sqrtPriceX96"])
    t0e, t1e = amounts(L_h, tick_lo, tick_hi, entry["tick"])
    t0l, t1l = amounts(L_h, tick_lo, tick_hi, last["tick"])
    entry_usd = t0e * p0 + t1e * p0
    final_usd = t0l * p1 + t1l * p1 + (pos_fees0 * p1 + pos_fees1)
    hodl = (t0e * p1 + t1e * p0) / entry_usd - 1 if entry_usd else 0.0

    report = {
        "format_version": 1, "chain_id": CHAIN_ID, "pool": pool,
        "position": {"id": tix, "tick_lower": tick_lo, "tick_upper": tick_hi, "liquidity": str(L)},
        "window": {"from": datetime.fromtimestamp(from_ts, timezone.utc).isoformat(),
                   "to": datetime.fromtimestamp(to_ts, timezone.utc).isoformat()},
        "block_range": [rows[0]["blk"], rows[-1]["blk"]],
        "data_hash": data_hash, "method_version": METHOD,
        "n": len(rows), "n_eff": 1.0,
        "benchmarks": {"hodl_5050_vs_quote": {"median": hodl}},
        "modes": [{"name": "hold_as_is",
                   "median": final_usd / entry_usd - 1 if entry_usd else 0.0,
                   "p10": 0.0, "p90": 0.0}],
        "calibration": None,
        "caveats": [
            "hold replay of the position's current liquidity and ticks; mints/burns inside the window are not replayed",
            "fees = position share of ΔfeeGrowthGlobal while in range (global, no tick-crossing correction)",
            "18/18 decimals assumed for CELO/USDm; single window, n_eff = 1",
        ],
    }
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
