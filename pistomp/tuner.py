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

import copy
import os
import numpy as np
import jack
import time
import queue
import threading
import gc

# =============================================================================
# Configuration Constants
# =============================================================================
SAMPLE_FREQ        = 48000          # sample frequency in Hz
WINDOW_SIZE        = 48000          # FFT window size in samples
WINDOW_STEP        = 12000          # number of samples to slide the window each time
NUM_HPS            = 5              # maximum number of harmonic product spectrums
POWER_THRESH       = 1e-6           # skip processing if the signal power is below this
CONCERT_PITCH      = 440            # A4 = 440 Hz
WHITE_NOISE_THRESH = 0.2            # fraction for noise suppression

# Frequency resolution:
DELTA_FREQ = SAMPLE_FREQ / WINDOW_SIZE

# Octave bands (Hz) for noise suppression:
OCTAVE_BANDS = [50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600]

# Note names:
ALL_NOTES = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]

# Only consider detected frequencies in this range:
MIN_FREQ = 80    
MAX_FREQ = 1200  

# Pre-calculate a Hann window to reduce spectral leakage:
HANN_WINDOW = np.hanning(WINDOW_SIZE)

# =============================================================================
# Globals for Threading and Buffering
# =============================================================================
# A thread-safe queue for passing audio blocks from the JACK realtime callback.
sample_queue = queue.Queue(maxsize=100)

# A rolling buffer to accumulate enough samples for processing.
processing_buffer = np.zeros(0, dtype=np.float32)

# A buffer to smooth detected frequency values.
FREQ_BUFFER_SIZE = 8
freqBuffer = []

# Global flags and thread/client handles.
running    = False
proc_thread = None
client      = None

# Global variables for sharing the latest tuning results.
latest_closest_note = ""  # e.g., "A4"
latest_freq         = 0.0 # the detected (smoothed) frequency in Hz
latest_ideal_freq   = 0.0 # the ideal frequency for that note in Hz

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
        # Get the mono input as a numpy array.
        data = client.inports[0].get_array()
        sample_queue.put_nowait(data)  # If full, the block is dropped.
    except queue.Full:
        pass
    return

