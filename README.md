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
| `scripts/verify.py` | Single-position verification CLI: reads any UniswapV3 NFPM tokenId on Celo from on-chain data (no keys) and emits a report-v1 JSON (`--position 202630 --hours 24`) |
| `scripts/verify_lib.py` | Integer ports of TickMath / LiquidityAmounts / feeGrowthInside / keccak256 (stdlib-only, contract-exact rounding) |
| `reports/*.md` | Published verification reports (numbers verbatim from the JSON outputs) |

## Calibration against live positions

The core idea: a model is only trusted if it reproduces what a **live** position actually claimed.

| Pool | Live calibration (model vs actually claimed) |
|---|---|
| USDC-AERO CL2000 (Base) | model 1.0806 vs **1.084257 AERO actually claimed** (6 on-chain getReward txs inside the calibration window — complete list with hashes in journals/claim_journal.md) → ratio **0.9966** |
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


## Public claim journal

`journals/claim_journal.md` — every reward claim of the live positions backing the calibration,
with tx hashes and amounts. Anyone can verify each row in a block explorer.
Corrections to published numbers are logged, never silently overwritten: `docs/ERRATA.md`.

## Agent identity (on-chain)

The harness agent is registered in two independent on-chain registries on Celo, both controlled
by the same wallet (`eip155:42220:0x3278B8911F948d0B2F83Ca931060cD253Be29F4C` — also the pilot
position wallet):

| Registry | agentId | tx |
|---|---|---|
| ERC-8004 canonical IdentityRegistry `eip155:42220:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432` | **9890** | `0xb27d257f06f8266b7af534aa151df96cec2b50647a506d2f2cae7d7e3d5d66e9` (block 79865191, 2026-10-12 00:45:49Z) |
| Self Agent ID registry `eip155:42220:0xaC3DF9ABf80d0F5c020C06B04Cced27763355944` | **193** | registered 2026-10-10 |

The ERC-8004 `agentURI` resolves to [`docs/agent.json`](docs/agent.json) (served by GitHub Pages),
which back-links both registrations.

## Data integrity

| File | sha256 (first 16) |
|---|---|
| data/celo_pool_2026-10-10.json | 15ae0939e82d3200 |
| data/weth_rerange_modes_2026-10-09.json | 3d584f2d58abe6cd |
| data/aero_rerange_modes_2026-10-10.json | af622de2a9b2b635 |

Hashes computed on LF-normalized git blobs: .
Rebuild the Celo dataset with `scripts/celo_fetch.py --from <ISO> --to <ISO>` (public archive
RPC, deterministic window) and re-run `scripts/celo_report.py` to match the hash above.

## Reproduce

```bash
# Celo pool measurement (data first, then compute)
python3 scripts/celo_fetch.py --days 30
python3 scripts/celo_report.py

# Single-position verification (any live UniswapV3 position on Celo, public RPC, no keys)
python3 scripts/verify.py --position 202630 --hours 24
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
