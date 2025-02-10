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

import time
import math
import board
import digitalio
import busio
import os
from adafruit_rgb_display import ili9341
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import pistomp.tuner as tuner # Your tuner module
import common.token as Token

# ---------------------------
# Global Display Initialization
# ---------------------------
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
tft_cs = digitalio.DigitalInOut(board.CE0)    # Chip select on board.CE0
tft_dc = digitalio.DigitalInOut(board.D6)       # Data/command on board.D6
reset_pin = digitalio.DigitalInOut(board.D5)    # Reset on board.D5

display = ili9341.ILI9341(spi, cs=tft_cs, dc=tft_dc, rst=reset_pin, rotation=90)
width, height = 320, 240

def clear_display():
    """Clears the display by showing a black screen."""
    black = Image.new("RGB", (width, height), "black")
    display.image(black)

# ---------------------------
# Create a smooth gradient background with dithering.
# ---------------------------
def create_smooth_gradient_background(width, height, top_color=(20,20,40), bottom_color=(0,0,0), scale=4, noise_amount=8):
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
def create_static_layer(background, cx, cy, R, arc_start, arc_end, tick_length, small_font):
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

# ---------------------------
# Main UI Loop
# ---------------------------
def run_ui():
    # Create smooth gradient background.
    background = create_smooth_gradient_background(width, height, top_color=(20,20,40), bottom_color=(0,0,0), scale=4, noise_amount=8)
    
    # Load fonts.
    try:
        large_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 55)
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        large_font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    # Gauge parameters.
    cx, cy = 160, 160           # Center of gauge.
    R = 140                     # Radius (so bounding box is (20,20,300,300))
    arc_start, arc_end = 210, 330 # Arc spans from 210° to 330°.
    needle_length = R - 16      # Approximately 120 pixels.
    tolerance = 5.0             # ±5 Hz maps to ±60° deviation (ideal = 270°).
    tick_length = 10            # Tick mark length.

    # Precompute the static gauge layer.
    static_layer = create_static_layer(background, cx, cy, R, arc_start, arc_end, tick_length, small_font)

    # Clearing Logic.
    clear_timeout = 4      # seconds.
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

    while True:
        # Start with a copy of the precomputed static layer.
        image = static_layer.copy()
        draw = ImageDraw.Draw(image)

        # Retrieve Tuner Data.
        note_val = tuner.latest_closest_note  # e.g., "A4"
        freq = tuner.latest_freq              # Raw frequency in Hz.
        ideal_freq = tuner.latest_ideal_freq  # Ideal frequency in Hz.
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
            # Force needle to center exactly.
            angle = 270
            needle_color = "white"
            nx = cx
            ny = cy - needle_length  # Perfectly vertical.
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

        # Draw Note Text (with Drop Shadow) as before.
        note_center = (cx, cy + 24)  # (160,192)
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

        display.image(image)

if __name__ == "__main__":
    clear_display()          # Clear display at startup.
    tuner.tuner_on()         # Start the tuner.
    try:
        run_ui()
    except KeyboardInterrupt:
        tuner.tuner_off()    # Turn off tuner.
        clear_display()      # Clear display after tuner is turned off.
