"""JACK outport client that plays a pre-loaded float32 audio buffer.

Models JackSource in pistomp/tuner/source.py, inverted to an output port.
The entire WAV is pre-loaded into a numpy array before the client activates,
so the RT process callback only slices/copies — no file I/O in the hot path.
A threading.Event signals EOF so the engine can stop the recorder precisely.
"""

from __future__ import annotations

import threading

import numpy as np
import numpy.typing as npt


class JackPlayer:
    """Plays a mono float32 sample array out a JACK output port."""

    def __init__(
        self,
        samples: npt.NDArray[np.float32],
        output_port: str,
        *,
        name: str = "pistomp-nam-play",
    ) -> None:
        self._samples = samples
        self._output_port = output_port
        self._client_name = name
        self._client = None
        self._pos = 0
        self._done = threading.Event()

    def start(self) -> None:
        import jack  # type: ignore[import-untyped]

        client = jack.Client(self._client_name, no_start_server=True)
        self._client = client
        if self._client.samplerate != 48000:
            self._client.close()
            self._client = None
            raise RuntimeError(f"JACK sample rate is {client.samplerate}, expected 48000")

        port = client.outports.register("out")
        samples = self._samples  # local ref for the RT callback

        @client.set_process_callback
        def process(frames: int) -> None:
            buf: npt.NDArray[np.float32] = port.get_array()
            pos = self._pos
            remaining = len(samples) - pos
            if remaining <= 0:
                buf[:] = 0.0
                if not self._done.is_set():
                    self._done.set()
                return
            n = min(frames, remaining)
            buf[:n] = samples[pos : pos + n]
            if n < frames:
                buf[n:] = 0.0
            self._pos = pos + n
            if self._pos >= len(samples) and not self._done.is_set():
                self._done.set()

        self._client.activate()
        self._client.connect(port, self._output_port)

    def wait(self, timeout: float | None = None) -> bool:
        """Block until EOF or *timeout* seconds. Returns True if EOF was reached."""
        return self._done.wait(timeout=timeout)

    def stop(self) -> None:
        if self._client is not None:
            self._done.set()  # unblock any waiter
            try:
                self._client.deactivate()
                self._client.close()
            except Exception:
                pass
            self._client = None
