"""Combined JACK client: plays a WAV out the FX send while recording the FX return.

One client owns both an output port (playback) and an input port (recording),
handling them in the same RT callback — frame-accurate start alignment and
equal capture length, no subprocess race.

Silence detection: after a 2-second settling window, 2 consecutive seconds of
near-silence on the input triggers an early-abort signal so the engine can
surface a "no audio returned" error without waiting for the full playback.

write_wav() is called after the client stops (outside the RT callback) and
serialises the captured float32 buffer to a 24-bit / 48 kHz / mono WAV.
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt

_SAMPLE_RATE = 48000
_SILENCE_SETTLE_FRAMES = _SAMPLE_RATE * 2  # 2 s settling before detection kicks in
_SILENCE_ABORT_FRAMES = _SAMPLE_RATE * 2  # 2 s of continuous silence → abort signal
_SILENCE_THRESHOLD = 1e-2  # ≈ −40 dBFS — high enough to clear floating-input noise
_CLIP_THRESHOLD = 0.99     # float32 full-scale; any frame peak above this is clipping


class CaptureSession:
    """Plays *samples* out *send_port* while capturing *return_port*."""

    def __init__(
        self,
        samples: npt.NDArray[np.float32],
        send_port: str,
        return_port: str,
        *,
        name: str = "pistomp-nam",
    ) -> None:
        self._samples = samples
        self._send_port = send_port
        self._return_port = return_port
        self._client_name = name

        n = len(samples)
        self._capture: npt.NDArray[np.float32] = np.zeros(n, dtype=np.float32)
        self._n = n
        self._pos = 0
        self._frames_elapsed = 0
        self._silence_run = 0

        self._client = None
        self._done = threading.Event()
        self._silence_abort = threading.Event()
        self._clip_abort = threading.Event()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        import jack  # type: ignore[import-untyped]

        client = jack.Client(self._client_name, no_start_server=True)
        self._client = client
        if client.samplerate != _SAMPLE_RATE:
            client.close()
            self._client = None
            raise RuntimeError(f"JACK sample rate is {client.samplerate}, expected {_SAMPLE_RATE}")

        out_port = client.outports.register("out")
        in_port = client.inports.register("in")

        samples = self._samples
        capture = self._capture
        n = self._n

        @client.set_process_callback
        def process(frames: int) -> None:
            pos = self._pos

            # ── playback ──────────────────────────────────────────────────────
            out_buf: npt.NDArray[np.float32] = out_port.get_array()
            play_remain = n - pos
            if play_remain > 0:
                take = min(frames, play_remain)
                out_buf[:take] = samples[pos : pos + take]
                if take < frames:
                    out_buf[take:] = 0.0
            else:
                out_buf[:] = 0.0

            # ── capture ───────────────────────────────────────────────────────
            in_buf: npt.NDArray[np.float32] = in_port.get_array()
            cap_remain = n - pos
            if cap_remain > 0:
                take = min(frames, cap_remain)
                capture[pos : pos + take] = in_buf[:take]

            # ── advance position ──────────────────────────────────────────────
            new_pos = pos + frames
            self._pos = new_pos
            self._frames_elapsed += frames

            # ── level checks (peak without allocating a temp array) ───────────
            peak = max(float(np.max(in_buf)), float(-np.min(in_buf)))
            if peak >= _CLIP_THRESHOLD and not self._clip_abort.is_set():
                self._clip_abort.set()
            if self._frames_elapsed > _SILENCE_SETTLE_FRAMES and not self._silence_abort.is_set():
                if peak < _SILENCE_THRESHOLD:
                    self._silence_run += frames
                    if self._silence_run >= _SILENCE_ABORT_FRAMES:
                        self._silence_abort.set()
                else:
                    self._silence_run = 0

            # ── EOF ───────────────────────────────────────────────────────────
            if new_pos >= n and not self._done.is_set():
                self._done.set()

        client.activate()
        client.connect(out_port, self._send_port)
        client.connect(self._return_port, in_port)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until EOF or *timeout* seconds.  Returns True on EOF."""
        return self._done.wait(timeout=timeout)

    def stop(self) -> None:
        if self._client is not None:
            self._done.set()
            self._silence_abort.set()
            try:
                self._client.deactivate()
                self._client.close()
            except Exception:
                pass
            self._client = None

    @property
    def silence_detected(self) -> bool:
        return self._silence_abort.is_set()

    @property
    def clip_detected(self) -> bool:
        return self._clip_abort.is_set()

    # ── output ────────────────────────────────────────────────────────────────

    def write_wav(self, path: Path) -> None:
        """Write captured audio to a 24-bit / 48 kHz / mono WAV file."""
        n = min(self._pos, self._n)
        buf = self._capture[:n]
        # float32 → int32 left-shifted by 8, then extract bytes [1,2,3] for 24-bit
        int32 = np.clip(buf * (2**31), -(2**31), 2**31 - 1).astype(np.int32)
        raw = int32.view(np.uint8).reshape(-1, 4)[:, 1:].tobytes()
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(3)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(raw)
