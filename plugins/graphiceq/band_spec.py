"""Band specifications for the gx_graphiceq plugin (10 bands, ISO)."""

from __future__ import annotations

from plugins.eq.band_spec import GraphicBandSpec
from plugins.eq.graphic import _graphic_palette

# Port naming is non-sequential: G10=31Hz, G11=63Hz, G1=125Hz, ..., G8=16kHz.
# G9 (20 kHz) is omitted — above the 20 kHz display edge.
_BANDS: list[tuple[str, float, str]] = [
    ("31 Hz", 31.0, "G10"),
    ("63 Hz", 63.0, "G11"),
    ("125 Hz", 125.0, "G1"),
    ("250 Hz", 250.0, "G2"),
    ("500 Hz", 500.0, "G3"),
    ("1 kHz", 1000.0, "G4"),
    ("2 kHz", 2000.0, "G5"),
    ("4 kHz", 4000.0, "G6"),
    ("8 kHz", 8000.0, "G7"),
    ("16 kHz", 16000.0, "G8"),
]

_colors = _graphic_palette(len(_BANDS))

BAND_SPECS: tuple[GraphicBandSpec, ...] = tuple(
    GraphicBandSpec(name=name, freq_hz=freq, gain_sym=sym, gain_min=-30.0, gain_max=20.0, color=color)
    for (name, freq, sym), color in zip(_BANDS, _colors)
)
