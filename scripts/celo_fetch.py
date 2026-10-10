#!/usr/bin/env python3
"""Celo UniswapV3 CELO/cUSD fee=100 (0.01%) collector — hourly samples, checkpointed, resumable.

Hourly {slot0(sqrtPriceX96, tick), liquidity, feeGrowthGlobal0/1} for the verification-report
pilot, same shape as the Base pool-sample fetcher (no keys, no deps). Backfill 30 d + live tail.

Pool 0x2d70cbabf4d8e61d5317b62cbe912935fd94e0fe: UniswapV3Factory(0xAfE208a311B21f13EF87E33A90049fC17A7acDEc)
.getPool(CELO 0x471EcE3750Da237f93B8E339c536989b8978a438, cUSD 0x765DE816845861e75A25fCA122bb6898B8B1282a, 100),
liquidity ~1.05e21 (highest of the four fee tiers, 10.10 RPC). token0/token1/symbols are asserted at start.
RPC https://forno.celo.org — archive eth_call verified 10.10 (slot0 at -30 d block returned).
Kill switch: file STOP_CELO_POOL next to the script.
USAGE: celo_fetch.py --days 30 [--backfill] (backfill if no checkpoint; otherwise tail)
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DATA.mkdir(parents=True, exist_ok=True)
STOP = HERE / "STOP_CELO_POOL"
OUT = DATA / "samples.jsonl"
RPC = "https://forno.celo.org"
FACTORY = "0xAfE208a311B21f13EF87E33A90049fC17A7acDEc"
CELO, CUSD = "0x471ece3750da237f93b8e339c536989b8978a438", "0x765de816845861e75a25fca122bb6898b8b1282a"
POOL = "0x2d70cbabf4d8e61d5317b62cbe912935fd94e0fe"
SLOT0, LIQ, FGG0, FGG1 = "0x3850c7bd", "0x1a686502", "0xf3058399", "0x46141319"
SYM, TOKEN0, TOKEN1 = "0x95d89b41", "0x0dfe1681", "0xd21220a7"
PACE, BACKOFF = 0.3, (10, 30, 60)


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_stop():
    if STOP.exists():
        raise SystemExit(f"stopped: {STOP}")


def rpc_call(method, params, _retry=0):
    req = urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "method": method,
                                                       "params": params, "id": 1}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "pool-hourly-collector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read())
        if "error" in j:
            raise RuntimeError(f"rpc error: {j['error']}")
        return j["result"]
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as e:
        if _retry >= len(BACKOFF):
            raise
        time.sleep(BACKOFF[_retry])
        return rpc_call(method, params, _retry + 1)


def call_at(addr, selector, block):
    return rpc_call("eth_call", [{"to": addr, "data": selector}, block])


def word(hexdata, i):
    return int(hexdata[2 + i * 64: 2 + (i + 1) * 64], 16)


def addr_of(hexdata):
    return "0x" + hexdata[-40:]


def block_ts(n):
    b = rpc_call("eth_getBlockByNumber", [hex(n), False])
    return int(b["timestamp"], 16)


def block_at_ts(ts):
    """Binary search the first block with timestamp >= ts."""
    lo, hi = 0, int(rpc_call("eth_blockNumber", []), 16)
    while lo < hi:
        mid = (lo + hi) // 2
        if block_ts(mid) < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def sample(block_hex):
    s0 = call_at(POOL, SLOT0, block_hex)
    liq = call_at(POOL, LIQ, block_hex)
    g0 = call_at(POOL, FGG0, block_hex)
    g1 = call_at(POOL, FGG1, block_hex)
    sqrt = word(s0, 0)
    tick = word(s0, 1)
    if tick >= 1 << 255:                      # int256 tick
        tick -= 1 << 256
    return {"sqrtPriceX96": sqrt, "tick": tick, "liquidity": int(liq, 16),
            "feeGrowthGlobal0X128": int(g0, 16), "feeGrowthGlobal1X128": int(g1, 16)}


def main():
    days = 30
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    # assert the pool identity (token0/token1/symbols) — no guesses
    t0, t1 = addr_of(call_at(POOL, TOKEN0, "latest")), addr_of(call_at(POOL, TOKEN1, "latest"))
    assert sorted([t0.lower(), t1.lower()]) == sorted([CELO, CUSD]), f"unexpected tokens {t0} {t1}"
    s0, s1 = call_at(t0, SYM, "latest"), call_at(t1, SYM, "latest")

    def sym(hexstr):
        n = word(hexstr, 0)
        b = int(hexstr[2:2 + 64], 16)
        raw = b.to_bytes(32, "big")[:n].decode("utf-8", "replace") if n <= 32 else "?"
        return raw
    print(f"{utc()} pool={POOL} token0={t0}({sym(s0)}) token1={t1}({sym(s1)})", flush=True)

    done = set()
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            if line.strip():
                done.add(int(json.loads(line)["ts"]))
    print(f"{utc()} checkpoint: {len(done)} hourly samples", flush=True)

    now = int(time.time())
    start_hour = (now - days * 86400) // 3600 * 3600
    hours = list(range(start_hour, now // 3600 * 3600 + 1, 3600))
    todo = [h for h in hours if h not in done]
    print(f"{utc()} target {len(hours)} h, todo {len(todo)}", flush=True)
    for i, h in enumerate(todo):
        check_stop()
        n = block_at_ts(h)
        rec = {"ts": h, "blk": n, **sample(hex(n))}
        with OUT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        if i % 24 == 0:
            print(f"{utc()} {i + 1}/{len(todo)} ts={h} blk={n}", flush=True)
        time.sleep(PACE)
    print(f"{utc()} done: {OUT}", flush=True)


if __name__ == "__main__":
    main()
