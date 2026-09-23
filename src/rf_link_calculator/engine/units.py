import math

LN10 = math.log(10)
KB = 1.380649e-23
T0 = 290.0


def db_to_ratio(db: float) -> float:
    value = 10 ** (db / 10)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("功率比超出双精度数值域")
    return value


def ratio_to_db(ratio: float) -> float:
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("功率比必须为正有限实数")
    return 10 * math.log10(ratio)


def dbm_to_mw(dbm: float) -> float:
    return db_to_ratio(dbm)


def mw_to_dbm(mw: float) -> float:
    return ratio_to_db(mw)


def dbm_to_w(dbm: float) -> float:
    return db_to_ratio(dbm - 30)


def w_to_dbm(w: float) -> float:
    return ratio_to_db(w) + 30


def logadd(a: float, b: float) -> float:
    """Natural log of a sum whose operands are represented as natural logs."""
    if a == -math.inf:
        return b
    if b == -math.inf:
        return a
    return max(a, b) + math.log1p(math.exp(-abs(a - b)))


def finite_exp(log_value: float) -> float | None:
    if log_value > 709 or log_value < -744:
        return None
    return math.exp(log_value)
