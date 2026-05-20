"""
XPT2046 resistive touch controller over SPI (common on ILI9341 SPI modules).

Typical wiring (BCM GPIO numbers):
  T_CLK/MOSI/MISO — shared with display SPI0
  T_CS  — often **GPIO 22** (pin 15), NOT hardware CE1, on 2.4" boards
        — or SPI CE1 / GPIO 7 → /dev/spidev0.1
  T_IRQ — GPIO 17 (pin 11), active LOW when pressed (many boards)

If /dev/spidev0.1 exists but raw reads stay (0,0), set:
  export TFT_TOUCH_CS_GPIO=22
or let auto-probe try common CS pins (22, 27, 16, 5).

Environment:
  TFT_TOUCH_CS_GPIO      BCM pin for T_CS (software chip-select)
  TFT_TOUCH_SPI_PORT     default 0
  TFT_TOUCH_SPI_DEVICE   default 1 when CS_GPIO unset (CE1)
  TFT_TOUCH_AUTO_PROBE   default 1 — try GPIO 22,27,16,5 if CE1 reads zero
  TFT_TOUCH_USE_IRQ      default 0 if IRQ stuck low; set 1 to enable
  TFT_TOUCH_IRQ_GPIO     default 17
  TFT_TOUCH_IRQ_ACTIVE   low | high
  TFT_TOUCH_POLL         default 1
  TFT_TOUCH_*MIN/MAX, SWAP_XY, INVERT_X/Y, DEBUG, PRESSURE_MIN
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass

_spi_lock = threading.Lock()

# Common T_CS BCM pins (physical pin 26 on many 2.4" boards = GPIO 7 / CE1).
_AUTO_CS_GPIO_PINS = (7, 22, 27, 16, 5)


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
    x: int
    y: int


def _valid_raw(rx: int, ry: int, z: int) -> bool:
    if z >= 80:
        return True
    return 80 < rx < 4080 and 80 < ry < 4080 and not (rx < 120 and ry < 120)


class XPT2046:
    """XPT2046 reader: hardware CE (spidev device) or software CS on a GPIO pin."""

    _CMD_X = 0xD0
    _CMD_Y = 0x90
    _CMD_Z = 0xB0

    def __init__(
        self,
        *,
        spi_port: int | None = None,
        spi_device: int | None = None,
        max_speed_hz: int | None = None,
        cs_gpio: int | None = None,
    ) -> None:
        import spidev

        self._cs_gpio = cs_gpio
        port = _env_int("TFT_TOUCH_SPI_PORT", 0) if spi_port is None else spi_port
        device = _env_int("TFT_TOUCH_SPI_DEVICE", 1) if spi_device is None else spi_device
        hz = _env_int("TFT_TOUCH_SPI_MAX_HZ", 1_000_000) if max_speed_hz is None else max_speed_hz

        self._spi: spidev.SpiDev = spidev.SpiDev()
        if cs_gpio is not None:
            # Share SPI bus; assert T_CS on GPIO during transfers (CE0 may also toggle).
            self._spi.open(port, 0)
            self._setup_cs_gpio(cs_gpio)
            self._spi_device = -1
            self._cs_label = f"GPIO{cs_gpio}"
        else:
            self._spi.open(port, device)
            self._spi_device = device
            self._cs_label = f"CE{device}"

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

    def _setup_cs_gpio(self, cs_gpio: int) -> None:
        import RPi.GPIO as GPIO

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(cs_gpio, GPIO.OUT, initial=GPIO.HIGH)

    def _cs_select(self, selected: bool) -> None:
        if self._cs_gpio is None:
            return
        import RPi.GPIO as GPIO

        GPIO.output(self._cs_gpio, GPIO.LOW if selected else GPIO.HIGH)

    def close(self) -> None:
        try:
            if self._cs_gpio is not None:
                self._cs_select(False)
            self._spi.close()
        except Exception:
            pass

    def _xfer(self, data: list[int]) -> list[int]:
        with _spi_lock:
            self._cs_select(True)
            try:
                return self._spi.xfer2(data)
            finally:
                self._cs_select(False)

    def _read_adc12(self, cmd: int) -> int:
        raw = self._xfer([cmd, 0x00])
        value = ((raw[0] << 8) | raw[1]) >> 4
        if value <= 0 or value >= 4095:
            raw3 = self._xfer([cmd, 0x00, 0x00])
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
        return samples_x[len(samples_x) // 2], samples_y[len(samples_y) // 2]

    def is_finger_down(self) -> bool:
        z = self.read_pressure()
        if z >= self.pressure_min:
            return True
        rx, ry = self.read_raw()
        return _valid_raw(rx, ry, z)

    def read_pixel(self, width: int, height: int) -> TouchPoint | None:
        rx, ry = self.read_raw()
        if not _valid_raw(rx, ry, 0):
            return None
        if self.swap_xy:
            rx, ry = ry, rx
        xr = self.xmax - self.xmin
        yr = self.ymax - self.ymin
        if xr <= 0 or yr <= 0:
            return None
        nx = max(0.0, min(1.0, (rx - self.xmin) / xr))
        ny = max(0.0, min(1.0, (ry - self.ymin) / yr))
        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny
        return TouchPoint(x=int(nx * (width - 1)), y=int(ny * (height - 1)))

    def log_config(self) -> None:
        print(
            f"Touch: spidev{self._spi_port} cs={self._cs_label} "
            f"IRQ={'on' if self.irq_enabled else 'off'} poll={'on' if self.poll_enabled else 'off'} "
            f"cal=({self.xmin},{self.xmax},{self.ymin},{self.ymax})",
            file=sys.stderr,
        )


def _sample_touch(touch: XPT2046) -> tuple[int, int, int]:
    rx, ry = touch.read_raw()
    z = touch.read_pressure()
    return rx, ry, z


def _open_with_cs(cs_gpio: int | None, spi_device: int) -> XPT2046:
    if cs_gpio is not None:
        return XPT2046(cs_gpio=cs_gpio, spi_device=0)
    return XPT2046(spi_device=spi_device)


def open_optional_xpt2046() -> XPT2046 | None:
    flag = (os.environ.get("TFT_TOUCH_ENABLE") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return None

    cs_env = (os.environ.get("TFT_TOUCH_CS_GPIO") or "").strip()
    auto_probe = _env_bool("TFT_TOUCH_AUTO_PROBE", True)
    spi_device = _env_int("TFT_TOUCH_SPI_DEVICE", 1)

    candidates: list[tuple[str, int | None, int]] = []
    if cs_env:
        candidates.append((f"GPIO{cs_env}", int(cs_env), 0))
    elif auto_probe:
        # Prefer software CS on GPIO 7 (header pin 26 / CE1) before hardware CE1 only.
        for pin in _AUTO_CS_GPIO_PINS:
            candidates.append((f"GPIO{pin}", pin, 0))
        candidates.append((f"CE{spi_device}", None, spi_device))
    else:
        candidates.append((f"CE{spi_device}", None, spi_device))

    last_exc: Exception | None = None
    for label, cs_gpio, device in candidates:
        try:
            touch = _open_with_cs(cs_gpio, device)
            rx, ry, z = _sample_touch(touch)
            if _valid_raw(rx, ry, z):
                if label != candidates[0][0]:
                    print(f"Touch: using {label} (raw=({rx},{ry}) z={z})", file=sys.stderr)
                return touch
            touch.close()
        except Exception as exc:
            last_exc = exc
            continue

    if cs_env:
        raise RuntimeError(
            f"TFT_TOUCH_CS_GPIO={cs_env} set but no valid touch reads. Check wiring and SPI."
        ) from last_exc

    touch = _open_with_cs(None, spi_device)
    print(
        "Warning: touch SPI still reads (0,0). For T_CS on header pin 26 use: export TFT_TOUCH_CS_GPIO=7",
        file=sys.stderr,
    )
    return touch


def setup_irq_gpio() -> tuple[int, bool]:
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


def detect_irq_stuck_low(irq_pin: int, touched_when_low: bool) -> bool:
    """True if IRQ line appears stuck in the 'pressed' state."""
    if not touched_when_low:
        return False
    pressed = 0
    for _ in range(8):
        if irq_is_pressed(irq_pin, touched_when_low):
            pressed += 1
        time.sleep(0.01)
    return pressed >= 6


def touch_input_active(
    touch: XPT2046,
    irq_pin: int | None,
    touched_when_low: bool,
) -> bool:
    """True only when SPI reports a real touch (IRQ alone is not enough)."""
    if touch.is_finger_down():
        return True
    if touch.irq_enabled and irq_pin is not None and irq_is_pressed(irq_pin, touched_when_low):
        rx, ry = touch.read_raw()
        z = touch.read_pressure()
        return _valid_raw(rx, ry, z)
    return False


def _diag_loop() -> None:
    try:
        from print_page_tft import load_env_file

        load_env_file()
    except ImportError:
        pass

    touch = open_optional_xpt2046()
    if touch is None:
        raise SystemExit("Touch disabled (TFT_TOUCH_ENABLE=0)")
    touch.log_config()
    irq_pin = None
    touched_when_low = True
    if touch.irq_enabled:
        irq_pin, touched_when_low = setup_irq_gpio()
        if detect_irq_stuck_low(irq_pin, touched_when_low):
            print(
                "Warning: T_IRQ (GPIO 17) looks stuck LOW — ignoring IRQ; using SPI only. "
                "Or try: export TFT_TOUCH_USE_IRQ=0",
                file=sys.stderr,
            )
            touch.irq_enabled = False

    print("Touch diagnostic — press the screen (Ctrl+C to quit)", file=sys.stderr)
    try:
        while True:
            rx, ry = touch.read_raw()
            z = touch.read_pressure()
            irq = (
                irq_is_pressed(irq_pin, touched_when_low)
                if irq_pin is not None and touch.irq_enabled
                else False
            )
            poll = _valid_raw(rx, ry, z)
            if poll or irq:
                pt = touch.read_pixel(320, 240)
                px = pt.x if pt else -1
                py = pt.y if pt else -1
                print(f"irq={irq} spi={poll} raw=({rx},{ry}) z={z} pixel=({px},{py})", file=sys.stderr)
            time.sleep(0.08)
    except KeyboardInterrupt:
        pass
    finally:
        if irq_pin is not None:
            cleanup_irq_gpio(irq_pin)
        touch.close()


if __name__ == "__main__":
    _diag_loop()
