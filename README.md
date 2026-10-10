# Agent LP with Verifiable Yield — measurement harness

Autonomous concentrated-liquidity (CL) agent + a deterministic replay harness that makes the
agent's numbers **verifiable**: any claimed LP strategy result can be re-run on collected on-chain
data and checked against rewards **actually claimed by a live position**.

The harness is pure Python (no LLM in the measurement loop), seeded and reproducible: one command,
seed 113, 113 random entry points, 14/30-day horizons.

## What is here

| Path | What it does |
|---|---|
| `scripts/weth_rerange_modes.py` | WETH-USDC (Aerodrome CL50): re-range policy modes replay — swap vs no-swap vs small-swap, hold vs re-range |
| `scripts/aero_rerange_modes.py` | USDC-AERO (Aerodrome CL2000): the same comparison on a coarse tick grid |
| `scripts/aero_rgg_backfill.py` | Backfill of rewardGrowthGlobal for the AERO pool |
| `scripts/celo_fetch.py` | Hourly collector for UniswapV3 pools on Celo mainnet (forno archive RPC): {sqrtPriceX96, tick, liquidity, feeGrowthGlobal0/1} |
| `scripts/celo_report.py` | Verification report compute for a Celo pool (widths ±2–30%, hold vs re-range, 113 seeded starts) |
| `reports/*.md` | Published verification reports (numbers verbatim from the JSON outputs) |

## Calibration against live positions

The core idea: a model is only trusted if it reproduces what a **live** position actually claimed.

| Pool | Live calibration (model vs actually claimed) |
|---|---|
| USDC-AERO CL2000 (Base) | model 1.0806 vs **1.084258 AERO actually claimed** (on-chain ClaimRewards, 4-day window) → ratio **0.9966** |
| WETH-USDC CL50 (Base) | calculation pending publication (live journal data) |
| CELO/USDm fee=100 (Celo) | live pilot running since 2026-10-10; calibration block fills from its journal (≥48h) |

## Verification report format (spec)

Every report carries, verbatim from a JSON output:

1. **Window**: data hours, price first/last/min/max, regime caveat.
2. **Method**: position size, entry, widths, costs (swap fee incl. a stress variant, gas per re-range).
3. **Statistics**: medians with p10/p90, tail probabilities P(< −5%) / P(< −10%), 113 seeded starts.
4. **Calibration**: model-predicted rewards vs actually claimed rewards of a live position (or an
   explicit "no live position yet" note).
5. **Caveats**: overlapping windows, market regime, simplifications — stated, not hidden.

Any agent can emit a compatible report; the format is designed to be comparable across agents.

## Reproduce

```bash
# Celo pool measurement (data first, then compute)
python3 scripts/celo_fetch.py --days 30
python3 scripts/celo_report.py
```

Deterministic: fixed seed (113), no network in the compute step, no LLM anywhere in the loop.

## License

MIT.


## Data and scope

- `data/*.json` are the full JSON outputs of the three published measurements (reproducible artifacts).
- The Base measurement scripts read hourly pool snapshots from a local collector store
  (not part of this repo); the Celo collector (`scripts/celo_fetch.py`) rebuilds its dataset
  from any public Celo archive RPC — run it to reproduce `data/celo_pool_*.json` inputs.
- The live agent runtime (position engine, keys, execution) is private and is NOT part of this repo;
  this repo is the verification harness: replay + calibration against the public per-claim journals.
