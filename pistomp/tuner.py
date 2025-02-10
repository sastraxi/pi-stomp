#!/usr/bin/env python3
# This file is part of pi-stomp.
#
# pi-stomp is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# pi-stomp is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with pi-stomp.  If not, see <https://www.gnu.org/licenses/>.

import os
import numpy as np
import jack
import time
import queue
import threading
import gc
import pyfftw

# =============================================================================
# Configuration Constants
# =============================================================================
SAMPLE_FREQ        = 48000          # sample frequency in Hz
WINDOW_SIZE        = 48000          # FFT window size in samples
WINDOW_STEP        = 12000          # number of samples to slide the window each time
NUM_HPS            = 5              # maximum number of harmonic product spectrums
POWER_THRESH       = 1e-5           # skip processing if the signal power is below this
CONCERT_PITCH      = 440            # A4 = 440 Hz
WHITE_NOISE_THRESH = 0.8            # fraction for noise suppression

# Frequency resolution:
DELTA_FREQ = SAMPLE_FREQ / WINDOW_SIZE

# Octave bands (Hz) for noise suppression:
OCTAVE_BANDS = [50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600]

# Note names:
ALL_NOTES = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]

# Only consider detected frequencies in this range:
MIN_FREQ = 80    
MAX_FREQ = 1200  

# Pre-calculate a Hann window (float32 for pyFFTW)
HANN_WINDOW = np.hanning(WINDOW_SIZE).astype(np.float32)

# =============================================================================
# Globals for Threading and Buffering
# =============================================================================
sample_queue = queue.Queue(maxsize=200)  # Thread-safe queue for audio blocks
processing_buffer = np.zeros(0, dtype=np.float32)  # Rolling buffer for accumulating samples
FREQ_BUFFER_SIZE = 8  # For smoothing detected frequencies
freqBuffer = []

running    = False
proc_thread = None
client      = None

latest_closest_note = ""  # e.g., "A4"
latest_freq         = 0.0 # Detected (smoothed) frequency in Hz
latest_ideal_freq   = 0.0 # Ideal frequency for that note in Hz

