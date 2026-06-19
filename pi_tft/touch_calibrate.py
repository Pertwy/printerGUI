#!/usr/bin/env python3
"""Interactive touch calibration for the XPT2046 panel.

Run on the Pi (from the project venv) after wiring the touch controller and
enabling SPI1:

    source ~/Desktop/printerGUI/.venv/bin/activate
    python3 pi_tft/touch_calibrate.py

It draws a target in each corner of the TFT. Tap each target firmly with a
stylus. When all four are captured it computes the min/max raw values and the
orientation flags (swap/invert) for the rotate=1 display, writes them to
pi_tft/touch_cal.json, and prints equivalent TOUCH_* env vars.
"""
from __future__ import annotations

import time

from luma.core.interface.serial import spi
from luma.lcd.device import ili9341
from PIL import Image, ImageDraw, ImageFont

try:
    from xpt2046_touch import TouchCalibration, XPT2046Touch, _median, wait_for_release
except ImportError:  # running from project root
    from pi_tft.xpt2046_touch import (
        TouchCalibration,
        XPT2046Touch,
        _median,
        wait_for_release,
    )

INSET = 24
SAMPLE_WINDOW_SECONDS = 0.4


def draw_target(device, font, px: int, py: int, message: str) -> None:
    img = Image.new("RGB", device.size, "#000000")
    draw = ImageDraw.Draw(img)
    arm = 12
    draw.line((px - arm, py, px + arm, py), fill="#6ec1ff", width=2)
    draw.line((px, py - arm, px, py + arm), fill="#6ec1ff", width=2)
    draw.ellipse((px - 6, py - 6, px + 6, py + 6), outline="#ffffff", width=2)
    draw.text((6, 6), message, fill="#ffffff", font=font)
    device.display(img)


def capture_point(touch: XPT2046Touch) -> tuple[int, int]:
    """Wait for a press, return the median raw (x, y), then wait for release."""
    while True:
        while touch.read_raw() is None:
            time.sleep(0.01)

        xs: list[int] = []
        ys: list[int] = []
        end = time.time() + SAMPLE_WINDOW_SECONDS
        while time.time() < end:
            raw = touch.read_raw()
            if raw is not None:
                xs.append(raw[0])
                ys.append(raw[1])
            time.sleep(0.01)

        wait_for_release(touch)
        if xs and ys:
            return _median(xs), _median(ys)
        # Spurious touch: retry.


def _axis_range(s0: int, r0: int, s1: int, r1: int, s_max: int) -> tuple[int, int, bool]:
    """Extrapolate raw values at screen positions 0 and s_max.

    Returns (lo, hi, invert) where lo/hi are min/max raw and invert is True when
    the raw value decreases as the screen coordinate increases.
    """
    if s1 == s0:
        lo, hi = sorted((r0, r1))
        return lo, hi, r0 > r1
    slope = (r1 - r0) / (s1 - s0)
    raw_at_0 = r0 - slope * s0
    raw_at_max = r1 + slope * (s_max - s1)
    invert = raw_at_0 > raw_at_max
    lo, hi = sorted((int(round(raw_at_0)), int(round(raw_at_max))))
    return lo, hi, invert


def main() -> None:
    serial = spi(port=0, device=0, gpio_DC=24, gpio_RST=25)
    device = ili9341(serial, rotate=1)
    width, height = device.size
    font = ImageFont.load_default()

    try:
        touch = XPT2046Touch()
    except Exception as exc:
        raise SystemExit(
            f"Could not open touch controller: {exc}\n"
            "Check that SPI1 is enabled (dtoverlay=spi1-1cs) and /dev/spidev1.0 exists,\n"
            "and that the touch pins are wired (see pi_tft/SETUP.md)."
        )

    targets = [
        ("tl", INSET, INSET, "Tap TOP-LEFT target"),
        ("tr", width - 1 - INSET, INSET, "Tap TOP-RIGHT target"),
        ("bl", INSET, height - 1 - INSET, "Tap BOTTOM-LEFT target"),
        ("br", width - 1 - INSET, height - 1 - INSET, "Tap BOTTOM-RIGHT target"),
    ]
    raw: dict[str, tuple[int, int]] = {}
    for key, px, py, message in targets:
        draw_target(device, font, px, py, message)
        raw[key] = capture_point(touch)
        print(f"{key}: raw={raw[key]}")
        time.sleep(0.3)

    rx_tl, ry_tl = raw["tl"]
    rx_tr, ry_tr = raw["tr"]
    rx_bl, ry_bl = raw["bl"]

    # Decide whether the raw X axis tracks screen-X (horizontal) or screen-Y.
    horiz_dx = abs(rx_tr - rx_tl)
    vert_dx = abs(rx_bl - rx_tl)
    swap_xy = vert_dx > horiz_dx

    px_inset_lo = INSET
    if swap_xy:
        # raw_x tracks screen Y; raw_y tracks screen X.
        min_x, max_x, invert_x = _axis_range(
            px_inset_lo, rx_tl, height - 1 - INSET, rx_bl, height - 1
        )
        min_y, max_y, invert_y = _axis_range(
            px_inset_lo, ry_tl, width - 1 - INSET, ry_tr, width - 1
        )
    else:
        # raw_x tracks screen X; raw_y tracks screen Y.
        min_x, max_x, invert_x = _axis_range(
            px_inset_lo, rx_tl, width - 1 - INSET, rx_tr, width - 1
        )
        min_y, max_y, invert_y = _axis_range(
            px_inset_lo, ry_tl, height - 1 - INSET, ry_bl, height - 1
        )

    cal = TouchCalibration(
        min_x=min_x,
        max_x=max_x,
        min_y=min_y,
        max_y=max_y,
        swap_xy=swap_xy,
        invert_x=invert_x,
        invert_y=invert_y,
    )
    cal_path = cal.save()

    img = Image.new("RGB", device.size, "#003300")
    draw = ImageDraw.Draw(img)
    draw.text((8, 8), "Calibration saved!", fill="#ffffff", font=font)
    draw.text((8, 24), "Restart the print UI.", fill="#ffffff", font=font)
    device.display(img)

    print("\nSaved calibration to", cal_path)
    print("Equivalent environment variables:")
    print(f"  export TOUCH_MIN_X={cal.min_x}")
    print(f"  export TOUCH_MAX_X={cal.max_x}")
    print(f"  export TOUCH_MIN_Y={cal.min_y}")
    print(f"  export TOUCH_MAX_Y={cal.max_y}")
    print(f"  export TOUCH_SWAP_XY={'1' if cal.swap_xy else '0'}")
    print(f"  export TOUCH_INVERT_X={'1' if cal.invert_x else '0'}")
    print(f"  export TOUCH_INVERT_Y={'1' if cal.invert_y else '0'}")

    touch.close()


if __name__ == "__main__":
    main()
