#!/usr/bin/env python3
"""
Focused test to find the maximum stable SPI speed for the ILI9341 LCD.
Tests incrementally and reports which speeds work.
"""

import time
import board
import digitalio
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.ili9341 as ili9341


def test_speed(speed_hz, iterations=10):
    """Test a specific SPI speed."""
    try:
        # Initialize display at this speed
        spi = board.SPI()
        cs_pin = digitalio.DigitalInOut(board.CE0)
        dc_pin = digitalio.DigitalInOut(board.D6)
        reset_pin = digitalio.DigitalInOut(board.D5)

        disp = ili9341.ILI9341(
            spi,
            cs=cs_pin,
            dc=dc_pin,
            rst=reset_pin,
            baudrate=speed_hz
        )

        # Create test pattern with some complexity
        img = Image.new('RGB', (320, 240), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw some shapes to test rendering
        draw.rectangle([10, 10, 310, 230], outline=(255, 255, 255), width=3)
        draw.rectangle([50, 50, 150, 150], fill=(255, 0, 0))
        draw.rectangle([170, 50, 270, 150], fill=(0, 255, 0))
        draw.ellipse([100, 160, 220, 220], fill=(0, 0, 255))

        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 24)
            draw.text((80, 100), f"{speed_hz//1_000_000}MHz", font=font, fill=(255, 255, 255))
        except:
            pass  # Font not critical for test

        # Warmup
        disp.image(img, 270)
        time.sleep(0.05)

        # Benchmark
        start = time.perf_counter()
        for i in range(iterations):
            disp.image(img, 270)
        elapsed = time.perf_counter() - start

        # Calculate metrics
        avg_time = elapsed / iterations
        max_fps = 1.0 / avg_time
        bytes_per_frame = 320 * 240 * 2  # RGB565
        effective_speed = (bytes_per_frame * iterations * 8) / elapsed

        # Clean up
        cs_pin.deinit()
        dc_pin.deinit()
        reset_pin.deinit()

        return {
            'success': True,
            'avg_ms': avg_time * 1000,
            'max_fps': max_fps,
            'effective_mhz': effective_speed / 1e6,
            'error': None
        }

    except Exception as e:
        return {
            'success': False,
            'avg_ms': None,
            'max_fps': None,
            'effective_mhz': None,
            'error': str(e)
        }


def main():
    print("\n" + "=" * 70)
    print("ILI9341 LCD SPI Speed Test - piStomp")
    print("=" * 70)
    print("\nTesting SPI speeds to find maximum stable configuration...")
    print("This will take about 2 minutes.\n")

    # Test speeds from conservative to aggressive
    speeds = [
        12_000_000,   # 12 MHz - very conservative
        16_000_000,   # 16 MHz - Adafruit default
        24_000_000,   # 24 MHz - ILI9341 datasheet typical max
        32_000_000,   # 32 MHz - pushing it
        40_000_000,   # 40 MHz
        48_000_000,   # 48 MHz - current config
        56_000_000,   # 56 MHz
        64_000_000,   # 64 MHz
        72_000_000,   # 72 MHz
        80_000_000,   # 80 MHz - aggressive
    ]

    results = []
    max_stable = None

    for speed in speeds:
        print(f"Testing {speed//1_000_000:3d} MHz... ", end='', flush=True)
        result = test_speed(speed, iterations=10)
        results.append((speed, result))

        if result['success']:
            print(f"✓ {result['avg_ms']:5.1f}ms/frame  {result['max_fps']:5.1f} FPS max  "
                  f"({result['effective_mhz']:4.1f} MHz effective)")
            max_stable = speed
        else:
            print(f"✗ FAILED - {result['error']}")
            # Once we hit failures, higher speeds won't work
            break

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    if max_stable:
        print(f"\n✓ Maximum stable speed: {max_stable//1_000_000} MHz\n")

        # Show comparison table
        print("Speed Comparison:")
        print("-" * 70)
        print(f"{'Speed':<12} {'ms/frame':<12} {'Max FPS':<12} {'vs 5Hz poll':<15}")
        print("-" * 70)

        for speed, result in results:
            if result['success']:
                speed_str = f"{speed//1_000_000} MHz"
                ms_str = f"{result['avg_ms']:.1f} ms"
                fps_str = f"{result['max_fps']:.1f} FPS"

                # Compare to 200ms polling interval
                if result['avg_ms'] < 200:
                    headroom = 200 / result['avg_ms']
                    vs_str = f"✓ {headroom:.1f}x headroom"
                else:
                    vs_str = "✗ too slow"

                print(f"{speed_str:<12} {ms_str:<12} {fps_str:<12} {vs_str:<15}")

        print("-" * 70)

        # Recommendation
        print("\nRECOMMENDATIONS:")
        print()

        # Find the result for max_stable
        max_result = next(r for s, r in results if s == max_stable and r['success'])

        print(f"1. Current config (48 MHz):")
        result_48 = next((r for s, r in results if s == 48_000_000), None)
        if result_48 and result_48['success']:
            print(f"   - Working: ✓")
            print(f"   - Frame time: {result_48['avg_ms']:.1f} ms")
            print(f"   - Can support up to {result_48['max_fps']:.0f} FPS")
        else:
            print(f"   - Working: ✗")

        print()
        print(f"2. Maximum stable ({max_stable//1_000_000} MHz):")
        print(f"   - Frame time: {max_result['avg_ms']:.1f} ms")
        print(f"   - Can support up to {max_result['max_fps']:.0f} FPS")
        print(f"   - Effective transfer rate: {max_result['effective_mhz']:.1f} MHz")

        if max_stable > 48_000_000:
            speedup = 48_000_000 / max_stable
            print(f"   - {(1/speedup - 1)*100:.0f}% faster than current 48 MHz")
            print(f"\n   → RECOMMEND: Update lcd320x240.py line 48 to {max_stable}")
        elif max_stable == 48_000_000:
            print(f"\n   → Current 48 MHz is already optimal")
        else:
            print(f"\n   → WARNING: Current 48 MHz may be unstable!")
            print(f"   → RECOMMEND: Reduce to {max_stable//1_000_000} MHz")

        # Refresh rate analysis
        print()
        print("3. Refresh rate implications:")
        if max_result['avg_ms'] < 100:
            print(f"   - Full panel refresh can run at 10+ Hz")
        if max_result['avg_ms'] < 50:
            print(f"   - Full panel refresh can run at 20+ Hz")
        if max_result['avg_ms'] < 33:
            print(f"   - Full panel refresh can run at 30+ Hz")
        else:
            print(f"   - For >30 Hz: Need partial widget updates")
            print(f"   - Widget-only refresh should be <10ms")

    else:
        print("\n✗ No stable speeds found - hardware issue?")

    print("\n" + "=" * 70)
    print()


if __name__ == "__main__":
    main()
