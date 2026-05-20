"""
XPT2046 resistive touch controller over SPI (common on ILI9341 SPI modules).

Typical wiring (BCM GPIO numbers, not header pin numbers):
  T_CLK  -> SPI0 SCLK (GPIO 11)  — shared with display
  T_DIN  -> SPI0 MOSI (GPIO 10) — shared
  T_DO   -> SPI0 MISO (GPIO 9)  — shared
  T_CS   -> SPI0 CE1  (GPIO 7)  — use spidev port=0 device=1 (default)
  T_IRQ  -> any free GPIO, active LOW when finger down (internal pull-up)

If your T_CS is not on CE1, wire it to GPIO 7 / CE1 or add a device-tree fragment;
this driver opens a separate spidev device so it does not share the luma display fd.

Environment (optional, all have sensible defaults):
  TFT_TOUCH_SPI_PORT     default 0
  TFT_TOUCH_SPI_DEVICE   default 1  (CE1)
  TFT_TOUCH_SPI_MAX_HZ  default 2000000
  TFT_TOUCH_IRQ_GPIO     default 17 (set to your T_IRQ BCM pin)
  TFT_TOUCH_IRQ_ACTIVE   "low" or "high" — level when screen is touched
  TFT_TOUCH_XMIN, TFT_TOUCH_XMAX, TFT_TOUCH_YMIN, TFT_TOUCH_YMAX  raw ADC calibration
  TFT_TOUCH_SWAP_XY      "1" to swap X/Y after calibration
  TFT_TOUCH_INVERT_X     "1"
  TFT_TOUCH_INVERT_Y     "1"
  TFT_TOUCH_POLL         "1" = also detect touch via SPI pressure (works when IRQ is flaky)
  TFT_TOUCH_USE_IRQ      "1" = use T_IRQ GPIO (default 17)
  TFT_TOUCH_DEBUG        "1" = print raw coords / actions to stderr
  TFT_TOUCH_PRESSURE_MIN default 400 (raw Z threshold)
  TFT_TOUCH_HIT_PAD      pixels to expand button hit boxes (in print_page_tft)
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    return int(raw, 0)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class TouchPoint:
    """Pixel coordinates on the TFT, origin top-left."""

    x: int
    y: int


class XPT2046:
    """Minimal XPT2046 reader using spidev (MODE0)."""

    # Control bytes: 12-bit, single-ended, differential off — matches common Arduino libs.
    _CMD_X = 0xD0
    _CMD_Y = 0x90
    _CMD_Z = 0xB0

    def __init__(
        self,
        *,
        spi_port: int | None = None,
        spi_device: int | None = None,
        max_speed_hz: int | None = None,
    ) -> None:
        import spidev

        self._spi: spidev.SpiDev = spidev.SpiDev()
        port = _env_int("TFT_TOUCH_SPI_PORT", 0) if spi_port is None else spi_port
        device = _env_int("TFT_TOUCH_SPI_DEVICE", 1) if spi_device is None else spi_device
        hz = _env_int("TFT_TOUCH_SPI_MAX_HZ", 2_000_000) if max_speed_hz is None else max_speed_hz
        self._spi.open(port, device)
        self._spi.max_speed_hz = max(500_000, hz)
        self._spi.mode = 0

        self.xmin = _env_int("TFT_TOUCH_XMIN", 200)
        self.xmax = _env_int("TFT_TOUCH_XMAX", 3900)
        self.ymin = _env_int("TFT_TOUCH_YMIN", 200)
        self.ymax = _env_int("TFT_TOUCH_YMAX", 3900)
        self.swap_xy = _env_bool("TFT_TOUCH_SWAP_XY", False)
        self.invert_x = _env_bool("TFT_TOUCH_INVERT_X", False)
        self.invert_y = _env_bool("TFT_TOUCH_INVERT_Y", False)
        self.pressure_min = _env_int("TFT_TOUCH_PRESSURE_MIN", 400)
        self.poll_enabled = _env_bool("TFT_TOUCH_POLL", True)
        self.irq_enabled = _env_bool("TFT_TOUCH_USE_IRQ", True)
        self.debug = _env_bool("TFT_TOUCH_DEBUG", False)
        self._spi_port = port
        self._spi_device = device

    def close(self) -> None:
        try:
            self._spi.close()
        except Exception:
            pass

    def _read_adc12(self, cmd: int) -> int:
        # Two-byte transfer (common on Pi modules).
        raw = self._spi.xfer2([cmd, 0x00])
        value = ((raw[0] << 8) | raw[1]) >> 4
        if value <= 0 or value >= 4095:
            # Fallback layout used by some luma / legacy examples.
            raw3 = self._spi.xfer2([cmd, 0x00, 0x00])
            value = ((raw3[1] & 0x7F) << 5) | (raw3[2] >> 3)
        return value & 0xFFF

    def read_pressure(self) -> int:
        return self._read_adc12(self._CMD_Z)

    def read_raw(self) -> tuple[int, int]:
        samples_x: list[int] = []
        samples_y: list[int] = []
        for _ in range(3):
            samples_x.append(self._read_adc12(self._CMD_X))
            samples_y.append(self._read_adc12(self._CMD_Y))
        samples_x.sort()
        samples_y.sort()
        rx = samples_x[len(samples_x) // 2]
        ry = samples_y[len(samples_y) // 2]
        return rx, ry

    def is_finger_down(self) -> bool:
        """Detect touch from SPI even when T_IRQ does not change."""
        z = self.read_pressure()
        if z >= self.pressure_min:
            return True
        rx, ry = self.read_raw()
        # Idle panels often read ~0 or saturated; a finger usually lands in mid-range.
        return (
            80 < rx < 4080
            and 80 < ry < 4080
            and not (rx < 150 and ry < 150)
        )

    def read_pixel(self, width: int, height: int) -> TouchPoint | None:
        rx, ry = self.read_raw()
        if self.swap_xy:
            rx, ry = ry, rx
        xr = self.xmax - self.xmin
        yr = self.ymax - self.ymin
        if xr <= 0 or yr <= 0:
            return None
        nx = (rx - self.xmin) / xr
        ny = (ry - self.ymin) / yr
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny
        x = int(nx * (width - 1))
        y = int(ny * (height - 1))
        return TouchPoint(x=x, y=y)

    def log_config(self) -> None:
        print(
            f"Touch: /dev/spidev{self._spi_port}.{self._spi_device} "
            f"IRQ={'on' if self.irq_enabled else 'off'} "
            f"poll={'on' if self.poll_enabled else 'off'} "
            f"cal=({self.xmin},{self.xmax},{self.ymin},{self.ymax})",
            file=sys.stderr,
        )


def open_optional_xpt2046() -> XPT2046 | None:
    """Return configured XPT2046 or None if disabled / unavailable."""
    flag = (os.environ.get("TFT_TOUCH_ENABLE") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return None
    try:
        return XPT2046()
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Touch SPI device missing (e.g. /dev/spidev0.1). "
            "Wire T_CS to CE1 (GPIO 7) or set TFT_TOUCH_SPI_PORT / TFT_TOUCH_SPI_DEVICE. "
            "Enable SPI in raspi-config."
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"Could not open XPT2046 SPI: {exc}") from exc


def setup_irq_gpio() -> tuple[int, bool]:
    """
    Configure T_IRQ as input with pull-up.
    Returns (bcm_pin, touched_when_low).
    """
    import RPi.GPIO as GPIO

    irq_pin = _env_int("TFT_TOUCH_IRQ_GPIO", 17)
    active = (os.environ.get("TFT_TOUCH_IRQ_ACTIVE") or "low").strip().lower()
    touched_when_low = active != "high"

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(irq_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    return irq_pin, touched_when_low


def irq_is_pressed(irq_pin: int, touched_when_low: bool) -> bool:
    import RPi.GPIO as GPIO

    level = GPIO.input(irq_pin)
    if touched_when_low:
        return level == GPIO.LOW
    return level == GPIO.HIGH


def cleanup_irq_gpio(irq_pin: int) -> None:
    try:
        import RPi.GPIO as GPIO

        GPIO.cleanup(irq_pin)
    except Exception:
        pass


def touch_input_active(
    touch: XPT2046,
    irq_pin: int | None,
    touched_when_low: bool,
) -> bool:
    """True if IRQ and/or SPI polling says the panel is being touched."""
    if touch.irq_enabled and irq_pin is not None:
        if irq_is_pressed(irq_pin, touched_when_low):
            return True
    if touch.poll_enabled:
        return touch.is_finger_down()
    return False


def _diag_loop() -> None:
    load_env = None
    try:
        from print_page_tft import load_env_file
    except ImportError:
        pass
    else:
        load_env = load_env_file
    if load_env:
        load_env()
    touch = open_optional_xpt2046()
    if touch is None:
        raise SystemExit("Touch disabled (TFT_TOUCH_ENABLE=0)")
    touch.log_config()
    irq_pin = None
    touched_when_low = True
    if touch.irq_enabled:
        irq_pin, touched_when_low = setup_irq_gpio()
    print("Touch diagnostic — press the screen (Ctrl+C to quit)", file=sys.stderr)
    try:
        while True:
            irq = (
                irq_is_pressed(irq_pin, touched_when_low)
                if irq_pin is not None
                else False
            )
            poll = touch.is_finger_down()
            if irq or poll:
                rx, ry = touch.read_raw()
                z = touch.read_pressure()
                pt = touch.read_pixel(320, 240)
                px = pt.x if pt else -1
                py = pt.y if pt else -1
                print(f"irq={irq} poll={poll} raw=({rx},{ry}) z={z} pixel=({px},{py})", file=sys.stderr)
            time.sleep(0.08)
    except KeyboardInterrupt:
        pass
    finally:
        if irq_pin is not None:
            cleanup_irq_gpio(irq_pin)
        touch.close()


if __name__ == "__main__":
    _diag_loop()
