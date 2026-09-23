"""RF functional symbols, shared by the workbench and both vector exporters.

Conventions and reference sources are recorded in docs/射频符号约定.md.
These are functional budget diagrams, not transistor/pin-level schematics.
"""

from dataclasses import dataclass
from math import pi, sin

SYMBOL_VERSION = "2.0"


@dataclass(frozen=True)
class Primitive:
    kind: str
    points: tuple[tuple[float, float], ...]
    text: str = ""


def symbol(kind: str) -> list[Primitive]:
    def line(*pts):
        return Primitive("polyline", tuple(pts))

    box = Primitive("rect", ((75, 10), (90, 80)))
    circle = Primitive("ellipse", ((90, 20), (60, 60)))
    leads = [line((0, 50), (75, 50)), line((165, 50), (240, 50))]

    def wave(y, x=88, width=64, amplitude=5):
        return line(
            *(
                (round(x + width * i / 32, 3), round(y - amplitude * sin(2 * pi * i / 32), 3))
                for i in range(33)
            )
        )

    if kind in ("amplifier", "lna", "pa", "vga"):
        # A generic amplifier has one signal input; +/- would incorrectly imply an op-amp.
        # Names (LNA, PA, VGA) belong outside the symbol, in the stage label.
        return leads + [
            Primitive("polygon", ((75, 5), (165, 50), (75, 95))),
        ]
    if kind.startswith("filter_"):
        # Three frequency bands, high to low; slash means rejected band.
        rejected = {
            "filter_bpf": (30, 70),
            "filter_lpf": (30, 50),
            "filter_hpf": (50, 70),
            "filter_bsf": (50,),
        }
        return leads + [
            box,
            *(wave(y) for y in (30, 50, 70)),
            *(line((114, y + 7), (126, y - 7)) for y in rejected[kind]),
        ]
    if kind == "mixer":
        return [
            line((0, 50), (90, 50)),
            line((150, 50), (240, 50)),
            circle,
            line((101, 31), (139, 69)),
            line((101, 69), (139, 31)),
            line((120, 0), (120, 20)),
        ]
    if kind == "lo":
        return [
            circle,
            wave(50, 98, 44, 11),
        ]
    if kind == "attenuator":
        # Functional attenuation mark; do not invent an internal resistor network.
        return leads + [
            box,
            line((94, 30), (94, 70)),
            line((94, 50), (146, 50)),
            line((146, 38), (146, 62)),
        ]
    if kind in ("cable", "connector"):
        return [
            line((0, 50), (240, 50)),
            line((65, 40), (175, 40)),
            line((65, 60), (175, 60)),
            Primitive("ellipse", ((60, 39), (10, 22))),
            Primitive("ellipse", ((170, 39), (10, 22))),
        ]
    if kind == "switch":
        return [
            line((0, 50), (90, 50)),
            line((150, 50), (240, 50)),
            line((90, 50), (150, 25)),
            Primitive("ellipse", ((85, 45), (10, 10))),
            Primitive("ellipse", ((145, 45), (10, 10))),
        ]
    if kind == "isolator":
        return leads + [box, line((85, 50), (155, 50)), line((140, 35), (155, 50), (140, 65))]
    if kind == "coupler":
        return leads + [box, line((75, 50), (165, 50)), line((90, 70), (150, 70))]
    if kind == "splitter":
        return [
            line((0, 50), (120, 50), (120, 30), (180, 30), (180, 50), (240, 50)),
            line((120, 50), (120, 75), (180, 75)),
        ]
    if kind in ("input", "output"):
        return [
            line((0, 50), (116, 50)),
            line((124, 50), (240, 50)),
            Primitive("ellipse", ((116, 46), (8, 8))),
        ]
    if kind == "antenna":
        return [line((0, 50), (240, 50)), line((120, 50), (120, 5)), line((98, 5), (120, 30), (142, 5))]
    if kind in ("adc", "dac", "digital"):
        return leads + [
            box,
            Primitive("text", ((100, 55),), {"adc": "A/D", "dac": "D/A", "digital": "DSP"}[kind]),
        ]
    return leads + [box]
