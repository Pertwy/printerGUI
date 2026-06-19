#!/usr/bin/env python3
"""XPT2046 resistive touch reader for the Hailege 2.4" ILI9341 panel.

The touch controller lives on the Pi's auxiliary SPI1 bus (see pi_tft/SETUP.md):
  T_CLK -> GPIO21 (pin 40)   T_DIN -> GPIO20 (pin 38)   T_DO -> GPIO19 (pin 35)
  T_CS  -> GPIO18 (pin 12, SPI1 CE0 -> /dev/spidev1.0)
  T_IRQ -> GPIO26 (pin 37, optional)

This module is intentionally dependency-light: it talks to the chip directly via
`spidev` (already required by the project) and only uses RPi.GPIO for the optional
IRQ line. It is shared by `print_page_tft.py` and `touch_calibrate.py`.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass

# XPT2046 12-bit differential read control bytes.
_CMD_X = 0xD0
_CMD_Y = 0x90
_CMD_Z1 = 0xB0
_CMD_Z2 = 0xC0

DEFAULT_BUS = 1
DEFAULT_DEVICE = 0
DEFAULT_IRQ_GPIO = 26
DEFAULT_MAX_SPEED_HZ = 1_000_000
# Pressure metric (z1 + 4095 - z2) above this means "pen down".
DEFAULT_Z_THRESHOLD = 400

_CAL_FILENAME = "touch_cal.json"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


@dataclass
class TouchCalibration:
    """Maps raw XPT2046 ADC values to the rotate=1 (320x240) pixel space."""

    min_x: int = 200
    max_x: int = 3900
    min_y: int = 200
    max_y: int = 3900
    swap_xy: bool = True
    invert_x: bool = False
    invert_y: bool = True

    @classmethod
    def load(cls, script_dir: str | None = None) -> "TouchCalibration":
        """Load calibration from touch_cal.json (if present), then env overrides."""
        cal = cls()
        if script_dir is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        cal_path = os.path.join(script_dir, _CAL_FILENAME)
        if os.path.exists(cal_path):
            try:
                with open(cal_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                cal = cls(
                    min_x=int(data.get("min_x", cal.min_x)),
                    max_x=int(data.get("max_x", cal.max_x)),
                    min_y=int(data.get("min_y", cal.min_y)),
                    max_y=int(data.get("max_y", cal.max_y)),
                    swap_xy=bool(data.get("swap_xy", cal.swap_xy)),
                    invert_x=bool(data.get("invert_x", cal.invert_x)),
                    invert_y=bool(data.get("invert_y", cal.invert_y)),
                )
            except (ValueError, OSError):
                pass

        cal.min_x = _env_int("TOUCH_MIN_X", cal.min_x)
        cal.max_x = _env_int("TOUCH_MAX_X", cal.max_x)
        cal.min_y = _env_int("TOUCH_MIN_Y", cal.min_y)
        cal.max_y = _env_int("TOUCH_MAX_Y", cal.max_y)
        cal.swap_xy = _env_bool("TOUCH_SWAP_XY", cal.swap_xy)
        cal.invert_x = _env_bool("TOUCH_INVERT_X", cal.invert_x)
        cal.invert_y = _env_bool("TOUCH_INVERT_Y", cal.invert_y)
        return cal

    def save(self, script_dir: str | None = None) -> str:
        if script_dir is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
        cal_path = os.path.join(script_dir, _CAL_FILENAME)
        with open(cal_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, indent=2)
        return cal_path


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


class XPT2046Touch:
    """Minimal XPT2046 reader using spidev (+ optional IRQ via RPi.GPIO)."""

    def __init__(
        self,
        bus: int | None = None,
        device: int | None = None,
        irq_gpio: int | None = None,
        max_speed_hz: int = DEFAULT_MAX_SPEED_HZ,
        z_threshold: int | None = None,
        calibration: TouchCalibration | None = None,
        samples: int = 5,
    ) -> None:
        import spidev  # imported here so the module imports cleanly off-Pi

        self.bus = DEFAULT_BUS if bus is None else bus
        self.device = DEFAULT_DEVICE if device is None else device
        self.samples = max(1, samples)
        self.z_threshold = (
            _env_int("TOUCH_Z_THRESHOLD", DEFAULT_Z_THRESHOLD)
            if z_threshold is None
            else z_threshold
        )
        self.calibration = calibration or TouchCalibration.load()

        self._spi = spidev.SpiDev()
        self._spi.open(self.bus, self.device)
        self._spi.max_speed_hz = max_speed_hz
        self._spi.mode = 0

        if irq_gpio is None:
            irq_gpio = _env_int("TOUCH_IRQ_GPIO", DEFAULT_IRQ_GPIO)
        self.irq_gpio = irq_gpio if irq_gpio and irq_gpio > 0 else None
        self._gpio = None
        if self.irq_gpio is not None:
            try:
                import RPi.GPIO as GPIO  # provided by rpi-lgpio on modern Pi OS

                GPIO.setmode(GPIO.BCM)
                GPIO.setup(self.irq_gpio, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                self._gpio = GPIO
            except Exception:
                # IRQ is optional; fall back to pressure-only detection.
                self._gpio = None
                self.irq_gpio = None

    def _read_adc(self, command: int) -> int:
        response = self._spi.xfer2([command, 0x00, 0x00])
        return ((response[1] << 8) | response[2]) >> 3

    def _read_channel(self, command: int) -> int:
        return _median([self._read_adc(command) for _ in range(self.samples)])

    def _irq_pressed(self) -> bool | None:
        """True/False from the IRQ line, or None if no IRQ wired."""
        if self._gpio is None or self.irq_gpio is None:
            return None
        return self._gpio.input(self.irq_gpio) == 0

    def _pressure(self) -> int:
        z1 = self._read_adc(_CMD_Z1)
        z2 = self._read_adc(_CMD_Z2)
        return z1 + (4095 - z2)

    def read_raw(self) -> tuple[int, int, int] | None:
        """Return (raw_x, raw_y, pressure) when touched, else None.

        Detection is pressure-based so it works whether or not T_IRQ is wired.
        """
        pressure = self._pressure()
        if pressure < self.z_threshold:
            return None

        raw_x = self._read_channel(_CMD_X)
        raw_y = self._read_channel(_CMD_Y)
        if raw_x <= 0 or raw_y <= 0:
            return None
        return raw_x, raw_y, pressure

    def debug_sample(self) -> dict:
        """Raw readings for diagnostics (ignores the pressure gate)."""
        z1 = self._read_adc(_CMD_Z1)
        z2 = self._read_adc(_CMD_Z2)
        return {
            "z1": z1,
            "z2": z2,
            "pressure": z1 + (4095 - z2),
            "x": self._read_channel(_CMD_X),
            "y": self._read_channel(_CMD_Y),
            "irq": self._irq_pressed(),
            "threshold": self.z_threshold,
        }

    def get_touch(self, width: int, height: int) -> tuple[int, int] | None:
        """Return (px, py) in the rotate=1 display space, or None if not touched."""
        raw = self.read_raw()
        if raw is None:
            return None
        raw_x, raw_y, _ = raw
        cal = self.calibration

        span_x = (cal.max_x - cal.min_x) or 1
        span_y = (cal.max_y - cal.min_y) or 1
        nx = _clamp((raw_x - cal.min_x) / span_x, 0.0, 1.0)
        ny = _clamp((raw_y - cal.min_y) / span_y, 0.0, 1.0)
        if cal.invert_x:
            nx = 1.0 - nx
        if cal.invert_y:
            ny = 1.0 - ny

        if cal.swap_xy:
            px = int(ny * (width - 1))
            py = int(nx * (height - 1))
        else:
            px = int(nx * (width - 1))
            py = int(ny * (height - 1))
        return px, py

    def close(self) -> None:
        try:
            self._spi.close()
        except Exception:
            pass
        if self._gpio is not None and self.irq_gpio is not None:
            try:
                self._gpio.cleanup(self.irq_gpio)
            except Exception:
                pass


def wait_for_release(touch: XPT2046Touch, timeout: float = 5.0) -> None:
    """Block until the panel reports no touch (used by the calibration helper)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if touch.read_raw() is None:
            return
        time.sleep(0.02)
