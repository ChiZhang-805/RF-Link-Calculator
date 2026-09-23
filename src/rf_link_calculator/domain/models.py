"""Immutable value objects; all powers are dBm, frequencies Hz, temperatures K."""

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class PowerSpec:
    mode: str = "unknown"
    reference: str = "input"
    value_dbm: float | None = None


@dataclass(frozen=True)
class NoiseSpec:
    mode: str = "unknown"
    value_db: float | None = None


@dataclass(frozen=True)
class Mixer:
    lo_frequency_hz: float = 2.3e9
    relation: str = "difference"
    noise_convention: str = "unknown"
    noise_compatible: bool = False
    lo_power_dbm: float | None = None


@dataclass(frozen=True)
class TwoTone:
    enabled: bool = False
    each_tone_power_dbm: float = -40.0
    spacing_hz: float = 1e5


@dataclass(frozen=True)
class Analysis:
    source_frequency_hz: float = 1e8
    signal_bandwidth_hz: float = 1e6
    noise_bandwidth_hz: float = 1e6
    source_noise_temperature_k: float = 290.0
    reference_temperature_k: float = 290.0
    reference_impedance_ohm: float = 50.0
    input_power_dbm: float = -60.0
    signal_mode: str = "cw"
    required_snr_db: float = 10.0
    implementation_loss_db: float = 0.0
    compression_backoff_db: float = 3.0
    compression_model: str = "p1_anchored_soft_saturation_v1"
    default_compression_p: float = 2.0
    two_tone: TwoTone = field(default_factory=TwoTone)


@dataclass(frozen=True)
class Stage:
    id: str = field(default_factory=lambda: str(uuid4()))
    order: int = 1
    enabled: bool = True
    name: str = "新器件"
    type: str = "amplifier"
    gain_db: float | None = 0.0
    noise: NoiseSpec = field(default_factory=NoiseSpec)
    ip3: PowerSpec = field(default_factory=PowerSpec)
    p1db: PowerSpec = field(default_factory=PowerSpec)
    physical_temperature_k: float = 290.0
    compression_p: float | None = None
    frequency_range_hz: tuple[float, float] | None = None
    mixer: Mixer | None = None
    absolute_max_input_dbm: float | None = None
    source: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class Project:
    schema_version: str = "1.0.0"
    project_id: str = field(default_factory=lambda: str(uuid4()))
    project_name: str = "射频链路项目"
    link_name: str = "接收链路"
    link_type: str = "receiver"
    analysis: Analysis = field(default_factory=Analysis)
    stages: tuple[Stage, ...] = ()
    assumptions: tuple[str, ...] = ()
    notes: str = ""
    lab: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.lab is None:
            data.pop("lab")
        return data


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    field_path: str
    message: str
    stage_id: str | None = None
    affected_metrics: tuple[str, ...] = ()
    suggestion: str = "请核对对应输入与数据来源"


@dataclass(frozen=True)
class Metric:
    value: float | None
    unit: str
    status: str = "valid"
    reference: str = "chain_input"
    model: str = "scalar_matched_v1"
    assumptions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class StageResult:
    stage_id: str
    name: str
    enabled: bool
    metrics: dict[str, Metric]


@dataclass(frozen=True)
class ResultBundle:
    project_hash: str
    calculation_hash: str
    created_at: str
    formula_version: str
    compression_model_version: str
    metrics: dict[str, Metric]
    stages: tuple[StageResult, ...]
    issues: tuple[Issue, ...]
    assumptions: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)