# =============================================================================
# Helper Function: Note Detection
# =============================================================================
def find_closest_note(pitch):
    """
    Given a pitch (in Hz), compute the closest musical note and its ideal frequency.
    """
    i = int(np.round(np.log2(pitch / CONCERT_PITCH) * 12))
    closest_note = ALL_NOTES[i % 12] + str(4 + (i + 9) // 12)
    closest_pitch = CONCERT_PITCH * 2 ** (i / 12)
    return closest_note, closest_pitch

# =============================================================================
# JACK Callback (Realtime Thread)
# =============================================================================
def jack_callback(frames):
    """
    Minimal realtime JACK callback.
    Simply obtains the input block and enqueues it.
    (Keep this as lightweight as possible.)
    """
    try:
        data = client.inports[0].get_array()  # Get mono input as a numpy array.
        sample_queue.put_nowait(data)         # Drop block if queue is full.
    except queue.Full:
        pass
    return

# =============================================================================
# Processing Thread (Non-Realtime)
# =============================================================================
def processing_thread():
    """
    Non-realtime thread that pulls audio blocks from the queue, accumulates them
    in a rolling buffer, and processes FFT windows. Uses pyFFTW and further 
    precomputed constants for better performance.
    """
    global processing_buffer, freqBuffer, running
    global latest_closest_note, latest_freq, latest_ideal_freq

    gc.enable()  # Enable garbage collection in this thread.

    # Create pyFFTW aligned arrays and plan.
    in_array = pyfftw.empty_aligned(WINDOW_SIZE, dtype='float32')
    out_array = pyfftw.empty_aligned(WINDOW_SIZE // 2 + 1, dtype='complex64')
    fft_object = pyfftw.FFTW(in_array, out_array, direction='FFTW_FORWARD',
                             flags=('FFTW_MEASURE',))

    # Precompute constant indices:
    spec_size = WINDOW_SIZE // 2 + 1
    num_bins_mains = int(62 / DELTA_FREQ)  # Bins to zero out for mains hum

    # Precompute octave band index ranges (start, end) as tuples.
    octave_indices = []
    for j in range(len(OCTAVE_BANDS) - 1):
        start_idx = int(OCTAVE_BANDS[j] / DELTA_FREQ)
        end_idx = int(OCTAVE_BANDS[j+1] / DELTA_FREQ)
        octave_indices.append((start_idx, min(end_idx, spec_size)))
    
    # Precompute interpolation grid for the magnitude spectrum.
    interp_x = np.arange(0, spec_size, 1 / NUM_HPS)

    while running:
        try:
            # Wait briefly for a new audio block.
            new_block = sample_queue.get(timeout=0.01)
            # Append the new block (Note: if profiling shows this concatenation to be a bottleneck,
            # consider implementing a pre-allocated ring buffer to avoid repeated allocations).
            processing_buffer = np.concatenate((processing_buffer, new_block))
        except queue.Empty:
            continue

        # Process as long as there's at least one full window.
        while processing_buffer.size >= WINDOW_SIZE:
            window = processing_buffer[:WINDOW_SIZE]
            processing_buffer = processing_buffer[WINDOW_STEP:]

            # Compute power (using vectorized np.sum).
            signal_power = np.sum(window ** 2) / WINDOW_SIZE
            if signal_power < POWER_THRESH:
                continue

            # Apply Hann window.
            hann_samples = window * HANN_WINDOW

            # Compute FFT via pyFFTW.
            in_array[:] = hann_samples
            fft_object()
            # Copy FFT output to avoid modifying FFTW's internal buffers.
            fft_result = out_array.copy()
            magnitude_spec = np.abs(fft_result)

            # --- Suppress mains hum: Zero out bins below ~62 Hz ---
            magnitude_spec[:num_bins_mains] = 0

            # --- Suppress low-level noise in each octave band ---
            for start_idx, end_idx in octave_indices:
                segment = magnitude_spec[start_idx:end_idx]
                if segment.size == 0:
                    continue
                # Compute average energy using L2 norm.
                avg_energy = np.sqrt(np.sum(segment ** 2) / segment.size)
                # Zero-out bins that are below the threshold.
                mask = segment < (WHITE_NOISE_THRESH * avg_energy)
                segment[mask] = 0

            # --- Interpolate the magnitude spectrum for finer resolution ---
            mag_spec_ipol = np.interp(interp_x,
                                      np.arange(spec_size),
                                      magnitude_spec)
            norm = np.linalg.norm(mag_spec_ipol, 2)
            if norm != 0:
                mag_spec_ipol /= norm

            # --- Compute the Harmonic Product Spectrum (HPS) ---
            hps_spec = mag_spec_ipol.copy()
            for i in range(NUM_HPS):
                tmp_len = int(np.ceil(mag_spec_ipol.size / (i + 1)))
                tmp_hps_spec = hps_spec[:tmp_len] * mag_spec_ipol[::(i + 1)]
                if not tmp_hps_spec.any():
                    break
                hps_spec = tmp_hps_spec

            # --- Find the peak and refine the frequency estimate via parabolic interpolation ---
            max_ind = np.argmax(hps_spec)
            if max_ind <= 0 or max_ind >= hps_spec.size - 1:
                p = 0.0
            else:
                alpha = hps_spec[max_ind - 1]
                beta  = hps_spec[max_ind]
                gamma = hps_spec[max_ind + 1]
                p = 0.5 * (alpha - gamma) / (alpha - 2 * beta + gamma)
            true_index = max_ind + p
            detected_freq = true_index * (SAMPLE_FREQ / WINDOW_SIZE) / NUM_HPS

            # Discard out-of-range frequencies.
            if detected_freq < MIN_FREQ or detected_freq > MAX_FREQ:
                continue

            # --- Smooth the detected frequency using a median filter ---
            if len(freqBuffer) < FREQ_BUFFER_SIZE:
                freqBuffer.append(detected_freq)
            else:
                freqBuffer.pop(0)
                freqBuffer.append(detected_freq)
            smoothed_freq = np.median(freqBuffer)

            # --- Find the closest musical note ---
            stable_note, stable_pitch = find_closest_note(smoothed_freq)
            latest_closest_note = stable_note
            latest_freq         = round(smoothed_freq, 1)
            latest_ideal_freq   = round(stable_pitch, 1)

    return

# =============================================================================
# Tuner Control Functions
# =============================================================================
def tuner_on():

    global client, running, proc_thread, processing_buffer, freqBuffer
    global latest_closest_note, latest_freq, latest_ideal_freq

    os.environ['JACK_PROMISCUOUS_SERVER'] = 'jack'

    processing_buffer = np.zeros(0, dtype=np.float32)
    freqBuffer = []
    latest_closest_note = ""
    latest_freq         = 0.0
    latest_ideal_freq   = 0.0

    gc.disable()  # Disable GC in realtime context.

    running = True
    client = jack.Client("HPS_Tuner", no_start_server=True)
    client.inports.register("input")
    client.set_process_callback(jack_callback)
    client.activate()

    capture_port = "system:capture_1"
    try:
        client.connect(capture_port, client.inports[0])
    except jack.JackError as err:
        print("Error while connecting to", capture_port, ":", err)

    proc_thread = threading.Thread(target=processing_thread)
    proc_thread.start()

def tuner_off():

    global running, proc_thread, client
    running = False
    if proc_thread is not None:
        proc_thread.join()
    if client is not None:
        client.deactivate()
        client.close()
        client = None

# =============================================================================
# Main: Run the Tuner
# =============================================================================
if __name__ == "__main__":
    try:
        tuner_on()
        # Print detected note and frequency every 0.5 seconds.
        while True:
            print("Detected:", latest_closest_note, latest_freq, "Hz, Ideal:", latest_ideal_freq, "Hz")
            time.sleep(0.5)
    except KeyboardInterrupt:
        tuner_off()
