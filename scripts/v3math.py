"""Exact integer port of UniswapV3 core math (TickMath, SqrtPriceMath, SwapMath, TickBitmap,
UniswapV3Pool.swap loop) for exact-input swaps. Python ints emulate uint256 (overflow checks
mirror the Solidity branches)."""
Q96 = 1 << 96
U256 = 1 << 256
MIN_TICK, MAX_TICK = -887272, 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342


def mul_div(a, b, d):
    return (a * b) // d


def mul_div_up(a, b, d):
    r = (a * b) // d
    if (a * b) % d:
        r += 1
    return r


def div_up(a, b):
    return a // b + (1 if a % b else 0)


def sqrt_ratio_at_tick(tick):
    abs_t = -tick if tick < 0 else tick
    assert abs_t <= MAX_TICK
    ratio = 0xfffcb933bd6fad37aa2d162d1a594001 if abs_t & 0x1 else 0x100000000000000000000000000000000
    for bit, c in [(0x2, 0xfff97272373d413259a46990580e213a), (0x4, 0xfff2e50f5f656932ef12357cf3c7fdcc),
                   (0x8, 0xffe5caca7e10e4e61c3624eaa0941cd0), (0x10, 0xffcb9843d60f6159c9db58835c926644),
                   (0x20, 0xff973b41fa98c081472e6896dfb254c0), (0x40, 0xff2ea16466c96a3843ec78b326b52861),
                   (0x80, 0xfe5dee046a99a2a811c461f1969c3053), (0x100, 0xfcbe86c7900a88aedcffc83b479aa3a4),
                   (0x200, 0xf987a7253ac413176f2b074cf7815e54), (0x400, 0xf3392b0822b70005940c7a398e4b70f3),
                   (0x800, 0xe7159475a2c29b7443b29c7fa6e889d9), (0x1000, 0xd097f3bdfd2022b8845ad8f792aa5825),
                   (0x2000, 0xa9f746462d870fdf8a65dc1f90e061e5), (0x4000, 0x70d869a156d2a1b890bb3df62baf32f7),
                   (0x8000, 0x31be135f97d08fd981231505542fcfa6), (0x10000, 0x9aa508b5b7a84e1c677de54f3e99bc9),
                   (0x20000, 0x5d6af8dedb81196699c329225ee604), (0x40000, 0x2216e584f5fa1ea926041bedfe98),
                   (0x80000, 0x48a170391f7dc42444e8fa2)]:
        if abs_t & bit:
            ratio = (ratio * c) >> 128
    if tick > 0:
        ratio = (U256 - 1) // ratio
    return (ratio >> 32) + (0 if ratio % (1 << 32) == 0 else 1)


def amount0_delta(sa, sb, liq, up):
    if sa > sb:
        sa, sb = sb, sa
    n1, n2 = liq << 96, sb - sa
    assert sa > 0
    if up:
        return div_up(mul_div_up(n1, n2, sb), sa)
    return mul_div(n1, n2, sb) // sa


def amount1_delta(sa, sb, liq, up):
    if sa > sb:
        sa, sb = sb, sa
    return mul_div_up(liq, sb - sa, Q96) if up else mul_div(liq, sb - sa, Q96)


