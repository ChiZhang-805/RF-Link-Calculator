import math

from rf_link_calculator.domain.labels import PASSIVE
from rf_link_calculator.domain.models import Stage

from .units import LN10, T0


def added_noise(stage: Stage) -> float | None:
    """Returns F-1 at fixed T0=290 K, not a dB contribution."""
    if not stage.enabled:
        return 0.0
    if stage.type == "mixer" and (
        stage.mixer is None or stage.mixer.noise_convention == "unknown" or not stage.mixer.noise_compatible
    ):
        return None
    n = stage.noise
    if n.mode == "ideal":
        return 0.0
    if n.mode == "manual" and n.value_db is not None:
        return math.expm1(n.value_db * LN10 / 10)
    if (
        n.mode == "passive_thermal"
        and stage.type in PASSIVE
        and stage.gain_db is not None
        and stage.gain_db <= 0
    ):
        return math.expm1(-stage.gain_db * LN10 / 10) * stage.physical_temperature_k / T0
    return None
