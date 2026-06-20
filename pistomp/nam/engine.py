from __future__ import annotations

import logging
import subprocess
import threading
import time
from enum import Enum, auto
from pathlib import Path

from pistomp.nam import routing
from pistomp.nam.player import JackPlayer
from pistomp.nam.wavio import load_wav_float32

_REAMP_WAV = Path(__file__).resolve().parents[2] / "setup" / "nam" / "v3_0_0.wav"


class CaptureState(Enum):
    IDLE = auto()
    CAPTURING = auto()
    DONE = auto()
    FAILED = auto()
    ABORTED = auto()


class NamCaptureEngine:
    """
    NAM Capture Engine — orchestrates a single FX-loop recording session.

    Lifecycle::

        engine = NamCaptureEngine(output_dir)
        engine.start("my-amp")          # kick off background thread
        while engine.state == CaptureState.CAPTURING:
            p = engine.progress()       # 0.0 → 1.0
            ...
        # engine.state in (DONE, FAILED, ABORTED)
        path = engine.output_path       # Path to the recorded WAV (if DONE)

    The routing (save/clear/restore) is wrapped in try/finally so the user's
    audio routing is always restored on normal completion, abort, or exception.

    Recording: shell out to jack_capture (custom-packaged in pistomp-arch).
    Playback:  embedded JackPlayer (Python jack outport client) that pre-loads
               the entire reamp WAV into a numpy float32 array.
    """

    def __init__(
        self,
        output_dir: Path | str,
        reamp_wav: Path | str = _REAMP_WAV,
        send_port: str = routing.FX_SEND_PORT,
        return_port: str = routing.FX_RETURN_PORT,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._reamp_wav = Path(reamp_wav)
        self._send_port = send_port
        self._return_port = return_port

        self._state = CaptureState.IDLE
        self._progress: float = 0.0
        self._error: str | None = None
        self._output_path: Path | None = None
        self._thread: threading.Thread | None = None
        self._abort = threading.Event()
        self._lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def state(self) -> CaptureState:
        with self._lock:
            return self._state

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def output_path(self) -> Path | None:
        with self._lock:
            return self._output_path

    def progress(self) -> float:
        with self._lock:
            return self._progress

    def start(self, name: str) -> None:
        """Begin a capture. No-op if already capturing."""
        with self._lock:
            if self._state == CaptureState.CAPTURING:
                return
            self._state = CaptureState.CAPTURING
            self._progress = 0.0
            self._error = None
            self._output_path = None
            self._abort.clear()

        self._thread = threading.Thread(target=self._run, args=(name,), daemon=True, name="nam-capture")
        self._thread.start()

    def stop(self) -> None:
        """Abort a running capture and wait for the thread to exit."""
        self._abort.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None

    # ── internal ──────────────────────────────────────────────────────────────

    def _run(self, name: str) -> None:
        saved: routing.Saved | None = None
        recorder: subprocess.Popen | None = None
        player: JackPlayer | None = None

        try:
            if not self._reamp_wav.exists():
                raise FileNotFoundError(
                    f"Reamp WAV not found: {self._reamp_wav}\n"
                    "Download v3_0_0.wav from the NAM trainer and place it at "
                    f"{self._reamp_wav}"
                )

            samples = load_wav_float32(self._reamp_wav)
            duration = len(samples) / 48000.0

            if self._abort.is_set():
                self._set_state(CaptureState.ABORTED)
                return

            # Snapshot then clear existing FX-loop connections.
            saved = routing.snapshot(self._send_port, self._return_port)
            routing.clear(self._send_port, self._return_port)

            if self._abort.is_set():
                routing.restore(saved)
                saved = None
                self._set_state(CaptureState.ABORTED)
                return

            # Prepare output path.  jack_capture adds .wav; we strip it from the stem.
            safe = (name.strip() or "capture").replace("/", "_").replace("\\", "_")
            self._output_dir.mkdir(parents=True, exist_ok=True)
            out_wav = self._output_dir / f"{safe}.wav"
            out_stem = str(out_wav.with_suffix(""))

            # Start recorder before player so no samples are missed.
            recorder = subprocess.Popen(
                [
                    "jack_capture",
                    "--port",
                    self._return_port,
                    "--bitdepth",
                    "24",
                    "--channels",
                    "1",
                    "--filename",
                    out_stem,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            # Start player and wire it to the FX send port.
            player = JackPlayer(samples, self._send_port)
            player.start()

            # Poll until playback completes, surfacing progress to the UI.
            t0 = time.monotonic()
            while not player.wait(timeout=0.1):
                if self._abort.is_set():
                    player.stop()
                    player = None
                    _stop_recorder(recorder)
                    recorder = None
                    routing.restore(saved)
                    saved = None
                    self._set_state(CaptureState.ABORTED)
                    return
                elapsed = time.monotonic() - t0
                with self._lock:
                    self._progress = min(elapsed / duration, 0.99)

            player.stop()
            player = None
            _stop_recorder(recorder)
            recorder = None

            routing.restore(saved)
            saved = None

            with self._lock:
                self._output_path = out_wav
                self._progress = 1.0
                self._state = CaptureState.DONE

        except Exception as exc:
            logging.error("NAM capture failed: %s", exc, exc_info=True)
            with self._lock:
                self._error = str(exc)
                self._state = CaptureState.FAILED

        finally:
            if saved is not None:
                try:
                    routing.restore(saved)
                except Exception as exc:
                    logging.error("NAM routing restore failed: %s", exc)
            if recorder is not None and recorder.poll() is None:
                _stop_recorder(recorder)
            if player is not None:
                player.stop()

    def _set_state(self, state: CaptureState) -> None:
        with self._lock:
            self._state = state


def _stop_recorder(proc: subprocess.Popen) -> None:
    """Terminate jack_capture and wait for it to flush its output."""
    try:
        proc.terminate()
        proc.wait(timeout=5.0)
    except Exception as exc:
        logging.warning("jack_capture teardown: %s", exc)
