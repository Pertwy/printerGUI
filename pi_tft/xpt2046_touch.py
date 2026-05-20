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
"""

from __future__ import annotations

import os
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

    def close(self) -> None:
        try:
            self._spi.close()
        except Exception:
            pass

    def _read_adc12(self, cmd: int) -> int:
        # Three-byte transfer; 12-bit result in bits from bytes 1–2 (controller-dependent layout).
        raw = self._spi.xfer2([cmd, 0x00, 0x00])
        return ((raw[1] & 0x7F) << 5) | (raw[2] >> 3)

    def read_raw(self) -> tuple[int, int]:
        samples_x: list[int] = []
        samples_y: list[int] = []
        for _ in range(4):
            samples_x.append(self._read_adc12(self._CMD_X))
            samples_y.append(self._read_adc12(self._CMD_Y))
        rx = sum(samples_x) // len(samples_x)
        ry = sum(samples_y) // len(samples_y)
        return rx, ry

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
