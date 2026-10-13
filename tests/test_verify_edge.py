"""tests/test_verify_edge.py — edge matrix for verify.py window events + burn cut (spec §1.2/§2).

Network tests against Celo mainnet via forno.celo.org (read-only RPC). The whole suite runs
the auditor edge matrix as verify.py CLI subprocesses in parallel and must finish in <= 3 min.
Stdlib unittest only (this box has no PyPI access); run with:

    python3 -m unittest discover -s tests -v        # or: python3 tests/test_verify_edge.py

Pinned on-chain facts (Celo mainnet, NFPM 0x3d79EdAaBC0EaB6F08ED885C05Fc0B014290D95A):
  202600: mint 79575397 (tiny entry L), IncreaseLiquidity top-up 79575896, close+burn 79606178
  202623: mint 79686658, single Collect 79784409 (amounts 2302620672 / 2326), L unchanged
  202630: mint 79742362, clean 24h window (regression anchor, no window events)
  202636: mint 79775390, DecreaseLiquidity+Collect 79776244 (closed, not burned)
  202637: mint 79776311, DecreaseLiquidity+Collect 79859208 (closed, not burned)
  202638: mint 79801839, DecreaseLiquidity+Collect+burn all in block 79817003
"""
import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VERIFY = os.path.join(ROOT, "scripts", "verify.py")

CASE_TIMEOUT = 170  # per-case seconds; the 6 cases run in parallel so the suite stays < 3 min

CASES = [  # (position, hours)
    (202600, 8),
    (202600, 24),
    (202636, 28),
    (202637, 28),
    (202623, 28),
    (202638, 24),
]


def run_case(pos, hours):
    return subprocess.run(
        [sys.executable, VERIFY, "--position", str(pos), "--hours", str(hours)],
        capture_output=True, text=True, timeout=CASE_TIMEOUT,
    )


class EdgeMatrix(unittest.TestCase):
    """Auditor edge matrix: 202600/8 flag; 202600/24 clamp; 202636+202637/28 flag;
    202623/28 no flag with collected fees; 202638/24 clamp."""

    REPORTS = None

    @classmethod
    def setUpClass(cls):
        # run the whole matrix once, in parallel (forno is read-only and handles this fine)
        with ThreadPoolExecutor(max_workers=len(CASES)) as ex:
            procs = {key: ex.submit(run_case, *key) for key in CASES}
            cls.REPORTS = {}
            for key, fut in procs.items():
                p = fut.result()
                assert p.returncode == 0, (
                                f"verify.py --position {key[0]} --hours {key[1]} "
                                f"exited {p.returncode}:\n{p.stderr[-2000:]}")
                assert p.stdout.strip(), "empty report on stdout"
                cls.REPORTS[key] = json.loads(p.stdout)

    def mode(self, key):
        rep = self.REPORTS[key]
        self.assertEqual(len(rep["modes"]), 1)
        return rep["modes"][0]

    # 202600 --hours 8: IncreaseLiquidity top-up inside the window -> flag path
    def test_202600_h8_flag(self):
        rep = self.REPORTS[(202600, 8)]
        self.assertIs(rep["position_modified_in_window"], True)
        self.assertIs(rep["window_clamped_to_burn"], False)
        m = self.mode((202600, 8))
        self.assertIsNone(m["median"])
        self.assertIsNone(m["p10"])
        self.assertIsNone(m["p90"])
        self.assertEqual(m["reason"],
                         "position modified in window: 1 events; "
                         "segment replay not in this version")
        self.assertEqual(rep["block_range"], [79575397, 79575397 + 8 * 3600])  # ends before burn

    # 202600 --hours 24: burned mid-window -> clamp (top-up at +0.14h still flags the window)
    def test_202600_h24_clamped_to_burn(self):
        rep = self.REPORTS[(202600, 24)]
        self.assertIs(rep["window_clamped_to_burn"], True)
        self.assertEqual(rep["burn_block"], 79606178)
        self.assertIsNotNone(rep["burn_ts"])
        self.assertEqual(rep["block_range"], [79575397, 79606177])  # last block before burn
        self.assertTrue(any("burned during the window" in c for c in rep["caveats"]))
        self.assertIs(rep["position_modified_in_window"], True)  # top-up inside clamped window
        self.assertIsNone(self.mode((202600, 24))["median"])

    # 202636 --hours 28: closed by DecreaseLiquidity inside the window -> flag path
    def test_202636_h28_flag(self):
        rep = self.REPORTS[(202636, 28)]
        self.assertIs(rep["position_modified_in_window"], True)
        self.assertIs(rep["window_clamped_to_burn"], False)  # closed but never burned
        m = self.mode((202636, 28))
        self.assertIsNone(m["median"])
        self.assertTrue(m["reason"].startswith("position modified in window: 1 events;"))
        self.assertEqual(rep["block_range"], [79775390, 79775390 + 28 * 3600])

    # 202637 --hours 28: closed by DecreaseLiquidity inside the window -> flag path
    def test_202637_h28_flag(self):
        rep = self.REPORTS[(202637, 28)]
        self.assertIs(rep["position_modified_in_window"], True)
        self.assertIs(rep["window_clamped_to_burn"], False)
        m = self.mode((202637, 28))
        self.assertIsNone(m["median"])
        self.assertTrue(m["reason"].startswith("position modified in window: 1 events;"))
        self.assertEqual(rep["block_range"], [79776311, 79776311 + 28 * 3600])

    # 202623 --hours 28: Collect-only, L unchanged -> no flag, collected fees counted as earned
    def test_202623_h28_collect_applied(self):
        rep = self.REPORTS[(202623, 28)]
        self.assertIs(rep["position_modified_in_window"], False)
        self.assertIs(rep["window_clamped_to_burn"], False)
        m = self.mode((202623, 28))
        self.assertIsInstance(m["median"], (int, float))  # measurable: no flag
        # auditor expectation: ~ +0.00379 fraction (+/- 0.0005), not -0.00188
        self.assertLessEqual(abs(m["median"] / 100 - 0.00379), 0.0005, msg=str(m["median"]))
        fc = rep["metrics"]["fees_collected"]
        self.assertEqual(fc["events"], 1)
        self.assertEqual(fc["amount0_raw"], 8994612)  # chain-verified: Collect topic0=Collect(uint256,address,uint256,uint256) blk 79784409
        self.assertEqual(fc["amount1_raw"], 2326)
        self.assertTrue(any("Collect event(s) applied" in c for c in rep["caveats"]))

    # 202638 --hours 24: burned mid-window -> clean clamp to the last existing block
    def test_202638_h24_clamped_to_burn(self):
        rep = self.REPORTS[(202638, 24)]
        self.assertIs(rep["window_clamped_to_burn"], True)
        self.assertEqual(rep["burn_block"], 79817003)
        self.assertIsNotNone(rep["burn_ts"])
        self.assertEqual(rep["block_range"], [79801839, 79817002])
        # close (DL+Collect+burn) happened in the burn block itself: clamped window is clean
        self.assertIs(rep["position_modified_in_window"], False)
        self.assertIsInstance(self.mode((202638, 24))["median"], (int, float))


if __name__ == "__main__":
    unittest.main(verbosity=2)
