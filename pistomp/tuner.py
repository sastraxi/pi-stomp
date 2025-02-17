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
import time
import math
import board
import digitalio
import busio
import subprocess
from adafruit_rgb_display import ili9341
from PIL import Image, ImageDraw, ImageFont
import pistomp.lcd320x240 as lcd
import common.token as Token
import common.util as Util
import logging
from pistomp.footswitch import Footswitch, LongpressInfo
from pistomp.switchstate import Value

class Tuner:

    def __init__(self, handler=None, callback=None):
        self.callback = callback
        self.handler = handler
        self.enabled = False
        self.encoder_exit_requested = False
        # =============================================================================
        # Configuration Constants
        # =============================================================================
        self.SAMPLE_FREQ        = 48000          # sample frequency in Hz
        self.WINDOW_SIZE        = 48000          # FFT window size in samples
        self.WINDOW_STEP        = 12000          # number of samples to slide the window each time
        self.NUM_HPS            = 5              # maximum number of harmonic product spectrums
        self.POWER_THRESH       = 1e-5           # skip processing if the signal power is below this
        self.CONCERT_PITCH      = 440            # A4 = 440 Hz
        self.WHITE_NOISE_THRESH = 0.8            # fraction for noise suppression

        # Frequency resolution:
        self.DELTA_FREQ = self.SAMPLE_FREQ / self.WINDOW_SIZE

        # Octave bands (Hz) for noise suppression:
        self.OCTAVE_BANDS = [50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600]

        # Note names:
        self.ALL_NOTES = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"]

        # Only consider detected frequencies in this range:
        self.MIN_FREQ = 80    
        self.MAX_FREQ = 1200  

        # Pre-calculate a Hann window (float32 for pyFFTW)
        self.HANN_WINDOW = np.hanning(self.WINDOW_SIZE).astype(np.float32)

        # =============================================================================
        # Globals for Threading and Buffering
        # =============================================================================
        self.sample_queue = queue.Queue(maxsize=200)  # Thread-safe queue for audio blocks
        self.processing_buffer = np.zeros(0, dtype=np.float32)  # Rolling buffer for accumulating samples
        self.FREQ_BUFFER_SIZE = 8  # For smoothing detected frequencies
        self.freqBuffer = []

        self.proc_thread = None
        self.client      = None

        self.latest_closest_note = ""  # e.g., "A4"
        self.latest_freq         = 0.0 # Detected (smoothed) frequency in Hz
        self.latest_ideal_freq   = 0.0 # Ideal frequency for that note in Hz

        # ---------------------------
        # Global Display Initialization
        # ---------------------------
        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        tft_cs = digitalio.DigitalInOut(board.CE0)    # Chip select on board.CE0
        tft_dc = digitalio.DigitalInOut(board.D6)       # Data/command on board.D6
        reset_pin = digitalio.DigitalInOut(board.D5)    # Reset on board.D5

        self.display = ili9341.ILI9341(spi, cs=tft_cs, dc=tft_dc, rst=reset_pin, rotation=90)
        self.width, self.height = 320, 240
    # =============================================================================
    # Helper Function: Note Detection
    # =============================================================================
    def find_closest_note(self, pitch):
        """
        Given a pitch (in Hz), compute the closest musical note and its ideal frequency.
        """
        i = int(np.round(np.log2(pitch / self.CONCERT_PITCH) * 12))
        closest_note = self.ALL_NOTES[i % 12] + str(4 + (i + 9) // 12)
        closest_pitch = self.CONCERT_PITCH * 2 ** (i / 12)
        return closest_note, closest_pitch

    # =============================================================================
    # JACK Callback (Realtime Thread)
    # =============================================================================
    def jack_callback(self, frames):
        """
        Minimal realtime JACK callback.
        Simply obtains the input block and enqueues it.
        (Keep this as lightweight as possible.)
        """
        try:
            data = self.client.inports[0].get_array()  # Get mono input as a numpy array.
            self.sample_queue.put_nowait(data)         # Drop block if queue is full.
        except queue.Full:
            pass
        return

    # =============================================================================
    # Processing Thread (Non-Realtime)
    # =============================================================================
    def processing_thread(self):
        """
        Non-realtime thread that pulls audio blocks from the queue, accumulates them
        in a rolling buffer, and processes FFT windows. Uses pyFFTW and further 
        precomputed constants for better performance.
        """

        gc.enable()  # Enable garbage collection in this thread.

        # Create pyFFTW aligned arrays and plan.
        in_array = pyfftw.empty_aligned(self.WINDOW_SIZE, dtype='float32')
        out_array = pyfftw.empty_aligned(self.WINDOW_SIZE // 2 + 1, dtype='complex64')
        fft_object = pyfftw.FFTW(in_array, out_array, direction='FFTW_FORWARD',
                                flags=('FFTW_MEASURE',))

        # Precompute constant indices:
        spec_size = self.WINDOW_SIZE // 2 + 1
        num_bins_mains = int(62 / self.DELTA_FREQ)  # Bins to zero out for mains hum

        # Precompute octave band index ranges (start, end) as tuples.
        octave_indices = []
        for j in range(len(self.OCTAVE_BANDS) - 1):
            start_idx = int(self.OCTAVE_BANDS[j] / self.DELTA_FREQ)
            end_idx = int(self.OCTAVE_BANDS[j+1] / self.DELTA_FREQ)
            octave_indices.append((start_idx, min(end_idx, spec_size)))
        
        # Precompute interpolation grid for the magnitude spectrum.
        interp_x = np.arange(0, spec_size, 1 / self.NUM_HPS)

        while self.enabled:
            try:
                # Wait briefly for a new audio block.
                new_block = self.sample_queue.get(timeout=0.01)
                # Append the new block (Note: if profiling shows this concatenation to be a bottleneck,
                # consider implementing a pre-allocated ring buffer to avoid repeated allocations).
                self.processing_buffer = np.concatenate((self.processing_buffer, new_block))
            except queue.Empty:
                continue

            # Process as long as there's at least one full window.
            while self.processing_buffer.size >= self.WINDOW_SIZE:
                window = self.processing_buffer[:self.WINDOW_SIZE]
                self.processing_buffer = self.processing_buffer[self.WINDOW_STEP:]

                # Compute power (using vectorized np.sum).
                signal_power = np.sum(window ** 2) / self.WINDOW_SIZE
                if signal_power < self.POWER_THRESH:
                    continue

                # Apply Hann window.
                hann_samples = window * self.HANN_WINDOW

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
                    mask = segment < (self.WHITE_NOISE_THRESH * avg_energy)
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
                for i in range(self.NUM_HPS):
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
                detected_freq = true_index * (self.SAMPLE_FREQ / self.WINDOW_SIZE) / self.NUM_HPS

                # Discard out-of-range frequencies.
                if detected_freq < self.MIN_FREQ or detected_freq > self.MAX_FREQ:
                    continue

                # --- Smooth the detected frequency using a median filter ---
                if len(self.freqBuffer) < self.FREQ_BUFFER_SIZE:
                    self.freqBuffer.append(detected_freq)
                else:
                    self.freqBuffer.pop(0)
                    self.freqBuffer.append(detected_freq)
                smoothed_freq = np.median(self.freqBuffer)

                # --- Find the closest musical note ---
                stable_note, stable_pitch = self.find_closest_note(smoothed_freq)
                self.latest_closest_note = stable_note
                self.latest_freq         = round(smoothed_freq, 1)
                self.latest_ideal_freq   = round(stable_pitch, 1)

        return

    # =============================================================================
    # Tuner Control Functions
    # =============================================================================
    def tuner_on(self, args):

        os.environ['JACK_PROMISCUOUS_SERVER'] = 'jack'

        self.processing_buffer = np.zeros(0, dtype=np.float32)
        self.freqBuffer = []
        self.latest_closest_note = ""
        self.latest_freq         = 0.0
        self.latest_ideal_freq   = 0.0

        gc.disable()  # Disable GC in realtime context.

        self.enabled = True
        self.client = jack.Client("HPS_Tuner", no_start_server=True)
        self.client.inports.register("input")
        self.client.set_process_callback(self.jack_callback)
        self.client.activate()

        self.capture_port = "system:capture_1"
        try:
            self.client.connect(self.capture_port, self.client.inports[0])
        except jack.JackError as err:
            logging.error("Error while connecting to", self.capture_port, ":", err)

        cmd = "amixer -c %d -q -- sset '%s' '%s'" % (0, 'DAC Soft Mute', 'on')
        try:
            subprocess.check_output(cmd, shell=True)
        except subprocess.CalledProcessError:
            logging.error("Failed trying to set audio card parameter")
            return False

        self.proc_thread = threading.Thread(target=self.processing_thread)
        self.proc_thread.start()
        self.run_ui()

    def tuner_off(self):

        self.enabled = False
        if self.proc_thread is not None:
            self.proc_thread.join()
        if self.client is not None:
            self.client.deactivate()
            self.client.close()
            cmd = "amixer -c %d -q -- sset '%s' '%s'" % (0, 'DAC Soft Mute', 'off')
            try:
                subprocess.check_output(cmd, shell=True)
            except subprocess.CalledProcessError:
                logging.error("Failed trying to set audio card parameter")
                return False
            self.client = None

    def clear_display(self):
        """Clears the display by showing a black screen."""
        black = Image.new("RGB", (self.width, self.height), "black")
        self.display.image(black)

    # ---------------------------
    # Create a smooth gradient background with dithering.
    # ---------------------------
    def create_smooth_gradient_background(self, width, height, top_color=(5,5,10), bottom_color=(0,0,0), scale=4, noise_amount=8):
        high_res_height = height * scale
        high_res = Image.new("RGB", (width, high_res_height), top_color)
        draw = ImageDraw.Draw(high_res)
        for y in range(high_res_height):
            t = y / (high_res_height - 1)
            r = int(top_color[0] * (1-t) + bottom_color[0] * t)
            g = int(top_color[1] * (1-t) + bottom_color[1] * t)
            b = int(top_color[2] * (1-t) + bottom_color[2] * t)
            draw.line([(0, y), (width, y)], fill=(r, g, b))
        # Add random noise for dithering.
        arr = np.array(high_res, dtype=np.int16)
        noise = np.random.randint(-noise_amount, noise_amount+1, arr.shape, dtype=np.int16)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        high_res_with_noise = Image.fromarray(arr, "RGB")
        return high_res_with_noise.resize((width, height), Image.LANCZOS)

    # ---------------------------
    # Create Static Gauge Layer.
    # ---------------------------
    def create_static_layer(self, background, cx, cy, R, arc_start, arc_end, tick_length, small_font):
        """
        Returns an Image that has the static background and gauge elements drawn:
        - Gradient background (provided by background).
        - Gauge arc and tick marks with labels.
        """
        static_img = background.copy()
        draw = ImageDraw.Draw(static_img)
        # Draw gauge arc.
        bbox = (cx - R, cy - R, cx + R, cy + R)
        draw.arc(bbox, start=arc_start, end=arc_end, fill="white", width=5)
        # Draw tick marks and labels.
        tolerance = 5.0  # used for mapping -5 to +5 Hz to angles.
        for diff in range(-5, 6):
            tick_angle = 270 + (diff / tolerance) * 60  # Map -5 Hz to 210°, +5 Hz to 330°.
            theta_tick = math.radians(tick_angle)
            tick_start_x = cx + R * math.cos(theta_tick)
            tick_start_y = cy + R * math.sin(theta_tick)
            tick_end_x = cx + (R - tick_length) * math.cos(theta_tick)
            tick_end_y = cy + (R - tick_length) * math.sin(theta_tick)
            draw.line((tick_start_x, tick_start_y, tick_end_x, tick_end_y), fill="white", width=2)
            label = f"{diff:+d}"
            label_radius = R + 15
            label_x = cx + label_radius * math.cos(theta_tick)
            label_y = cy + label_radius * math.sin(theta_tick)
            bbox_label = draw.textbbox((0, 0), label, font=small_font)
            lw = bbox_label[2] - bbox_label[0]
            lh = bbox_label[3] - bbox_label[1]
            draw.text((label_x - lw/2, label_y - lh/2), label, font=small_font, fill="white")
        return static_img

    def exit_ui_on_encoder_longpress(self, *args, **kwargs):
        self.encoder_exit_requested
        logging.debug("exit_ui_on_encoder_longpress called with args: %s, kwargs: %s", args, kwargs)
        self.encoder_exit_requested = True

    # ---------------------------
    # Main UI Loop
    # ---------------------------
    def run_ui(self):
        # Create smooth gradient background.
        background = self.create_smooth_gradient_background(self.width, self.height, top_color=(0,0,5),
                                                    bottom_color=(0,0,0), scale=4, noise_amount=8)
        
        # Load fonts.
        try:
            large_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 55)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        except Exception:
            large_font = ImageFont.load_default()
            small_font = ImageFont.load_default()

        # Gauge parameters.
        cx, cy = 160, 160           # Center of gauge.
        R = 140                     # Radius (bounding box: (20,20,300,300))
        arc_start, arc_end = 210, 330 # Arc spans from 210° to 330°.
        needle_length = R - 16      # Approximately 120 pixels.
        tolerance = 5.0             # ±5 Hz maps to ±60° deviation (ideal = 270°).
        tick_length = 10            # Tick mark length.

        # Precompute the static gauge layer.
        static_layer = self.create_static_layer(background, cx, cy, R, arc_start, arc_end, tick_length, small_font)

        # Clearing Logic.
        clear_timeout = 4       # seconds.
        change_threshold = 0.1  # Hz.
        last_note = None
        last_freq = None
        last_change_time = time.time()

        def update_display(new_note, new_freq):
            nonlocal last_note, last_freq, last_change_time
            current_time = time.time()
            if last_freq is None:
                last_freq = new_freq
                last_note = new_note
                last_change_time = current_time
                return new_note
            if abs(new_freq - last_freq) > change_threshold or new_note != last_note:
                last_freq = new_freq
                last_note = new_note
                last_change_time = current_time
                return new_note
            else:
                if current_time - last_change_time > clear_timeout:
                    return ""
                else:
                    return new_note

        fixed_freq_text_y = 224  # Fixed vertical position for frequency text.

        # Smoothing Parameters.
        smoothing_factor = 0.2
        smoothed_freq = None
        smoothed_ideal = None

        self.enabled = True

        try:
            hw = globals().get("hw", None)
            if hw is None:
                raise ImportError("Global hw not set")
            logging.debug("Overriding encoder switch longpress callbacks using global hw.")
            # Force the encoder switch id to 1 and override its longpress_callback for the one with id==1.
            for enc_sw in hw.encoder_switches:
                enc_sw.id = 1
                logging.debug("Setting encoder switch id to %d", enc_sw.id)
                if enc_sw.id == 1:
                    enc_sw.longpress_callback = self.exit_ui_on_encoder_longpress
                    logging.debug("Longpress callback for encoder id 1 overridden.")
            # Longpress and longpress groups
            Footswitch.callbacks[Token.TUNER] = self.exit_ui_on_encoder_longpress
            logging.debug("Longpress callback for footswitch with token TUNER overridden.")
        except Exception as e:
            hw = None
            logging.error("Hardware instance 'hw' not available; encoder/footswitch longpress exit not enabled: %s", e)

        while self.enabled:
            # Start with a copy of the precomputed static layer.
            image = static_layer.copy()
            draw = ImageDraw.Draw(image)

            # Retrieve Tuner Data.
            note_val = self.latest_closest_note  # e.g., "A4"
            freq = self.latest_freq              # Raw frequency in Hz.
            ideal_freq = self.latest_ideal_freq  # Ideal frequency in Hz.
            display_note = update_display(note_val, freq)

            # Smooth Frequency Values.
            if smoothed_freq is None:
                smoothed_freq = freq
                smoothed_ideal = ideal_freq
            else:
                smoothed_freq = smoothing_factor * freq + (1 - smoothing_factor) * smoothed_freq
                smoothed_ideal = smoothing_factor * ideal_freq + (1 - smoothing_factor) * smoothed_ideal

            if display_note == "":
                smoothed_freq = freq
                smoothed_ideal = ideal_freq

            # Compute Needle Angle and Color.
            if display_note == "":
                angle = 270
                needle_color = "white"
                nx = cx
                ny = cy - needle_length
            else:
                diff_val = freq - ideal_freq
                diff_val = max(-tolerance, min(tolerance, diff_val))
                angle = 270 + (diff_val / tolerance) * 60
                needle_color = "green" if abs(freq - ideal_freq) < 0.2 else "red"
                theta = math.radians(angle)
                nx = int(round(cx + needle_length * math.cos(theta)))
                ny = int(round(cy + needle_length * math.sin(theta)))
            draw.line((cx, cy, nx, ny), fill=needle_color, width=5)

            # Add a white dot at the pivot (base) of the needle.
            base_radius = 4
            base_bbox = (cx - base_radius, cy - base_radius, cx + base_radius, cy + base_radius)
            draw.ellipse(base_bbox, fill="white")

            # Draw Note Text (with Drop Shadow).
            note_center = (cx, cy + 24)
            if display_note:
                text_color = "green" if abs(freq - ideal_freq) < 0.2 else "red"
                bbox_text = draw.textbbox((0, 0), display_note, font=large_font)
                text_w = bbox_text[2] - bbox_text[0]
                text_h = bbox_text[3] - bbox_text[1]
                text_x = note_center[0] - text_w // 2
                text_y = note_center[1] - text_h // 2
                draw.text((text_x + 2, text_y + 2), display_note, font=large_font, fill="gray")
                draw.text((text_x, text_y), display_note, font=large_font, fill=text_color)

            # Draw Frequency Readout.
            if display_note == "":
                freq_text = ""
            else:
                freq_text = f"{smoothed_freq:.1f} Hz"
            if freq_text:
                bbox_freq = draw.textbbox((0, 0), freq_text, font=small_font)
                freq_w = bbox_freq[2] - bbox_freq[0]
                freq_x = cx - freq_w // 2
                freq_y = fixed_freq_text_y
                draw.text((freq_x, freq_y), freq_text, font=small_font, fill="white")

            # --- Poll hardware controls to update encoder switch events ---
            if hw is not None:
                hw.poll_controls()  # Process encoder events which may trigger the longpress callback.

            if self.encoder_exit_requested:
                logging.debug("Encoder exit flag detected; exiting run_ui loop.")
                self.tuner_off()
                self.clear_display()
                self.enabled = False
                self.encoder_exit_requested = False
                break

            self.display.image(image)
            time.sleep(0.01)  # Small delay for responsiveness

    def set_callback(self, callback):
        self.callback = callback

    def enable(self, enable):
        self.enabled = enable

    def is_enabled(self):
        return self.enabled

    def toggle_enable(self):
        self.enabled = not self.enabled