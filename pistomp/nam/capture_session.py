"""Combined JACK client: plays a WAV out the FX send while recording the FX return.

One client owns both an output port (playback) and an input port (recording),
handling them in the same RT callback — frame-accurate start alignment and
equal capture length, no subprocess race.

Latency compensation: the round-trip hardware latency (output + input) is
queried from JACK before activation and stored as self._latency.  The capture
buffer is extended by L extra frames so the reamp signal plays to completion;
write_wav() trims the first L frames so the output WAV is sample-aligned with
the reamp signal.

Silence detection: after a 2-second settling window, 2 consecutive seconds of
near-silence on the input *while the reamp output is loud* triggers an abort.
Gating on playback level prevents false triggers during intentionally quiet
sections of the reamp WAV.

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
_SILENCE_SETTLE_FRAMES = _SAMPLE_RATE * 2   # 2 s before detection kicks in
_SILENCE_ABORT_FRAMES = _SAMPLE_RATE * 2    # 2 s of gated silence → abort
_SILENCE_INPUT_THRESHOLD = 1e-2             # ≈ −40 dBFS — capture must exceed this
_SILENCE_PLAY_THRESHOLD = 0.1               # ≈ −20 dBFS — reamp must be this loud to gate
_CLIP_THRESHOLD = 0.99                      # float32 full-scale; any peak above → abort


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

        self._capture: npt.NDArray[np.float32] | None = None
        self._latency: int = 0   # frames; set in start() after JACK query
        self._total: int = 0     # n + latency; set in start()
        self._pos = 0
        self._frames_elapsed = 0
        self._silence_run = 0

        self._client = None
        self._done = threading.Event()
        self._silence_abort = threading.Event()
        self._clip_abort = threading.Event()

        # Level accumulators — written by RT callback, read+reset by display thread.
        self._acc_in: float = 0.0
        self._acc_out: float = 0.0
        self._acc_count: int = 0

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

        # Query round-trip latency from the hardware ports so we can extend the
        # capture window and trim the result to produce an aligned WAV.
        try:
            send = client.get_port_by_name(self._send_port)
            ret  = client.get_port_by_name(self._return_port)
            L = send.get_latency_range(jack.PLAYBACK)[1] + ret.get_latency_range(jack.CAPTURE)[1]
        except Exception:
            L = 0
        self._latency = L

        samples = self._samples
        n = len(samples)
        total = n + L
        self._total = total
        capture = np.zeros(total, dtype=np.float32)
        self._capture = capture

        @client.set_process_callback
        def process(frames: int) -> None:
            pos = self._pos

            # ── playback — reamp signal then silence for L extra frames ────────
            out_buf: npt.NDArray[np.float32] = out_port.get_array()
            play_remain = n - pos
            if play_remain > 0:
                take = min(frames, play_remain)
                out_buf[:take] = samples[pos : pos + take]
                if take < frames:
                    out_buf[take:] = 0.0
            else:
                out_buf[:] = 0.0

            # ── capture — record for n + L frames ─────────────────────────────
            in_buf: npt.NDArray[np.float32] = in_port.get_array()
            cap_remain = total - pos
            if cap_remain > 0:
                take = min(frames, cap_remain)
                capture[pos : pos + take] = in_buf[:take]

            # ── advance position ──────────────────────────────────────────────
            new_pos = pos + frames
            self._pos = new_pos
            self._frames_elapsed += frames

            # ── level checks (peak without allocating a temp array) ───────────
            out_peak = max(float(np.max(out_buf)), float(-np.min(out_buf)))
            in_peak  = max(float(np.max(in_buf)),  float(-np.min(in_buf)))
            self._acc_in  += in_peak
            self._acc_out += out_peak
            self._acc_count += 1
            if in_peak >= _CLIP_THRESHOLD and not self._clip_abort.is_set():
                self._clip_abort.set()
            if self._frames_elapsed > _SILENCE_SETTLE_FRAMES and not self._silence_abort.is_set():
                if out_peak >= _SILENCE_PLAY_THRESHOLD:
                    if in_peak < _SILENCE_INPUT_THRESHOLD:
                        self._silence_run += frames
                        if self._silence_run >= _SILENCE_ABORT_FRAMES:
                            self._silence_abort.set()
                    else:
                        self._silence_run = 0
                # else: reamp is quiet — freeze counter, neither accumulate nor reset.

            # ── EOF ───────────────────────────────────────────────────────────
            if new_pos >= total and not self._done.is_set():
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

    def level_snapshot(self) -> tuple[float, float] | None:
        """Return (avg_in_peak, avg_out_peak) since last call and reset accumulators.

        Returns None if no callbacks have fired yet.  Called from display thread;
        relies on CPython GIL atomicity for the integer/float swaps.
        """
        count = self._acc_count
        if count == 0:
            return None
        avg_in  = self._acc_in  / count
        avg_out = self._acc_out / count
        self._acc_in  = 0.0
        self._acc_out = 0.0
        self._acc_count = 0
        return avg_in, avg_out

    # ── output ────────────────────────────────────────────────────────────────

    def write_wav(self, path: Path) -> None:
        """Write latency-trimmed captured audio as a 24-bit / 48 kHz / mono WAV."""
        assert self._capture is not None, "write_wav called before start()"
        L = self._latency
        end = min(self._pos, self._total)
        start = min(L, end)
        buf = self._capture[start:end]
        # float32 → int32 left-shifted by 8, then extract bytes [1,2,3] for 24-bit
        int32 = np.clip(buf * (2**31), -(2**31), 2**31 - 1).astype(np.int32)
        raw = int32.view(np.uint8).reshape(-1, 4)[:, 1:].tobytes()
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(3)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(raw)
