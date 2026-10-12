# ERRATA — Corrections to published artifacts

This page documents every correction made to published reports, calibration numbers, and computed results — with the reason and the discovering party. Corrections are not hidden: they demonstrate the verifier working as intended (data corrections from on-chain evidence, not opinions).

| Date | What changed | Why | Commit | Found by |
|---|---|---|---|---|
| 2026-10-10 | Calibration ratio: 1.0066 → 0.9966 | Initially 5 claims counted from journal; on-chain getLogs showed 6 claims (early claim missed by probe) | 2192f2d | author |
| 2026-10-11 | Calibration ratio: 0.9966 → 1.0061 | Accepted an internal journal as complete (5 claims); this was wrong — on-chain remains canonical | (reverted) | author |
| 2026-10-11 | Calibration ratio: 1.0061 → **0.9966** (final) | 6th claim verified on-chain (tx 0x841c677c, 0.010272 AERO, block-verified). **Internal journals are not canon; on-chain getLogs is the only canon for on-chain sums** | ea97416 | External auditor |
| 2026-10-11 | Slippage formula: P^1.5 → k/(1+k) pending | Original heuristic didn't match pool curve; auditor proposed k/(1+k) per pool L | pending | External auditor |
| 2026-10-10 | Journal line-0 added | First claim (05.10 12:20:15Z) was missing from the pilot journal (probe started after mint). Added with attribution | b38961b0 | External auditor |
| 2026-10-10 | aero_rerange KeyError 'lo' | Positional tick-bounds variable deleted during anchor privatization but usage remained | e8ab2ed | author |
| 2026-10-12 | ERC-8004: agent #193 → #9890 | #193 in Self registry (non-canonical); #9890 in canonical IdentityRegistry 0x8004A169 | 8184021 | External auditor |
| 2026-10-11 | Celo report: liquidity "≈1.05e21" → range 5.5e21–6.1e22 | Single eth_call at selection time ≠ pool characteristic; replaced with full-window range + median | 0015c83 | author |

## Principle

> Every number in this project is either: (a) from an on-chain primary source, or (b) from a deterministic script reproducible from the repo. When (a) and (b) disagree, (a) wins and the correction is recorded here. This page is itself a demonstration of the verifier's core claim: **trust is earned by showing corrections, not by hiding them.**
