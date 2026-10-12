"""lib.py — общие примитивы метода refA: RPC (read-only), keccak256, TickMath, LiquidityAmounts.

Только stdlib. Все вычисления целочисленные, как в контрактах Uniswap V3.
"""
import json
import time
import urllib.request

RPC = "https://forno.celo.org"
Q96 = 1 << 96
Q128 = 1 << 128
M256 = 1 << 256

ALLOWED = {"eth_call", "eth_getLogs", "eth_getBlockByNumber", "eth_getTransactionByHash",
           "eth_getTransactionReceipt", "eth_blockNumber", "eth_chainId"}


def rpc(method, params, url=RPC, _retry=0):
    assert method in ALLOWED, f"method {method} not allowed (read-only)"
    req = urllib.request.Request(
        url, data=json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 refA-verify"})
    try:
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.loads(r.read())
        except urllib.error.HTTPError as he:
            body = he.read().decode(errors="replace")
            if he.code == 400:  # детерминированная ошибка запроса (лимит диапазона, revert) — без ретраев
                raise ValueError(f"rpc http400: {body[:300]}")
            raise
        if "error" in j:
            raise ValueError(f"rpc error: {j['error']}")
        return j["result"]
    except ValueError:
        raise
    except Exception:
        if _retry >= 4:
            raise
        time.sleep(2 * (_retry + 1))
        return rpc(method, params, url, _retry + 1)


def call(to, data, block, frm=None):
    tx = {"to": to, "data": data}
    if frm:
        tx["from"] = frm
    return rpc("eth_call", [tx, hex(block) if isinstance(block, int) else block])


def words(h):
    h = h[2:] if h.startswith("0x") else h
    return [int(h[i:i + 64], 16) for i in range(0, len(h), 64)]


def s(v, bits):
    """uint -> signed int of given width (берём младшие bits бит)."""
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def enc_int(v):
    return format(v % M256, "064x")


def enc_addr(a):
    return a.lower().replace("0x", "").rjust(64, "0")


# ---------------- keccak256 (pure python) ----------------
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
       0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
       0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
       0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
       0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56],
        [27, 20, 39, 8, 14]]
_M64 = (1 << 64) - 1


def _rol(x, n):
    return ((x << n) | (x >> (64 - n))) & _M64 if n else x


def _f(A):
    for rc in _RC:
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ _rol(C[(x + 1) % 5], 1) for x in range(5)]
        A = [[A[x][y] ^ D[x] for y in range(5)] for x in range(5)]
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                B[y][(2 * x + 3 * y) % 5] = _rol(A[x][y], _ROT[x][y])
        A = [[B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        A[0][0] ^= rc
    return A


def keccak256(data: bytes) -> bytes:
    rate = 136
    p = bytearray(data) + b"\x01"
    while len(p) % rate:
        p += b"\x00"
    p[-1] |= 0x80
    A = [[0] * 5 for _ in range(5)]
    for off in range(0, len(p), rate):
        blk = p[off:off + rate]
        for i in range(rate // 8):
            x, y = i % 5, i // 5
            A[x][y] ^= int.from_bytes(blk[8 * i:8 * i + 8], "little")
        A = _f(A)
    out = b""
    for i in range(4):
        x, y = i % 5, i // 5
        out += A[x][y].to_bytes(8, "little")
    return out


def sel(sig):
    return "0x" + keccak256(sig.encode()).hex()[:8]


def topic(sig):
    return "0x" + keccak256(sig.encode()).hex()


# ---------------- TickMath.getSqrtRatioAtTick (точная копия) ----------------
def get_sqrt_ratio_at_tick(tick):
    abs_tick = -tick if tick < 0 else tick
    assert abs_tick <= 887272
    ratio = 0xfffcb933bd6fad37aa2d162d1a594001 if abs_tick & 0x1 else 0x100000000000000000000000000000000
    consts = [
        (0x2, 0xfff97272373d413259a46990580e213a), (0x4, 0xfff2e50f5f656932ef12357cf3c7fdcc),
        (0x8, 0xffe5caca7e10e4e61c3624eaa0941cd0), (0x10, 0xffcb9843d60f6159c9db58835c926644),
        (0x20, 0xff973b41fa98c081472e6896dfb254c0), (0x40, 0xff2ea16466c96a3843ec78b326b52861),
        (0x80, 0xfe5dee046a99a2a811c461f1969c3053), (0x100, 0xfcbe86c7900a88aedcffc83b479aa3a4),
        (0x200, 0xf987a7253ac413176f2b074cf7815e54), (0x400, 0xf3392b0822b70005940c7a398e4b70f3),
        (0x800, 0xe7159475a2c29b7443b29c7fa6e889d9), (0x1000, 0xd097f3bdfd2022b8845ad8f792aa5825),
        (0x2000, 0xa9f746462d870fdf8a65dc1f90e061e5), (0x4000, 0x70d869a156d2a1b890bb3df62baf32f7),
        (0x8000, 0x31be135f97d08fd981231505542fcfa6), (0x10000, 0x9aa508b5b7a84e1c677de54f3e99bc9),
        (0x20000, 0x5d6af8dedb81196699c329225ee604), (0x40000, 0x2216e584f5fa1ea926041bedfe98),
        (0x80000, 0x48a170391f7dc42444e8fa2)]
    for bit, c in consts:
        if abs_tick & bit:
            ratio = (ratio * c) >> 128
    if tick > 0:
        ratio = (M256 - 1) // ratio
    return (ratio >> 32) + (0 if ratio % (1 << 32) == 0 else 1)


# ---------------- LiquidityAmounts (округление вниз, как в periphery) ----------------
def amount0_for_liquidity(a, b, L):
    if a > b:
        a, b = b, a
    return ((L << 96) * (b - a) // b) // a


def amount1_for_liquidity(a, b, L):
    if a > b:
        a, b = b, a
    return L * (b - a) // Q96


def amounts_for_liquidity(sp, a, b, L):
    if a > b:
        a, b = b, a
    if sp <= a:
        return amount0_for_liquidity(a, b, L), 0
    if sp < b:
        return amount0_for_liquidity(sp, b, L), amount1_for_liquidity(a, sp, L)
    return 0, amount1_for_liquidity(a, b, L)


# ---------------- SqrtPriceMath (roundUp — как pool.mint) ----------------
def _ceil_div(x, d):
    return -(-x // d)


def amount0_delta_up(a, b, L):
    if a > b:
        a, b = b, a
    n1, n2 = L << 96, b - a
    return _ceil_div(_ceil_div(n1 * n2, b), a)


def amount1_delta_up(a, b, L):
    if a > b:
        a, b = b, a
    return _ceil_div(L * (b - a), Q96)


def fee_growth_inside(tick_cur, tl, tu, g, lo_out, up_out):
    below = lo_out if tick_cur >= tl else (g - lo_out) % M256
    above = up_out if tick_cur < tu else (g - up_out) % M256
    return (g - below - above) % M256
