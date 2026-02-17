#!/usr/bin/env python3
"""Display a PNG image on the piStomp ILI9341 320x240 LCD.

Can optionally overlay a status message at the bottom of the screen,
useful for showing boot progress.
"""

import argparse
import sys

import board
import digitalio
from PIL import Image, ImageDraw, ImageFont
from PIL.Image import Resampling
import adafruit_rgb_display.ili9341 as ili9341


LCD_WIDTH = 320
LCD_HEIGHT = 240

SAFE_BAUD = 24 * 1000 * 1000
FAST_BAUD = 80 * 1000 * 1000

MSG_FONT = "DejaVuSans.ttf"
MSG_FONT_SIZE = 22
MSG_COLOR = (255, 255, 255)
MSG_PADDING_BOTTOM = 18


def init_display(baudrate: int) -> ili9341.ILI9341:
    cs = digitalio.DigitalInOut(board.CE0)
    dc = digitalio.DigitalInOut(board.D6)
    spi = board.SPI()
    return ili9341.ILI9341(spi, cs=cs, dc=dc, rst=None, baudrate=baudrate)


def load_and_fit(path: str, stretch: bool) -> Image.Image:
    img = Image.open(path).convert("RGB")
    if stretch:
        return img.resize((LCD_WIDTH, LCD_HEIGHT), Resampling.LANCZOS)
    # Fit within bounds, letterbox with black
    img.thumbnail((LCD_WIDTH, LCD_HEIGHT), Resampling.LANCZOS)
    canvas = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (0, 0, 0))
    x = (LCD_WIDTH - img.width) // 2
    y = (LCD_HEIGHT - img.height) // 2
    canvas.paste(img, (x, y))
    return canvas


def draw_message(img: Image.Image, message: str) -> None:
    """Draw a centered status message at the bottom of the image."""
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(MSG_FONT, MSG_FONT_SIZE)
    bbox = font.getbbox(message)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (LCD_WIDTH - text_w) // 2
    y = LCD_HEIGHT - text_h - MSG_PADDING_BOTTOM
    draw.text((x, y), message, font=font, fill=MSG_COLOR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Show an image on the piStomp LCD")
    parser.add_argument("image", help="Path to PNG/JPG image")
    parser.add_argument("--message", "-m", help="Status text to display at the bottom of the screen")
    parser.add_argument("--stretch", action="store_true", help="Stretch to fill (ignore aspect ratio)")
    parser.add_argument(
        "--rotation", type=int, choices=[90, 270], default=90, help="Display rotation (default: 90, use 270 if flipped)"
    )
    parser.add_argument("--baudrate", type=int, default=FAST_BAUD, help="SPI baudrate in Hz (default: 80MHz)")
    args = parser.parse_args()

    try:
        img = load_and_fit(args.image, args.stretch)
    except Exception as e:
        print(f"Error loading image: {e}", file=sys.stderr)
        sys.exit(1)

    if args.message:
        draw_message(img, args.message)

    disp = init_display(args.baudrate)
    disp.image(img, args.rotation)


if __name__ == "__main__":
    main()