def next_sqrt_from_amount0_up(sp, liq, amount, add=True):
    if amount == 0:
        return sp
    n1 = liq << 96
    assert add
    product = amount * sp
    if product < U256:  # no overflow
        denom = n1 + product
        if denom < U256:
            return mul_div_up(n1, sp, denom)
    return div_up(n1, n1 // sp + amount)


def next_sqrt_from_amount1_down(sp, liq, amount, add=True):
    assert add
    if amount <= (1 << 160) - 1:
        q = (amount << 96) // liq
    else:
        q = mul_div(amount, Q96, liq)
    return sp + q


def next_sqrt_from_input(sp, liq, amount_in, zero_for_one):
    assert sp > 0 and liq > 0
    return (next_sqrt_from_amount0_up(sp, liq, amount_in) if zero_for_one
            else next_sqrt_from_amount1_down(sp, liq, amount_in))


def compute_swap_step(sc, st, liq, rem, fee):
    """exact-input only (rem >= 0). returns sqrtNext, amountIn, amountOut, feeAmount"""
    z = sc >= st
    assert rem >= 0
    rem_less_fee = mul_div(rem, 1_000_000 - fee, 1_000_000)
    amt_in = amount0_delta(st, sc, liq, True) if z else amount1_delta(sc, st, liq, True)
    if rem_less_fee >= amt_in:
        sn = st
    else:
        sn = next_sqrt_from_input(sc, liq, rem_less_fee, z)
    mx = st == sn
    if z:
        amt_in = amt_in if mx else amount0_delta(sn, sc, liq, True)
        amt_out = amount1_delta(sn, sc, liq, False)
    else:
        amt_in = amt_in if mx else amount1_delta(sc, sn, liq, True)
        amt_out = amount0_delta(sc, sn, liq, False)
    if sn != st:
        fee_amt = rem - amt_in
    else:
        fee_amt = mul_div_up(amt_in, fee, 1_000_000 - fee)
    return sn, amt_in, amt_out, fee_amt


def msb(x):
    return x.bit_length() - 1


def lsb(x):
    return (x & -x).bit_length() - 1


def next_init_tick_in_word(bitmap, tick, spacing, lte):
    """bitmap: dict wordPos->uint256 (missing words = 0)."""
    compressed = abs(tick) // spacing * (1 if tick >= 0 else -1)  # Solidity truncation
    if tick < 0 and tick % spacing != 0:
        compressed -= 1
    if lte:
        wp, bp = compressed >> 8, compressed & 0xff
        mask = (1 << bp) - 1 + (1 << bp)
        masked = bitmap.get(wp, 0) & mask
        init = masked != 0
        nxt = (compressed - (bp - msb(masked))) * spacing if init else (compressed - bp) * spacing
    else:
        c1 = compressed + 1
        wp, bp = c1 >> 8, c1 & 0xff
        mask = (U256 - 1) ^ ((1 << bp) - 1)
        masked = bitmap.get(wp, 0) & mask
        init = masked != 0
        nxt = (c1 + (lsb(masked) - bp)) * spacing if init else (c1 + (255 - bp)) * spacing
    return nxt, init, wp


def tick_at_sqrt_float(sp):
    import math
    t = math.floor(math.log((sp / Q96) ** 2) / math.log(1.0001))
    # correct floor by exact check
    while sqrt_ratio_at_tick(t + 1) <= sp:
        t += 1
    while sqrt_ratio_at_tick(t) > sp:
        t -= 1
    return t


def swap_exact_in(state, zero_for_one, amount_in, limit=None, need_word=None, log=None):
    """state: dict sqrtP, tick, liquidity, fee, spacing, bitmap(dict), liq_net(dict tick->int).
    need_word(wp) is called when a bitmap word is touched that is not loaded (to fetch lazily).
    Returns dict amountIn_used, amountOut, feeTotal, sqrtAfter, tickAfter, liqAfter, crossed list."""
    sp, tick, liq = state["sqrtP"], state["tick"], state["liquidity"]
    fee, spc, bm, ln = state["fee"], state["spacing"], state["bitmap"], state["liq_net"]
    if limit is None:
        limit = MIN_SQRT_RATIO + 1 if zero_for_one else MAX_SQRT_RATIO - 1
    rem, out, fee_tot, crossed, steps = amount_in, 0, 0, [], []
    while rem != 0 and sp != limit:
        start = sp
        if need_word is not None:
            c = tick if zero_for_one else tick + 1
            need_word(c >> 8)
        tn, init, wp = next_init_tick_in_word(bm, tick, spc, zero_for_one)
        tn = max(MIN_TICK, min(MAX_TICK, tn))
        sn_tick = sqrt_ratio_at_tick(tn)
        target = limit if ((sn_tick < limit) if zero_for_one else (sn_tick > limit)) else sn_tick
        sp, ai, ao, fa = compute_swap_step(sp, target, liq, rem, fee)
        rem -= ai + fa
        out += ao
        fee_tot += fa
        steps.append((tick, tn, init, liq, ai, ao, fa))
        if sp == sn_tick:
            if init:
                net = ln[tn]
                if zero_for_one:
                    net = -net
                liq = liq + net
                crossed.append((tn, net, liq))
            tick = tn - 1 if zero_for_one else tn
        elif sp != start:
            tick = tick_at_sqrt_float(sp)
    return {"amountIn_used": amount_in - rem, "amountOut": out, "feeTotal": fee_tot,
            "sqrtAfter": sp, "tickAfter": tick, "liqAfter": liq, "crossed": crossed,
            "steps": steps}
