"""Frequency propagation includes selected mixer sideband and spectral inversion."""

from rf_link_calculator.domain.models import Stage


def map_frequency(stage: Stage, frequency: float) -> tuple[float | None, bool]:
    if stage.type != "mixer" or not stage.enabled:
        return frequency, False
    if stage.mixer is None:
        return None, False
    m = stage.mixer
    if m.relation == "sum":
        return frequency + m.lo_frequency_hz, False
    return abs(frequency - m.lo_frequency_hz), frequency < m.lo_frequency_hz
