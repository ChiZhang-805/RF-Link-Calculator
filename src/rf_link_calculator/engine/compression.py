"""Independent AM/AM estimate, deliberately not inferred from IP3."""

import math

from rf_link_calculator.domain.models import Metric, Stage

from .units import LN10, logadd


def input_reference(stage: Stage, kind: str = "p1db") -> float | None:
    spec = getattr(stage, kind)
    if spec.mode != "finite" or spec.value_dbm is None or stage.gain_db is None:
        return None
    if spec.reference == "input":
        return spec.value_dbm
    return spec.value_dbm - stage.gain_db + (1 if spec.reference == "actual_output" else 0)


def compression_loss(pin: float, ip1: float, p: float) -> float:
    if not all(math.isfinite(v) for v in (pin, ip1, p)) or not 1 <= p <= 10:
        raise ValueError("压缩模型需要有限数值且1≤p≤10")
    z = math.log(math.expm1(p * LN10 / 10)) + p * (pin - ip1) * LN10 / 10
    return 10 / (p * LN10) * (max(z, 0) + math.log1p(math.exp(-abs(z))))


def propagate(stages: tuple[Stage, ...], pin: float, default_p: float = 2) -> tuple[float, list[tuple]]:
    trace = []
    x = pin
    for s in stages:
        if not s.enabled:
            continue
        if s.gain_db is None:
            raise ValueError("增益不完整")
        ip1 = input_reference(s)
        if s.p1db.mode == "ideal":
            c = 0.0
        elif ip1 is not None:
            c = compression_loss(x, ip1, s.compression_p or default_p)
        else:
            raise ValueError("压缩参数不完整")
        y = x + s.gain_db - c
        trace.append((s.id, x, y, c, None if ip1 is None else ip1 - x))
        x = y
    return x, trace


def solve_system_p1db(stages: tuple[Stage, ...], default_p: float = 2) -> Metric:
    active = tuple(s for s in stages if s.enabled)
    prefix, thresholds = 0.0, []
    for s in active:
        if (
            s.gain_db is None
            or s.p1db.mode == "unknown"
            or (s.p1db.mode == "finite" and input_reference(s) is None)
        ):
            return Metric(None, "dBm", "unknown", reason="压缩参数不完整", dependencies=("gain", "p1db"))
        if s.p1db.mode == "finite":
            thresholds.append(input_reference(s) - prefix)
        prefix += s.gain_db
    if not thresholds:
        return Metric(None, "dBm", "ideal", reason="全链路显式理想，无有限P1")
    lo, hi = min(thresholds) - 80, max(thresholds) + 20

    def residual(x):
        return x + prefix - propagate(active, x, default_p)[0] - 1

    try:
        for _ in range(8):
            if residual(lo) < 0 < residual(hi):
                break
            lo -= 40
            hi += 40
        else:
            return Metric(None, "dBm", "failed", reason="搜索范围未夹住1 dB压缩根")
        for _ in range(80):
            mid = (lo + hi) / 2
            if residual(mid) > 0:
                hi = mid
            else:
                lo = mid
            if hi - lo < 1e-8:
                break
        return Metric(
            (lo + hi) / 2,
            "dBm",
            "estimated",
            model="p1_anchored_soft_saturation_v1",
            assumptions=("单音CW软饱和工程模型，非实测",),
            dependencies=("gain", "p1db", "p"),
        )
    except (ValueError, OverflowError) as exc:
        return Metric(None, "dBm", "failed", reason=str(exc))


def closed_p1(stages: tuple[Stage, ...], default_p: float = 2) -> float | None:
    active = tuple(s for s in stages if s.enabled)
    finite = [s for s in active if s.p1db.mode == "finite"]
    if not finite or any(s.p1db.mode == "unknown" or s.gain_db is None for s in active):
        return None
    ps = {s.compression_p or default_p for s in finite}
    if len(ps) != 1 or any(input_reference(s) is None for s in finite):
        return None
    p = ps.pop()
    prefix, total = 0.0, -math.inf
    for s in active:
        if s.p1db.mode == "finite":
            total = logadd(total, p * (prefix - input_reference(s)) * LN10 / 10)
        prefix += s.gain_db
    return -10 * total / (p * LN10)
