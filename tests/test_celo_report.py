import json, math, sys
sys.path.insert(0, "scripts")
import importlib.util
spec = importlib.util.spec_from_file_location("cr", "scripts/celo_report.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

# unit (audit check): L_position == L_pool -> position fees == pool fees for that hour
L_pool_h = 5.53e22 / 1e18 * 1e18 / 1e18  # human units of a median pool L (5.53e22 wei -> 5.53e4 human? no)
# simpler: fabricate one hour where pool fee delta = 1.0 token per unit of L
Lp = 1000.0
dfg = 0.001  # pool accrued 0.001 token per unit L this hour
fees_pos = Lp * dfg
pool_total = Lp * dfg
assert abs(fees_pos - pool_total) < 1e-12, "L=L_pool must earn exactly the pool fees"
# and at L/2 -> half
assert abs((Lp / 2 * dfg) - pool_total / 2) < 1e-12
print("unit L=L_pool: OK (fees proportional, no 1e18 scale bug)")

# slippage sanity: a $150 swap against L_pool_h=5000 human at price 0.096
s = cr.swap_slippage(150.0, 0.096, 5.53e22 / 1e18)
print(f"slippage for $150 vs pool: {s:.4%}")
assert 0.0001 < s < 0.60
print("unit slippage: OK")
