from __future__ import annotations

import logging
import threading
from enum import Enum, auto
from pathlib import Path

from pistomp.nam import routing
from pistomp.nam.capture_session import CaptureSession
from pistomp.nam.wavio import load_wav_float32

_REAMP_WAV = Path(__file__).resolve().parents[2] / "setup" / "nam" / "T3K-sweep-v3.wav"


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
        engine.start("my-amp")          # kicks off background thread
        while engine.state == CaptureState.CAPTURING:
            p = engine.progress()       # 0.0 → 1.0
            ...
        # engine.state in (DONE, FAILED, ABORTED)
        path = engine.output_path       # Path to the recorded WAV (if DONE)

    Routing (save/clear/restore) is wrapped in try/finally so the user's
    audio routing is always restored on completion, abort, or exception.

    A single CaptureSession JACK client handles both playback (FX send) and
    recording (FX return) in the same RT callback — frame-accurate alignment
    and equal length, no subprocess race.
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
        self._session: CaptureSession | None = None

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
        """Begin a capture.  No-op if already capturing."""
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

    def level_diff_db(self) -> float | None:
        """Return peak_in_dBFS − peak_out_dBFS since last call, or None if no data.

        Returns None when the output peak for the window is below the play
        gate threshold — transitions from silence produce meaningless spikes.
        """
        import math
        from pistomp.nam.capture_session import _SILENCE_PLAY_THRESHOLD

        with self._lock:
            session = self._session
        if session is None:
            return None
        snap = session.level_snapshot()
        if snap is None:
            return None
        avg_in, avg_out = snap
        if avg_out < _SILENCE_PLAY_THRESHOLD or avg_in <= 0:
            return None
        return 20.0 * math.log10(avg_in) - 20.0 * math.log10(avg_out)

    def stop(self) -> None:
        """Abort a running capture and wait for the thread to exit."""
        self._abort.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            self._thread = None

    # ── internal ──────────────────────────────────────────────────────────────

    def _run(self, name: str) -> None:
        saved: routing.Saved | None = None
        session: CaptureSession | None = None

        try:
            if not self._reamp_wav.exists():
                raise FileNotFoundError(
                    f"Reamp WAV not found: {self._reamp_wav}\n"
                    "Download T3K-sweep-v3.wav from the NAM trainer and place it at "
                    f"{self._reamp_wav}"
                )

            samples = load_wav_float32(self._reamp_wav)
            duration = len(samples) / 48000.0

            if self._abort.is_set():
                self._set_state(CaptureState.ABORTED)
                return

            saved = routing.snapshot(self._send_port, self._return_port)
            routing.clear(self._send_port, self._return_port)

            if self._abort.is_set():
                routing.restore(saved)
                saved = None
                self._set_state(CaptureState.ABORTED)
                return

            safe = (name.strip() or "capture").replace("/", "_").replace("\\", "_")
            self._output_dir.mkdir(parents=True, exist_ok=True)
            out_wav = self._output_dir / f"{safe}.wav"
            n = 2
            while out_wav.exists():
                out_wav = self._output_dir / f"{safe}-{n}.wav"
                n += 1

            session = CaptureSession(samples, self._send_port, self._return_port)
            with self._lock:
                self._session = session
            session.start()

            import time

            t0 = time.monotonic()
            while not session.wait(timeout=0.1):
                if self._abort.is_set():
                    session.stop()
                    session.write_wav(out_wav)
                    session = None
                    routing.restore(saved)
                    saved = None
                    with self._lock:
                        self._output_path = out_wav
                        self._state = CaptureState.ABORTED
                    return
                if session.clip_detected:
                    session.stop()
                    session = None
                    routing.restore(saved)
                    saved = None
                    with self._lock:
                        self._error = "Input clipped - reduce amp output level"
                        self._state = CaptureState.FAILED
                    return
                if session.silence_detected:
                    session.stop()
                    session = None
                    routing.restore(saved)
                    saved = None
                    with self._lock:
                        self._error = "No audio returned — check FX loop cable"
                        self._state = CaptureState.FAILED
                    return
                elapsed = time.monotonic() - t0
                with self._lock:
                    self._progress = min(elapsed / duration, 0.99)

            session.write_wav(out_wav)
            session.stop()
            session = None
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
            with self._lock:
                self._session = None
            if saved is not None:
                try:
                    routing.restore(saved)
                except Exception as exc:
                    logging.error("NAM routing restore failed: %s", exc)
            if session is not None:
                session.stop()

    def _set_state(self, state: CaptureState) -> None:
        with self._lock:
            self._state = state
