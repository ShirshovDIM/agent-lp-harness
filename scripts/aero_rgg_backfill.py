#!/usr/bin/env python3
"""Backfill rewardGrowthGlobalX128 of the USDC-AERO CL2000 pool at every pool-sample block.

Reads data/pool_samples/samples_*.jsonl (blk, ts), skips blocks already in aero_rgg.jsonl, appends
{"blk","ts","rgg"} lines to aero_rgg.jsonl (same format as the 05.10 backfill). Read-only RPC
(eth_call at historical blocks), pacing/backoff from the fetch helper Rpc. Writes <out>.done when finished.
USAGE: python3 aero_rgg_backfill.py data/pool_samples
"""
import glob
import json
import sys
from pathlib import Path

from pool_samples_fetch import AERO_POOL, RGG, Rpc


def main(argv):
    data = Path(argv[1])
    out = data / "aero_rgg.jsonl"
    have = set()
    if out.exists():
        for line in out.open(errors="replace"):
            if line.strip():
                have.add(json.loads(line)["blk"])
    todo = []
    for f in sorted(glob.glob(str(data / "samples_*.jsonl"))):
        for line in open(f, errors="replace"):
            if line.strip():
                r = json.loads(line)
                if r["blk"] not in have:
                    todo.append((r["blk"], r["ts"]))
    todo = sorted(set(todo))
    print(f"todo {len(todo)}", flush=True)
    rpc = Rpc()
    with out.open("a") as f:
        for blk, ts in todo:
            rg = rpc.call("eth_call", [{"to": AERO_POOL, "data": RGG}, hex(blk)])
            if rg and rg != "0x":
                f.write(json.dumps({"blk": blk, "ts": ts, "rgg": rg[2:]}) + "\n")
                f.flush()
    Path(str(out) + ".done").write_text(str(len(todo)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
