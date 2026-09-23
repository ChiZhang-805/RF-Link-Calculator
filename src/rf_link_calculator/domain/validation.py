"""Semantic checks preserve independent availability of noise, IP3 and compression."""

import math

from .labels import BOUNDARIES, PASSIVE
from .models import Issue, Project


def real(value: object) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def validate(project: Project) -> tuple[Issue, ...]:
    issues = []

    def add(code, path, message, sid=None, severity="warning", affected=()):
        issues.append(Issue(code, severity, path, message, sid, affected))

    if len({s.id for s in project.stages}) != len(project.stages):
        add("E001", "stages.id", "器件ID重复", severity="error")
    if len({s.order for s in project.stages}) != len(project.stages):
        add("E001", "stages.order", "器件顺序重复", severity="error")
    if not project.stages or not any(s.enabled for s in project.stages):
        add("I000", "stages", "尚无启用器件；恒等传输不代表完整系统", severity="info")
    for s in project.stages:
        if s.frequency_range_hz and s.frequency_range_hz[0] >= s.frequency_range_hz[1]:
            add("E003", "frequency_range_hz", "频率上限必须高于下限", s.id, "error")
        if not s.enabled:
            continue
        if s.type in BOUNDARIES:
            if s.gain_db != 0 or any(x.mode != "ideal" for x in (s.noise, s.ip3, s.p1db)):
                add("E002", "type", "边界只能用0 dB、理想标注；不参与天线dBi或数字噪声换算", s.id, "error")
        if not real(s.gain_db):
            add("E002", "gain_db", "增益缺失或非法：该级及后级的参考换算不可用", s.id, "error")
        for key, spec in (("noise", s.noise), ("ip3", s.ip3), ("p1db", s.p1db)):
            val = getattr(spec, "value_db", getattr(spec, "value_dbm", None))
            if spec.mode == "unknown" or (spec.mode in ("finite", "manual") and val is None):
                add("W001", key, f"{key} 参数不完整，不会自动视为理想", s.id, affected=(key,))
        if s.noise.mode == "passive_thermal" and (
            s.type not in PASSIVE or not real(s.gain_db) or s.gain_db > 0
        ):
            add("W005", "noise", "热无源噪声要求匹配无源器件和非正增益", s.id, affected=("noise",))
        if s.type == "mixer":
            if s.mixer is None:
                add("E003", "mixer", "混频器缺少LO与变频关系", s.id, "error")
            elif s.mixer.noise_convention == "unknown" or not s.mixer.noise_compatible:
                add(
                    "W010",
                    "mixer.noise_convention",
                    "混频器噪声口径未确认，停止可靠噪声预算",
                    s.id,
                    affected=("noise",),
                )
        if s.type == "vga" and not s.source.get("bias"):
            add("W015", "source.bias", "VGA需记录当前增益档位／偏置条件", s.id)
        if any(x.mode == "ideal" for x in (s.noise, s.ip3, s.p1db)):
            add("W011", "assumptions", "使用了显式理想模型", s.id, "info")
        source = s.source
        if (
            source.get("test_frequency_hz")
            and source["test_frequency_hz"] != project.analysis.source_frequency_hz
        ):
            add("W015", "source", "来源测试频点与链路入口不同，请按本级实际频点复核", s.id)
        if source.get("conditions_mismatch"):
            add("W015", "source", "数据来源声明了频率／温度／偏置条件不一致", s.id)
    return tuple(issues)