# =============================================================================
# Processing Thread (Non-Realtime)
# =============================================================================
def processing_thread():
    """
    Non-realtime thread that pulls audio blocks from the queue, accumulates them
    in a rolling buffer, and performs heavy processing (FFT, HPS, etc.) when enough
    samples have been collected.
    """
    global processing_buffer, freqBuffer, running
    global latest_closest_note, latest_freq, latest_ideal_freq

    # Enable garbage collection in this thread.
    gc.enable()
    while running:
        try:
            # Wait briefly for a new audio block.
            new_block = sample_queue.get(timeout=0.01)
            processing_buffer = np.concatenate((processing_buffer, new_block))
        except queue.Empty:
            continue

        # Process windows as long as there are enough samples.
        while len(processing_buffer) >= WINDOW_SIZE:
            window = processing_buffer[:WINDOW_SIZE]
            processing_buffer = processing_buffer[WINDOW_STEP:]

            # Skip processing if the signal power is too low.
            signal_power = np.linalg.norm(window, ord=2)**2 / len(window)
            if signal_power < POWER_THRESH:
                continue

            # Apply Hann window.
            hann_samples = window * HANN_WINDOW

            # Compute FFT using rfft (for real-valued input).
            fft_result = np.fft.rfft(hann_samples)
            magnitude_spec = np.abs(fft_result)

            # Suppress mains hum by zeroing frequencies below ~62 Hz.
            for i in range(int(62 / DELTA_FREQ)):
                if i < len(magnitude_spec):
                    magnitude_spec[i] = 0

            # Suppress low-level noise in each octave band.
            for j in range(len(OCTAVE_BANDS) - 1):
                ind_start = int(OCTAVE_BANDS[j] / DELTA_FREQ)
                ind_end   = int(OCTAVE_BANDS[j+1] / DELTA_FREQ)
                if ind_end > len(magnitude_spec):
                    ind_end = len(magnitude_spec)
                avg_energy = np.linalg.norm(magnitude_spec[ind_start:ind_end], ord=2)**2 / (ind_end - ind_start)
                avg_energy = np.sqrt(avg_energy)
                for i in range(ind_start, ind_end):
                    if magnitude_spec[i] < WHITE_NOISE_THRESH * avg_energy:
                        magnitude_spec[i] = 0

            # Interpolate the magnitude spectrum for finer resolution.
            interp_x = np.arange(0, len(magnitude_spec), 1 / NUM_HPS)
            mag_spec_ipol = np.interp(interp_x,
                                      np.arange(0, len(magnitude_spec)),
                                      magnitude_spec)
            norm = np.linalg.norm(mag_spec_ipol, ord=2)
            if norm != 0:
                mag_spec_ipol = mag_spec_ipol / norm

            # Compute the Harmonic Product Spectrum (HPS).
            hps_spec = copy.deepcopy(mag_spec_ipol)
            for i in range(NUM_HPS):
                tmp_len = int(np.ceil(len(mag_spec_ipol) / (i+1)))
                tmp_hps_spec = np.multiply(hps_spec[:tmp_len], mag_spec_ipol[::(i+1)])
                if not np.any(tmp_hps_spec):
                    break
                hps_spec = tmp_hps_spec

            # Find the peak in the HPS spectrum.
            max_ind = np.argmax(hps_spec)

            # Parabolic interpolation to refine the frequency estimate.
            if max_ind <= 0 or max_ind >= len(hps_spec)-1:
                p = 0.0
            else:
                alpha = hps_spec[max_ind-1]
                beta  = hps_spec[max_ind]
                gamma = hps_spec[max_ind+1]
                p = 0.5 * (alpha - gamma) / (alpha - 2*beta + gamma)
            true_index = max_ind + p
            detected_freq = true_index * (SAMPLE_FREQ / WINDOW_SIZE) / NUM_HPS

            # Discard frequencies outside the expected range.
            if detected_freq < MIN_FREQ or detected_freq > MAX_FREQ:
                continue

            # Smooth the detected frequency using a median filter.
            if len(freqBuffer) < FREQ_BUFFER_SIZE:
                freqBuffer.append(detected_freq)
            else:
                freqBuffer.pop(0)
                freqBuffer.append(detected_freq)
            smoothed_freq = np.median(freqBuffer)

            stable_note, stable_pitch = find_closest_note(smoothed_freq)

            # Update the global variables.
            latest_closest_note = stable_note
            latest_freq         = round(smoothed_freq, 1)
            latest_ideal_freq   = round(stable_pitch, 1)

            # Optionally, clear the terminal and print the result.
            #os.system('cls' if os.name == 'nt' else 'clear')
            print(
                f"Closest note: {latest_closest_note}  "
                f"(freq: {latest_freq} Hz, ideal: {latest_ideal_freq} Hz)"
            )
    return

# =============================================================================
# Tuner Control Functions
# =============================================================================
def tuner_on():
    """
    Start the tuner:
      - Set the required environment variable.
      - Create the JACK client and register ports.
      - Set the realtime callback.
      - Activate the client and connect to the desired input.
      - Start the processing thread.
    """
    global client, running, proc_thread, processing_buffer, freqBuffer

    # Set the environment variable as required.
    os.environ['JACK_PROMISCUOUS_SERVER'] = 'jack'

    # Clear any previous state.
    processing_buffer = np.zeros(0, dtype=np.float32)
    freqBuffer = []
    latest_closest_note = ""
    latest_freq         = 0.0
    latest_ideal_freq   = 0.0

    # Disable garbage collection in the realtime callback (to reduce jitter).
    gc.disable()

    running = True
    client = jack.Client("HPS_Tuner", no_start_server=True)
    client.inports.register("input")
    client.set_process_callback(jack_callback)
    client.activate()

    capture_port = "system:capture_1"
    try:
        client.connect(capture_port, client.inports[0])
        print("Connected to input port:", capture_port)
    except jack.JackError as err:
        print("Error while connecting to", capture_port, ":", err)

    proc_thread = threading.Thread(target=processing_thread)
    proc_thread.start()
    print("Tuner is on.")

def tuner_off():
    """
    Shut down the tuner:
      - Signal the processing thread to exit and join it.
      - Deactivate and close the JACK client.
    """
    global running, proc_thread, client
    running = False
    if proc_thread is not None:
        proc_thread.join()
    if client is not None:
        client.deactivate()
        client.close()
        client = None
    print("Tuner is off.")

# =============================================================================
# Example usage when run as a script
# =============================================================================
if __name__ == "__main__":
    tuner_on()
    try:
        # Run indefinitely until interrupted.
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        tuner_off()
