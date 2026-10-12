#!/usr/bin/env python3
"""verify.py — LP-position verification CLI (Celo mainnet, chain 42220).

Implements auditor spec verify_spec_2026-10-12.md §1.2 (method refA):

  * position discovery: NFPM Transfer(from=0, topic3=tokenId) gives B_mint and the mint tx;
    IncreaseLiquidity(tokenId) in the mint-tx receipt gives entry L (word 0), a0 (word 1),
    a1 (word 2); NFPM.positions(tokenId) at the mint block gives tickLower/tickUpper/
    token0/token1/fee; factory.getPool(token0, token1, fee) gives the pool.
  * state at block b: slot0 (sqrtPriceX96 word 0, signed tick word 1), feeGrowthGlobal0/1X128,
    ticks(tickLower/tickUpper) words 2,3 (feeGrowthOutside0/1X128), NFPM.positions(tokenId)
    word 7 = liquidity, words 8,9 = feeGrowthInsideLast0/1, words 10,11 = tokensOwed.
    f_i = owed_i + ((inside_i_now - inside_i_last) mod 2^256) * L // 2^128.
  * prices/values in exact arithmetic (int for raw amounts, Fraction for human values,
    no floats in any sum):
      P(b)      = sqrtPriceX96^2 / 2^192 * 10^d0 / 10^d1          (token1 per token0)
      V0        = a0 * P(B_mint) + a1                             (entry value)
      value_lp  = (x + f0) * P_end + (y + f1)                     (position + uncollected fees)
      hodl_mix  = a0 * P_end + a1                                 (hold entry mix)
      hodl_5050 = (V0/2) / P_start * P_end + V0/2                 (rebalanced 50/50)
  * blocks by timestamp: Celo L2 maps 1 block = 1 second; b = b_anchor + (ts_target -
    ts_anchor), verified with eth_getBlockByNumber at both window ends.

USAGE:
    python3 scripts/verify.py --position <tokenId> [--hours N | --end-block B]
    python3 scripts/verify.py --validate <report.json>

--end-block B pins the window end to exactly block B (overrides --hours); window.hours
is then reported as (ts_end - ts_mint)/3600 from the block timestamps.

Only stdlib + verify_lib (read-only RPC, whitelisted methods, retries handled there).
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_lib as vl  # noqa: E402

NFPM = "0x3d79EdAaBC0EaB6F08ED885C05Fc0B014290D95A"
FACTORY = "0xAfE208a311B21f13EF87E33A90049fC17A7acDEc"
CHAIN_ID = 42220
METHOD_VERSION = "verify.py/2.0 (spec verify_spec_2026-10-12.md §1.2: IL-event entry, NFPM positions, feeGrowthInside fees, Fraction arithmetic)"

Q128 = 1 << 128
GETLOGS_MAX_RANGE = 5000  # forno eth_getLogs limit

SEL_POSITIONS = vl.sel("positions(uint256)")          # 0x99fbab88
SEL_GETPOOL = vl.sel("getPool(address,address,uint24)")
SEL_DECIMALS = vl.sel("decimals()")
SEL_SLOT0 = vl.sel("slot0()")
SEL_TICKS = vl.sel("ticks(int24)")
SEL_FGG0 = vl.sel("feeGrowthGlobal0X128()")
SEL_FGG1 = vl.sel("feeGrowthGlobal1X128()")
TOPIC_TRANSFER = vl.topic("Transfer(address,address,uint256)")
TOPIC_IL = vl.topic("IncreaseLiquidity(uint256,uint128,uint256,uint256)")  # 0x3067048b...

# ---------------- helpers ----------------

def dec_str(fr, places=30):
    """Exact decimal string of a Fraction (floor at `places` digits)."""
    n, d = fr.numerator, fr.denominator
    sign = "-" if n < 0 else ""
    n = abs(n)
    ip, rp = divmod(n, d)
    digits = []
    for _ in range(places):
        rp *= 10
        q, rp = divmod(rp, d)
        digits.append(str(q))
        if rp == 0:
            break
    frac = "".join(digits).rstrip("0")
    return f"{sign}{ip}.{frac}" if frac else f"{sign}{ip}"


def enc32(v):
    """32-byte hex topic word WITH 0x prefix (vl.enc_int has no prefix)."""
    return "0x" + vl.enc_int(v)


def addr_of(hexword):
    h = hexword[2:] if isinstance(hexword, str) and hexword.startswith("0x") else hexword
    if isinstance(h, str):
        return "0x" + h[-40:]
    return "0x" + format(h & ((1 << 160) - 1), "040x")  # from a parsed word


def call(to, data, block):
    return vl.call(to, data, block)


def chain_assert():
    cid = int(vl.rpc("eth_chainId", []), 16)
    if cid != CHAIN_ID:
        raise SystemExit(f"rpc chain id {cid} != expected {CHAIN_ID} (Celo mainnet)")


def nfpm_positions_raw(token_id, block):
    return call(NFPM, SEL_POSITIONS + vl.enc_int(token_id), block)


def nfpm_positions_ok(token_id, block):
    try:
        r = nfpm_positions_raw(token_id, block)
        return r is not None and r != "0x"
    except Exception:
        return False  # revert: token does not exist at this block


def find_mint(token_id, head):
    """Mint tx/block from Transfer(from=0, topic3=tokenId) on the NFPM.

    A full-range getLogs is impossible (5000-block limit), so first bracket the mint
    block with a binary search on NFPM.positions(tokenId) existence, then pull the
    Transfer log from that single block. Falls back to a backward chunked getLogs
    scan if the token no longer exists (burned position).
    """
    log_topics = [TOPIC_TRANSFER, enc32(0), None, enc32(token_id)]

    def getlogs(a, b):
        p = {"address": NFPM, "fromBlock": hex(a), "toBlock": hex(b), "topics": log_topics}
        try:
            return vl.rpc("eth_getLogs", [p])
        except ValueError:
            return []  # deterministic http400: range/revert, no logs to return

    def is_mint_transfer(lg):
        t = lg.get("topics") or []
        return (lg["address"].lower() == NFPM.lower() and len(t) == 4
                and t[0] == TOPIC_TRANSFER and t[1] == enc32(0)
                and t[3] == enc32(token_id))

    blk = None
    if nfpm_positions_ok(token_id, head):
        # live token: binary-search the first block where positions(tokenId) succeeds
        lo, hi = 0, head
        while lo < hi:
            mid = (lo + hi) // 2
            if nfpm_positions_ok(token_id, mid):
                hi = mid
            else:
                lo = mid + 1
        blk = lo
    else:
        # burned token: scan backwards for the mint log in max-range chunks
        a = head
        while a > 0 and a > head - GETLOGS_MAX_RANGE * 200:
            b0 = max(0, a - GETLOGS_MAX_RANGE)
            logs = getlogs(b0, a)
            hit = [lg for lg in logs if is_mint_transfer(lg)]
            if hit:
                lg = hit[-1]
                return lg["transactionHash"], int(lg["blockNumber"], 16), lg
            a = b0 - 1
        raise SystemExit(
            f"tokenId {token_id} does not exist at head (burned?) and its mint log is not "
            f"indexed within {GETLOGS_MAX_RANGE * 200} blocks below head")

    logs = [lg for lg in getlogs(blk, blk) if is_mint_transfer(lg)]
    if not logs:
        # log index may lag the state archive: scan the block's tx receipts instead
        blkfull = vl.rpc("eth_getBlockByNumber", [hex(blk), True])
        if blkfull:
            import time as _t
            for tx in blkfull.get("transactions", []):
                rc = None
                for _ in range(3):  # nodes return null receipts transiently
                    try:
                        rc = vl.rpc("eth_getTransactionReceipt", [tx["hash"]])
                    except Exception:
                        rc = None
                    if rc:
                        break
                    _t.sleep(1)
                if not rc:
                    continue
                for lg in rc.get("logs", []):
                    if is_mint_transfer(lg):
                        return lg["transactionHash"], int(lg["blockNumber"], 16), lg
        raise SystemExit(
            f"Transfer(from=0) log for tokenId {token_id} not recoverable in block {blk} "
            f"(this RPC prunes old logs); cannot read entry amounts for this position")
    lg = logs[-1]
    return lg["transactionHash"], int(lg["blockNumber"], 16), lg


def block_ts(block):
    b = vl.rpc("eth_getBlockByNumber", [hex(block), False])
    if b is None:
        raise SystemExit(f"block {block} not returned by rpc")
    return int(b["timestamp"], 16)


def block_for_ts(b_anchor, ts_anchor, ts_target, head):
    """b = b_anchor + (ts_target - ts_anchor); verify with block reads and correct."""
    b = b_anchor + (ts_target - ts_anchor)
    if b > head:
        raise SystemExit(f"target block {b} is past head {head}; reduce --hours")
    for _ in range(5):
        ts = block_ts(b)  # verification call
        if ts == ts_target:
            return b, ts
        b += ts_target - ts
    raise SystemExit(f"1 block = 1 second mapping failed around block {b}")


def pool_state_raw(pool, tick_lower, tick_upper, block):
    out = {}
    out["slot0"] = call(pool, SEL_SLOT0, block)
    out["fgg0"] = call(pool, SEL_FGG0, block)
    out["fgg1"] = call(pool, SEL_FGG1, block)
    out["ticksLower"] = call(pool, SEL_TICKS + vl.enc_int(tick_lower), block)
    out["ticksUpper"] = call(pool, SEL_TICKS + vl.enc_int(tick_upper), block)
    return out


def parse_state(raw):
    s0 = vl.words(raw["slot0"])
    tl = vl.words(raw["ticksLower"])
    tu = vl.words(raw["ticksUpper"])
    return {
        "sqrtPriceX96": s0[0],
        "tick": vl.s(s0[1], 24),
        "fgg0": int(raw["fgg0"], 16),
        "fgg1": int(raw["fgg1"], 16),
        "fgOutsideLower": (tl[2], tl[3]),
        "fgOutsideUpper": (tu[2], tu[3]),
    }


def price_human(sqrt_p_x96, d0, d1):
    """P = sqrtPriceX96^2 / 2^192 * 10^d0 / 10^d1 — token1 per token0 (exact)."""
    return Fraction(sqrt_p_x96 * sqrt_p_x96, 1 << 192) * Fraction(10 ** d0, 10 ** d1)


def verify_position(token_id, hours, end_block=None):
    chain_assert()
    head = int(vl.rpc("eth_blockNumber", []), 16)

    # ---- 1) position discovery -------------------------------------------
    mint_tx, b_mint, mint_log = find_mint(token_id, head)
    rc = vl.rpc("eth_getTransactionReceipt", [mint_tx])
    if rc is None or rc.get("status") != "0x1":
        raise SystemExit(f"mint tx {mint_tx} receipt missing or failed")
    il = None
    for lg in rc.get("logs", []):
        t = lg.get("topics") or []
        if len(t) >= 2 and t[0] == TOPIC_IL and int(t[1], 16) == token_id:
            il = lg
            break
    if il is None:
        raise SystemExit(f"IncreaseLiquidity(tokenId={token_id}) not in mint tx {mint_tx}")
    w = vl.words(il["data"])
    L_entry, a0_raw, a1_raw = w[0], w[1], w[2]  # L = word 0 (not word 1!)

    pos_hex = nfpm_positions_raw(token_id, b_mint)
    pw = vl.words(pos_hex)
    token0 = addr_of(pw[2])
    token1 = addr_of(pw[3])
    fee = pw[4]
    tick_lower = vl.s(pw[5], 24)
    tick_upper = vl.s(pw[6], 24)
    L_mint = pw[7]

    pool = addr_of(call(FACTORY, SEL_GETPOOL + vl.enc_addr(token0) + vl.enc_addr(token1) + vl.enc_int(fee), b_mint))
    if int(pool[2:], 16) == 0:
        raise SystemExit("factory.getPool returned zero address")
    d0 = int(call(token0, SEL_DECIMALS, b_mint), 16)
    d1 = int(call(token1, SEL_DECIMALS, b_mint), 16)

    # ---- 2) window: [B_mint, B_end] — pinned --end-block, or B_mint + hours*3600 by 1 block = 1 second ----
    ts_mint = block_ts(b_mint)
    if end_block is not None:
        # explicit end block: use the block number directly, no timestamp re-derivation
        if end_block <= b_mint:
            raise SystemExit(f"--end-block {end_block} must be > mint block {b_mint} for position {token_id}")
        if end_block > head:
            raise SystemExit(f"--end-block {end_block} is past chain head {head}")
        b_end = end_block
        ts_end = block_ts(b_end)
    else:
        ts_end_target = ts_mint + hours * 3600
        b_end, ts_end = block_for_ts(b_mint, ts_mint, ts_end_target, head)

    # ---- 3) state at both blocks ------------------------------------------
    raw_m = pool_state_raw(pool, tick_lower, tick_upper, b_mint)
    raw_e = pool_state_raw(pool, tick_lower, tick_upper, b_end)
    st_m, st_e = parse_state(raw_m), parse_state(raw_e)
    pos_end_hex = nfpm_positions_raw(token_id, b_end)
    pe_m = vl.words(pos_end_hex)
    L_end, fil0, fil1, ow0, ow1 = pe_m[7], pe_m[8], pe_m[9], pe_m[10], pe_m[11]

    def fees_and_amounts(st, L, fil0, fil1, ow0, ow1):
        inside0 = vl.fee_growth_inside(st["tick"], tick_lower, tick_upper, st["fgg0"],
                                       st["fgOutsideLower"][0], st["fgOutsideUpper"][0])
        inside1 = vl.fee_growth_inside(st["tick"], tick_lower, tick_upper, st["fgg1"],
                                       st["fgOutsideLower"][1], st["fgOutsideUpper"][1])
        f0 = ow0 + ((inside0 - fil0) % vl.M256) * L // Q128
        f1 = ow1 + ((inside1 - fil1) % vl.M256) * L // Q128
        sa = vl.get_sqrt_ratio_at_tick(tick_lower)
        sb = vl.get_sqrt_ratio_at_tick(tick_upper)
        x, y = vl.amounts_for_liquidity(st["sqrtPriceX96"], sa, sb, L)
        return f0, f1, x, y, inside0, inside1

    f0, f1, x, y, inside0_end, inside1_end = fees_and_amounts(st_e, L_end, fil0, fil1, ow0, ow1)

    # ---- 4) prices and values (Fractions; ints for raw amounts) -----------
    P_start = price_human(st_m["sqrtPriceX96"], d0, d1)
    P_end = price_human(st_e["sqrtPriceX96"], d0, d1)
    e0 = Fraction(a0_raw, 10 ** d0)
    e1 = Fraction(a1_raw, 10 ** d1)
    V0 = e0 * P_start + e1
    value_lp = Fraction(x + f0, 10 ** d0) * P_end + Fraction(y + f1, 10 ** d1)
    value_hodl_entry_mix = e0 * P_end + e1
    value_hodl_5050 = (V0 / 2) / P_start * P_end + V0 / 2

    pct = lambda a, b: (a / b - 1) * 100
    ret_lp_vs_entry_pct = pct(value_lp, V0)
    lp_vs_hodl_pct = pct(value_lp, value_hodl_entry_mix)
    lp_vs_5050_pct = pct(value_lp, value_hodl_5050)

    # ---- 5) report --------------------------------------------------------
    raw = {
        "chain_id": f"{CHAIN_ID}",
        "mint_log": mint_log,
        "increase_liquidity_log": il,
        "nfpm_positions": {str(b_mint): pos_hex, str(b_end): pos_end_hex},
        "pool": {k: {str(b_mint): raw_m[k], str(b_end): raw_e[k]} for k in raw_m},
        "decimals": {token0: d0, token1: d1},
    }
    data_hash = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).isoformat()
    r6 = lambda fr: round(float(fr), 6)
    r12 = lambda fr: round(float(fr), 12)
    caveat_extra = []
    if L_end != L_entry:
        caveat_extra.append(
            f"position liquidity changed in the window (mint IL L={L_entry}, end L={L_end}); "
            "value_lp uses end-of-window liquidity and fee state")

    report = {
        "format_version": 1,
        "chain_id": CHAIN_ID,
        "pool": pool,
        "position": {
            "id": token_id,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "liquidity": str(L_end),
            "liquidity_entry": str(L_entry),
            "token0": token0,
            "token1": token1,
            "token0_decimals": d0,
            "token1_decimals": d1,
            "fee": fee,
        },
        "window": {"from": iso(ts_mint), "to": iso(ts_end), "hours": (round((ts_end - ts_mint) / 3600, 4) if end_block is not None else hours)},
        "block_range": [b_mint, b_end],
        "mint_tx": {
            "hash": mint_tx,
            "block": b_mint,
            "timestamp": ts_mint,
            "increase_liquidity": {
                "liquidity": L_entry, "amount0": a0_raw, "amount1": a1_raw,
                "amount0_human": dec_str(e0), "amount1_human": dec_str(e1),
            },
        },
        "data_hash": data_hash,
        "method_version": METHOD_VERSION,
        "n": 1,
        "n_eff": 1.0,
        "benchmarks": {
            "hodl_5050_vs_quote": {"median": r6(lp_vs_5050_pct), "p10": r6(lp_vs_5050_pct), "p90": r6(lp_vs_5050_pct)},
            "hodl_entry_mix_vs_quote": {"median": r6(lp_vs_hodl_pct), "p10": r6(lp_vs_hodl_pct), "p90": r6(lp_vs_hodl_pct)},
        },
        "modes": [{
            "name": "hold_as_is",
            "median": r6(ret_lp_vs_entry_pct),
            "p10": r6(ret_lp_vs_entry_pct),
            "p90": r6(ret_lp_vs_entry_pct),
        }],
        "metrics": {
            "quote_token": token1,
            "V0": {"value": r12(V0), "exact": dec_str(V0)},
            "value_lp": {"value": r12(value_lp), "exact": dec_str(value_lp)},
            "value_hodl_entry_mix": {"value": r12(value_hodl_entry_mix), "exact": dec_str(value_hodl_entry_mix)},
            "value_hodl_5050": {"value": r12(value_hodl_5050), "exact": dec_str(value_hodl_5050)},
            "ret_lp_vs_entry_pct": {"value": r12(ret_lp_vs_entry_pct), "exact": dec_str(ret_lp_vs_entry_pct)},
            "lp_vs_hodl_pct": {"value": r12(lp_vs_hodl_pct), "exact": dec_str(lp_vs_hodl_pct)},
            "lp_vs_5050_pct": {"value": r12(lp_vs_5050_pct), "exact": dec_str(lp_vs_5050_pct)},
            "price": {
                "P_start": {"value": r12(P_start), "exact": dec_str(P_start), "tick": st_m["tick"], "sqrtPriceX96": st_m["sqrtPriceX96"]},
                "P_end": {"value": r12(P_end), "exact": dec_str(P_end), "tick": st_e["tick"], "sqrtPriceX96": st_e["sqrtPriceX96"]},
            },
            "fees_uncollected": {
                "f0_raw": f0, "f1_raw": f1,
                "f0": r12(Fraction(f0, 10 ** d0)), "f1": r12(Fraction(f1, 10 ** d1)),
                "inside0_end": inside0_end, "inside1_end": inside1_end,
                "feeGrowthInsideLast0": fil0, "feeGrowthInsideLast1": fil1,
                "tokensOwed0": ow0, "tokensOwed1": ow1,
            },
            "principal_end": {
                "x_raw": x, "y_raw": y,
                "x": r12(Fraction(x, 10 ** d0)), "y": r12(Fraction(y, 10 ** d1)),
            },
        },
        "calibration": None,
        "caveats": [
            "single-window deterministic replay (n=1, n_eff=1); p10/p90 mirror the point value",
            "fees measured at end block: f_i = tokensOwed_i + (inside_i_now - inside_i_last) mod 2^256 * L // 2^128; not compounded",
            "value_lp holds the position as-is from entry liquidity; no burns/increases replayed apart from end-block state" ,
            "quote unit is token1 (human units) via P = sqrtPriceX96^2/2^192 * 10^d0/10^d1",
            "block mapping assumes Celo L2 1 block = 1 second, verified against block timestamps at both window ends",
        ] + caveat_extra,
    }
    return report


# ---------------- minimal report-v1 schema validation (stdlib only) ----------------

def _type_ok(inst, t):
    if t == "object":
        return isinstance(inst, dict)
    if t == "array":
        return isinstance(inst, list)
    if t == "string":
        return isinstance(inst, str)
    if t == "integer":
        return isinstance(inst, int) and not isinstance(inst, bool)
    if t == "number":
        return isinstance(inst, (int, float)) and not isinstance(inst, bool)
    if t == "boolean":
        return isinstance(inst, bool)
    if t == "null":
        return inst is None
    return True


def _validate(inst, sch, path, errs):
    t = sch.get("type")
    if isinstance(t, list):
        if not any(_type_ok(inst, x) for x in t):
            errs.append(f"{path}: type {type(inst).__name__} not in {t}")
            return
    elif isinstance(t, str) and not _type_ok(inst, t):
        errs.append(f"{path}: expected {t}, got {type(inst).__name__}")
        return
    if "const" in sch and inst != sch["const"]:
        errs.append(f"{path}: {inst!r} != const {sch['const']!r}")
    if "pattern" in sch and isinstance(inst, str) and not re.search(sch["pattern"], inst):
        errs.append(f"{path}: {inst!r} does not match {sch['pattern']}")
    if "minimum" in sch and isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if inst < sch["minimum"]:
            errs.append(f"{path}: {inst} < minimum {sch['minimum']}")
    if isinstance(inst, dict):
        for k in sch.get("required", []):
            if k not in inst:
                errs.append(f"{path}: missing required '{k}'")
        for k, sub in sch.get("properties", {}).items():
            if k in inst:
                _validate(inst[k], sub, f"{path}.{k}", errs)
    if isinstance(inst, list):
        if "minItems" in sch and len(inst) < sch["minItems"]:
            errs.append(f"{path}: {len(inst)} items < minItems {sch['minItems']}")
        if "maxItems" in sch and len(inst) > sch["maxItems"]:
            errs.append(f"{path}: {len(inst)} items > maxItems {sch['maxItems']}")
        if "items" in sch:
            for i, item in enumerate(inst):
                _validate(item, sch["items"], f"{path}[{i}]", errs)


def validate_file(path):
    here = os.path.dirname(os.path.abspath(__file__))
    schema_path = os.path.join(here, "report.schema.json")
    schema = json.load(open(schema_path))
    report = json.load(open(path))
    errs = []
    _validate(report, schema, "$", errs)
    if errs:
        for e in errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(f"OK: {path} matches report-v1")
    return 0


def main():
    ap = argparse.ArgumentParser(description="LP position verification (Celo, spec §1.2)")
    ap.add_argument("--position", type=int, help="UniswapV3 NFPM tokenId on Celo")
    ap.add_argument("--hours", type=int, default=24, help="window length in hours (default 24)")
    ap.add_argument("--end-block", type=int, default=None,
                    help="pin the window end to exactly this block (overrides --hours)")
    ap.add_argument("--validate", help="validate a report JSON against scripts/report.schema.json")
    args = ap.parse_args()

    if args.validate:
        return validate_file(args.validate)
    if not args.position:
        ap.error("--position <tokenId> required (or --validate <file>)")
    if args.end_block is None and args.hours < 1:
        ap.error("--hours must be >= 1")

    report = verify_position(args.position, args.hours, end_block=args.end_block)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
