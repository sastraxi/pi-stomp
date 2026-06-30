"""Full-screen LCD panel for the NAM (Neural Amp Modeler) plugin.

Three control-port parameters (input_level, output_level, quality_scale) are
stacked as small arc knobs on the left; the right 80% of the screen is a
virtualized file picker over ``~/data/user-files/NAM Models/``.

Tweak1  →  quality_scale  (0.00 .. 1.00)
Tweak2  →  input_level    (-20.0 .. +20.0 dB)
Tweak3  →  output_level   (-20.0 .. +20.0 dB)
Nav     →  file selection (left/right, wraps, scrolls into view)

Picking a file sends a ``patch_set`` frame to mod-ui (LV2 atom:Path
``#model``); control-port changes go through the standard
``PluginPanel.set_param`` coalesce/flush path.

The "only currently-selected file animates" guarantee is structural, not
gated by tick logic: ``FileListView`` attaches a ``ScrollingText`` child
to the selected row only. When the selection moves the old row is
rebuilt without that child, so its ``_anchor_time`` stops advancing.
Other rows have no child widget at all and never tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from typing_extensions import override

from plugins.base import PluginPanel
from plugins.nam.files import current_index, list_nam_files
from plugins.nam.widgets import FileListView, MiniArcKnob
from uilib.box import Box
from uilib.config import Config


# ── Plugin URIs (LV2 patch:writable targets) ────────────────────────────────
#
# The NAM plugin advertises a single atom:Path property `#model` that
# carries the file path. All four URIs we register for share the same
# `#model` symbol, so we use the one from the upstream Mike Oliphant
# build (the one whose LV2 the deployed pi-stomp is built against).
NAM_MODEL_URI = "http://github.com/mikeoliphant/neural-amp-modeler-lv2#model"

# Control-port symbols (all three use the same URIs / symbols).
_INPUT_SYM = "input_level"
_OUTPUT_SYM = "output_level"
_QUALITY_SYM = "quality_scale"


# ── Layout ──────────────────────────────────────────────────────────────────
_W = 320
_H = 240
_BTN_H = 28
_BTN_GAP = 2
_CONTENT_H = _H - _BTN_H - _BTN_GAP  # 210

# Left column: 3 stacked mini-knobs.
_KNOB_COL_W = 64
_KNOB_X = 2
_KNOB_PAD_Y = 2
_KNOB_H = (_CONTENT_H - 4 * _KNOB_PAD_Y) // 3  # ~67 px each

# Right column: file list.
_LIST_X = _KNOB_COL_W + 6
_LIST_Y = 2
_LIST_W = _W - _LIST_X - 4
_LIST_H = _CONTENT_H - 4
_ROW_H = 18


# ── Step sizes ──────────────────────────────────────────────────────────────
_DB_STEP = 0.5
_QUALITY_STEP = 0.05


def _param(plugin, symbol: str, default: float) -> float:
    p = plugin.parameters.get(symbol)
    if p is None or p.value is None:
        return default
    return float(p.value)


@dataclass
class NamState:
    input_db: float
    output_db: float
    quality: float
    selected_index: int
    scroll_top: int


class NamPanel(PluginPanel[NamState]):
    """Full-screen NAM plugin panel."""

    def __init__(self, *, plugin, handler, on_dismiss) -> None:
        # Pre-build the file list so the constructor's chrome step (which
        # captures state via snapshot_state) can read the right selected
        # index. list_nam_files reads the filesystem; this is a one-shot
        # cost at panel-open time.
        self._all_files = list_nam_files()
        self._initial_path = plugin.model_path
        self._initial_index = current_index(self._all_files, self._initial_path)
        if self._initial_index < 0 and self._all_files:
            self._initial_index = 0
        self._initial_model_path = self._initial_path  # captured for Reset

        # Mini knobs: created in build_widgets. The 3 widgets + 1 list
        # are stored on self for snapshot/refresh access.
        self._knob_quality: MiniArcKnob | None = None
        self._knob_input: MiniArcKnob | None = None
        self._knob_output: MiniArcKnob | None = None
        self._file_list: FileListView | None = None

        # Last-selected index is initialised to the already-loaded file
        # so the first tick doesn't re-send the model that the pedalboard
        # just loaded with.
        self._last_selected: int = self._initial_index

        super().__init__(plugin=plugin, handler=handler, on_dismiss=on_dismiss)

    # ── PluginPanel contract ────────────────────────────────────────────────

    def snapshot_state(self) -> NamState:
        return NamState(
            input_db=_param(self.plugin, _INPUT_SYM, 0.0),
            output_db=_param(self.plugin, _OUTPUT_SYM, 0.0),
            quality=_param(self.plugin, _QUALITY_SYM, 1.0),
            selected_index=self._file_list.selected_index if self._file_list else self._initial_index,
            scroll_top=self._file_list._top if self._file_list else 0,
        )

    def apply_state(self, state: NamState) -> None:
        if self._knob_input is not None:
            self._knob_input.set_value(state.input_db)
        if self._knob_output is not None:
            self._knob_output.set_value(state.output_db)
        if self._knob_quality is not None:
            self._knob_quality.set_value(state.quality)
        if self._file_list is not None:
            self._file_list.set_selected_index(state.selected_index, scroll=False)
            self._file_list._top = state.scroll_top
            self._file_list._build_rows()
            self._file_list.refresh()

    def build_widgets(self) -> None:
        # Three stacked knobs in the left column.
        knob_y0 = _KNOB_PAD_Y
        self._knob_quality = MiniArcKnob(
            box=Box.xywh(_KNOB_X, knob_y0, _KNOB_COL_W, _KNOB_H),
            label="Q",
            min_val=0.0,
            max_val=1.0,
            parent=self,
        )
        self._knob_input = MiniArcKnob(
            box=Box.xywh(_KNOB_X, knob_y0 + _KNOB_H + _KNOB_PAD_Y, _KNOB_COL_W, _KNOB_H),
            label="IN",
            min_val=-20.0,
            max_val=20.0,
            unit="dB",
            parent=self,
        )
        self._knob_output = MiniArcKnob(
            box=Box.xywh(_KNOB_X, knob_y0 + 2 * (_KNOB_H + _KNOB_PAD_Y), _KNOB_COL_W, _KNOB_H),
            label="OUT",
            min_val=-20.0,
            max_val=20.0,
            unit="dB",
            parent=self,
        )

        # Initialise knob readouts from current parameter values so the
        # very first paint reflects the live plugin state, not the
        # min-val defaults.
        self._knob_quality.set_value(_param(self.plugin, _QUALITY_SYM, 1.0))
        self._knob_input.set_value(_param(self.plugin, _INPUT_SYM, 0.0))
        self._knob_output.set_value(_param(self.plugin, _OUTPUT_SYM, 0.0))

        # File list (right column).
        self._file_list = FileListView(
            box=Box.xywh(_LIST_X, _LIST_Y, _LIST_W, _LIST_H),
            row_h=_ROW_H,
            files=[str(p) for p in self._all_files],
            parent=self,
        )
        if self._all_files:
            self._file_list.set_selected_index(self._initial_index, scroll=True)

        # The list is the only selectable widget from our subclass —
        # nav encoder drives its step(). The base class adds Back/Bypass/Reset
        # after build_widgets returns. We register the list as the
        # subclass-scope selectable so the user can land on it (rather
        # than chrome) when the panel opens.
        self.add_sel_widget(self._file_list)

    # ── Encoder routing ────────────────────────────────────────────────────

    @override
    def on_encoder_rotation(self, encoder_id: int, rotations: int) -> bool:
        if rotations == 0:
            return True
        if encoder_id == 1:  # Tweak1 → quality
            new_q = max(0.0, min(1.0, self._knob_quality.value + rotations * _QUALITY_STEP))
            if new_q != self._knob_quality.value:
                self._knob_quality.set_value(new_q)
                self.set_param(_QUALITY_SYM, new_q)
            return True
        if encoder_id == 2:  # Tweak2 → input dB
            new_db = max(-20.0, min(20.0, self._knob_input.value + rotations * _DB_STEP))
            if new_db != self._knob_input.value:
                self._knob_input.set_value(new_db)
                self.set_param(_INPUT_SYM, new_db)
            return True
        if encoder_id == 3:  # Tweak3 → output dB
            new_db = max(-20.0, min(20.0, self._knob_output.value + rotations * _DB_STEP))
            if new_db != self._knob_output.value:
                self._knob_output.set_value(new_db)
                self.set_param(_OUTPUT_SYM, new_db)
            return True
        return False

    # ── File selection ─────────────────────────────────────────────────────

    def _on_pick_file(self) -> None:
        """Send the selected file's path via LV2 patch:Set (atom:Path)."""
        path = self._file_list.selected_path
        if not path:
            return
        bridge = self.handler.ws_bridge
        if bridge is None:
            return
        bridge.send_atom_patch(self.plugin.instance_id, NAM_MODEL_URI, path, valuetype="p")

    # ── Reset: also restore the model file ──────────────────────────────────

    def _on_reset(self) -> None:
        # Base class restores control-port symbols from the pedalboard
        # snapshot. The model file is *not* a control port, so we handle
        # it separately: re-send the originally-loaded file (captured
        # in __init__) and re-select it in the list.
        super()._on_reset()
        if self._initial_model_path and self._file_list is not None:
            idx = current_index(
                [Path(p) for p in self._file_list._files],
                self._initial_model_path,
            )
            if idx >= 0:
                self._file_list.set_selected_index(idx, scroll=True)
            bridge = self.handler.ws_bridge
            if bridge is not None:
                bridge.send_atom_patch(self.plugin.instance_id, NAM_MODEL_URI, self._initial_model_path, valuetype="p")

    # ── tick ────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        # Drive a one-shot file pick when the user has settled on a row
        # after a navigation move. We pick on the leading edge of a
        # selection change so each nav click commits immediately (matches
        # how footswitches send a MIDI CC on every press).
        if self._file_list is not None and self._file_list.selected_index != self._last_selected:
            self._last_selected = self._file_list.selected_index
            self._on_pick_file()
        super().tick()
