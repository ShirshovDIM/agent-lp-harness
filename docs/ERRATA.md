# ERRATA — Corrections to published artifacts

This page documents corrections to published reports, calibration numbers, and computed results — with the reason, the introducing/fixing commit, and the discovering party. Corrections are not hidden: they demonstrate the verifier working as intended (data corrections from on-chain evidence, not opinions). Scope: this repository only.

| Date | What changed | Why | Commit | Found by |
|---|---|---|---|---|
| 2026-10-10 | Celo report: pool liquidity "≈1.05e21" → full-window range 5.5e21–6.1e22 + median | A single eth_call at selection time is not a pool characteristic | 0015c83 | external audit |
| 2026-10-10 | Gas per re-range: $0.001 → $0.014 (range $0.01–0.018) | Measured from actual NFPM transactions instead of an assumption | 0c23770 | author |
| 2026-10-10 | Calibration ratio: 1.0066 → 0.9966 | Initially 5 claims counted from an internal journal; on-chain getLogs showed 6 claims (an early claim was missed by the probe) | 0c23770 | external audit |
| 2026-10-11 | Calibration ratio: 0.9966 → 1.0061 (wrong, reverted same day) | An internal journal accepted as complete (5 claims); on-chain remains canonical | 2192f2d | author |
| 2026-10-11 | Calibration ratio: 1.0061 → **0.9966** (final) | 6th claim verified on-chain (tx 0x841c677c, 0.010272 AERO, block-verified). Internal journals are not canon; on-chain getLogs is the only canon for on-chain sums | ea97416 | external audit |
| 2026-10-11 | Journal row 0 added (initially with wrong time 12:33:03Z) | The first claim (2026-10-05 12:20:15Z) was missing entirely — the probe started after the mint | ea97416 | external audit |
| 2026-10-11 | aero script KeyError 'range'; claimed total 1.084258 → 1.084257 | Anchor privatization left a dangling key use; on-chain recount of the 6 claims | 1421f6c | external audit |
| 2026-10-12 | Journal row 0 time: 12:33:03Z → 12:20:15Z | Block time of tx 0x841c677c; the first fix diff edited a different line | b38961b | external audit |
| 2026-10-12 | Journal rows 1–6 times: −2 s each | Block times vs journal times (12:22:11 → 12:22:13Z etc.) | 6a15439 | external audit |
| 2026-10-12 | aero script NameError 'lo' | Introduced by the KeyError 'range' fix (1421f6c) — lo/hi uses remained; fixed with explicit constants | e8ab2ed | external audit |
| 2026-10-12 | aero script KeyError 'L' | Anchor returns 'withheld' without runtime args | a15142f | external audit |
| 2026-10-12 | Identity: agent.json registrations — canonical ERC-8004 agentId 9890 (IdentityRegistry eip155:42220:0x8004A169…a432) alongside Self Agent ID 193 (a different registry), same controlling wallet | 193 alone was ambiguous; the canonical registry pair is the verifiable identity | 8184021 | external audit |
| 2026-10-12 | Slippage formula fixed: **k/(1+k) on the swapped volume V=φ·W** | Audit spec §2: wrong price power (P^1.5 vs P^0.5, ×10.2 at BREF), spurious /2, linear saturation, and cost applied to full W instead of V. Cross-check: bit-exact Pool.swap port (scripts/v3math.py) matches QuoterV2 on the fixture's 4 no-tick-crossing quotes; k/(1+k) matches the spec table (BREF $150 → 4.0045%) | e19f321 | external audit |
| 2026-10-12 | Celo report rerange medians recomputed (slippage fix + window 737→782h): HODL 50/50 +8.15% → +6.78%, CELO window rise +28.1% → +24.7%; rerange medians ±10% −33.70%→−18.36%, ±15% −19.70%→−1.76%, ±30% −0.06%→+2.82% — **being recomputed again**: the L_eff correction (next row) shifts them | Formula correction; the second pass fixes a defect the first pass introduced | e19f321 | external audit |
| 2026-10-12 | celo_report L_eff: `L_pool − L_own` → `L_pool` (recomputation in progress) | The replayed position is hypothetical: it never existed in the hourly `liquidity()` samples, and at a re-range it is out of range (not part of active L). Subtracting it fired the 60% cap 338× and distorted medians (external audit review of ec171fc; spec §2 clarified) | pending | external audit |
| 2026-10-12 | README data hashes: computed on LF-normalized git blobs; hash command documented | CRLF-affected hashes failed verification on Linux | 211f9f3 | external audit |

## Principle

Corrections stay visible with their reasons and commits. Where a correction was itself wrong, the follow-up correction is recorded in the same table — the trail is the evidence.
