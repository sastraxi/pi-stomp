"""NAM (Neural Amp Modeler) plugin customizations.

Registers the tri-color tile chrome (yellow body, red top, blue bottom
borders) and a fullscreen ``NamPanel`` for selecting models and adjusting
the three NAM control-port parameters (input level, output level,
quality).

The panel's file list is virtualised over the user's NAM library under
``~/data/user-files/NAM Models/`` (recursively scanned at open time),
so the 300+ models installed on a typical system don't cost 300 widgets.
"""

from __future__ import annotations

import os
import urllib.parse

from common.color import RectBorder
from plugins.customization import PluginCustomization, register
from plugins.nam.panel import NamPanel

_NAM_YELLOW = (224, 179, 0)
_NAM_RED = (220, 20, 20)
_NAM_BLUE = (20, 30, 220)

_NAM_URIS = (
    "http://github.com/mikeoliphant/neural-amp-modeler-lv2",
    "http://gareus.org/oss/lv2/nam#mono",
    "http://gareus.org/oss/lv2/nam#stereo",
    "https://tone3000.com/plugins/nam",
)


def _model_filename(plugin) -> str | None:
    path = plugin.model_path
    if path is None:
        return None
    decoded = urllib.parse.unquote(path)
    return os.path.basename(decoded)


def _nam_display_name(plugin) -> str | None:
    name = _model_filename(plugin)
    if name is None:
        return None
    stem, _ = os.path.splitext(name)
    return stem


def _nam_subtitle(plugin) -> str | None:
    name = _model_filename(plugin)
    if name is None:
        return None
    return f"NAM: {name}"


register(
    *_NAM_URIS,
    customization=PluginCustomization(
        tile_active_color=_NAM_YELLOW,
        tile_border=RectBorder(
            top=_NAM_RED,
            right=_NAM_YELLOW,
            bottom=_NAM_BLUE,
            left=_NAM_YELLOW,
        ),
        display_name_fn=_nam_display_name,
        subtitle_fn=_nam_subtitle,
        panel_cls=NamPanel,
    ),
)
