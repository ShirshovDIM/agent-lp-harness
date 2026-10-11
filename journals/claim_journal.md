# Public claim journal — live LP positions (Base + Celo)

Every reward claim of the live positions backing this project's calibration, from on-chain
transactions. Anyone can verify each row in a block explorer. Amounts are the token amounts
transferred by the claim transaction.

## Position: USDC-AERO CL2000, Base (Aerodrome Slipstream)

- pool `0xBE00fF35AF70E8415D0eB605A286D8A45466A4c1`, gauge `0x430C09546ae9249AB75B9A4ef7B5FD9a4006D6f3`
- tokenId **77587480**
- deposit 2026-10-05 11:55:12Z `0x4750ce064022531ba4d215edc61fc13ef858cb17781f8d304028ad09963c88e2`
- redeposit (reinvest) 2026-10-09 23:48:18Z `0x8c2cb73f6df1a5544f4a92d4c3224647bfb8561c6f152fc783ccabfb9ee50743`
- withdraw (reinvest, pays accrued) 2026-10-09 23:47:53Z `0xa35d67dbdf7b1044950b5588c1d8b3810d9c91dd960b35d14b26bdb12b3ceebe`

### getReward claims (tokenId 77587480)

| # | time (UTC) | tx | AERO claimed |
|---|---|---|---|
| 0 | 2026-10-05 12:33:03Z | `0x841c677cdc204b90346ebbd0968ab3dbc9962a22f63676d4a990d0f0fcef84f7` | 0.010272 |
| 1 | 2026-10-05 12:22:11Z | `0xb51cb4df7472c4c09864b811283a5818ee38dd3063f6a32f50ddceb75aabbf9b` | 0.000611 |
| 2 | 2026-10-06 12:22:11Z | `0x4f2ab64d9f0d5ce4d81914549f7d77fa87199e3126ad4aab65ca1009bc507fdb` | 0.309624 |
| 3 | 2026-10-07 12:22:11Z | `0xf4f9eb3981e261e54de39384cb9094cf751b2e61a83fa696c87bb19992bccdb3` | 0.233139 |
| 4 | 2026-10-08 12:22:11Z | `0xa5b96fc065d578c064843b42311e2b9262561c3ce0efa05973e33c6606b5c057` | 0.193454 |
| 5 | 2026-10-09 12:22:11Z | `0x6e43614cce4ac5484eaa501502c84edd03dc77a5b69ea54a4ac7c58ae9dde92a` | 0.337156 |
| 6 | 2026-10-10 12:22:11Z | `0x0707f9e9ec0915d6e4a639ae760a3e27b98a70e8add67328dad07fffc4689815` | 0.195427 |

### Calibration window 2026-10-05 11:55Z → 2026-10-09 12:22Z

Claims **within the window: rows 0–5, total 1.084257 AERO**. The replay model over the same
window predicts **1.0806 AERO** → ratio **0.9966**. (Row 0 was initially missed by our internal
probe journal and caught by an external audit — the journal below is the complete on-chain truth.) (Row 6 and the reinvest withdraw are outside
the window; an external recount that includes different boundaries is expected to differ — the
window edges are what matters, and they are the deposit/claim timestamps above.)

## Position: CELO/USDm fee=100, Celo mainnet (UniswapV3)

- pool `0x2d70cbabf4d8e61d5317b62cbe912935fd94e0fe`, NFPM tokenId **202630**, owner = the Celo wallet of the agent identity (see README); the Base positions above are operated by a different wallet
- opened 2026-10-10 14:38Z: swap `0x688969def898770852902062a459dc891f562065aed2a84191cae3c33b857df7`,
  mint `0x18c259ce6f17823f1b721068bd934e120c98261c75b0ea9d5b8f8918d29b9c91`
- fee collects: none yet (position live; this journal updates with each Collect transaction)
- calibration: fills once ≥48h of the position's hourly journal exists (model fees vs collected)

## WETH-USDC CL50, Base

- operated by a different wallet than the agent identity wallet; per-claim journal rows will be appended here from the same on-chain source
  (getReward transactions of the position's tokenId on its gauge) — pending compilation.
