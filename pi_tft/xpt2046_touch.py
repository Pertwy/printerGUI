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

# Shared with display updates in print_page_tft.py (same SPI0 bus).
spi_bus_lock = threading.Lock()
_spi_lock = spi_bus_lock

# BCM pins that are hardware SPI chip-selects — use spidev CE, never GPIO.output().
_HARDWARE_CE_BCM_TO_DEVICE: dict[int, int] = {
    8: 0,  # CE0 — display CS (header pin 24 on your board)
    7: 1,  # CE1 — touch T_CS (header pin 26)
}
# Software T_CS on a free GPIO (try if CE1 reads stay zero).
_AUTO_CS_GPIO_PINS = (22, 27, 16, 5)


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
        hz = _env_int("TFT_TOUCH_SPI_MAX_HZ", 500_000) if max_speed_hz is None else max_speed_hz
        spi_mode = _env_int("TFT_TOUCH_SPI_MODE", 0)

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

        self._spi.max_speed_hz = max(100_000, min(hz, 2_000_000))
        self._spi.mode = spi_mode & 3

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
        if cs_gpio in _HARDWARE_CE_BCM_TO_DEVICE:
            raise RuntimeError(
                f"BCM GPIO {cs_gpio} is hardware SPI CE{_HARDWARE_CE_BCM_TO_DEVICE[cs_gpio]} — "
                f"do not use as TFT_TOUCH_CS_GPIO. Use TFT_TOUCH_SPI_DEVICE="
                f"{_HARDWARE_CE_BCM_TO_DEVICE[cs_gpio]} instead."
            )
        import RPi.GPIO as GPIO

        GPIO.setmode(GPIO.BCM)
        try:
            GPIO.setup(cs_gpio, GPIO.OUT, initial=GPIO.HIGH)
        except Exception as exc:
            if "busy" in str(exc).lower():
                raise RuntimeError(
                    f"GPIO {cs_gpio} is busy (often a hardware SPI CE pin). "
                    f"If T_CS is on header pin 26, use: export TFT_TOUCH_SPI_DEVICE=1 "
                    f"(and unset TFT_TOUCH_CS_GPIO)."
                ) from exc
            raise

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

    def _spi_transaction(self, data: list[int]) -> list[int]:
        """One CS assertion for the whole transfer (required for hardware CE1)."""
        with spi_bus_lock:
            self._cs_select(True)
            try:
                return self._spi.xfer2(data)
            finally:
                self._cs_select(False)

    @staticmethod
    def _decode_adc12(raw: list[int], offset: int) -> int:
        """Try common XPT2046 byte layouts from a multi-byte SPI reply."""
        if offset + 2 >= len(raw):
            return 0
        candidates = [
            ((raw[offset] << 8) | raw[offset + 1]) >> 4,
            ((raw[offset + 1] & 0x7F) << 5) | (raw[offset + 2] >> 3) if offset + 2 < len(raw) else 0,
            ((raw[offset] << 8) | raw[offset + 1]) >> 3,
        ]
        for value in candidates:
            value &= 0xFFF
            if 50 < value < 4080:
                return value
        return candidates[0] & 0xFFF

    def _read_axes_once(self) -> tuple[int, int, int, list[int]]:
        """
        Read Y, X, Z in one SPI transaction (one CS window).
        Returns (rx, ry, z, raw_bytes).
        """
        raw = self._spi_transaction(
            [
                self._CMD_Y,
                0x00,
                0x00,
                self._CMD_X,
                0x00,
                0x00,
                self._CMD_Z,
                0x00,
                0x00,
            ]
        )
        ry = self._decode_adc12(raw, 1)
        rx = self._decode_adc12(raw, 4)
        z = self._decode_adc12(raw, 7)
        if not _valid_raw(rx, ry, z):
            vals = []
            for i in range(max(0, len(raw) - 2)):
                v = self._decode_adc12(raw, i)
                if 80 < v < 4080:
                    vals.append(v)
            if len(vals) >= 2:
                vals.sort()
                ry, rx = vals[-2], vals[-1]
            elif len(vals) == 1:
                ry = vals[0]
        if self.debug and not _valid_raw(rx, ry, z):
            print(f"touch raw bytes: {[hex(b) for b in raw]}", file=sys.stderr)
        return rx, ry, z, raw

    def read_pressure(self) -> int:
        _, _, z, _ = self._read_axes_once()
        return z

    def read_raw(self) -> tuple[int, int]:
        xs: list[int] = []
        ys: list[int] = []
        for _ in range(3):
            rx, ry, _, _ = self._read_axes_once()
            xs.append(rx)
            ys.append(ry)
            time.sleep(0.001)
        xs.sort()
        ys.sort()
        return xs[len(xs) // 2], ys[len(ys) // 2]

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


def _open_touch(bcm_cs: int | None, spi_device: int) -> XPT2046:
    """
    Open touch controller.
    bcm_cs: BCM pin for T_CS, or None for default spi_device (hardware CE).
    """
    if bcm_cs is not None and bcm_cs in _HARDWARE_CE_BCM_TO_DEVICE:
        dev = _HARDWARE_CE_BCM_TO_DEVICE[bcm_cs]
        return XPT2046(cs_gpio=None, spi_device=dev)
    if bcm_cs is not None:
        return XPT2046(cs_gpio=bcm_cs, spi_device=0)
    return XPT2046(spi_device=spi_device)


def _candidate_list(cs_env: str, auto_probe: bool, spi_device: int) -> list[tuple[str, int | None, int]]:
    """(label, bcm_cs pin or None, spidev device when bcm_cs is None)."""
    out: list[tuple[str, int | None, int]] = []
    if cs_env:
        bcm = int(cs_env, 0)
        if bcm in _HARDWARE_CE_BCM_TO_DEVICE:
            dev = _HARDWARE_CE_BCM_TO_DEVICE[bcm]
            out.append((f"CE{dev} (BCM{bcm})", bcm, dev))
        else:
            out.append((f"GPIO{bcm}", bcm, 0))
        return out
    if auto_probe:
        # CE0 first: many 2.4" boards wire touch CS to the same line as LCD CS (pin 24).
        out.append(("CE0 (shared with display CS, pin 24)", None, 0))
        out.append(("CE1 (BCM7, header pin 26)", 7, 1))
        for pin in _AUTO_CS_GPIO_PINS:
            out.append((f"GPIO{pin}", pin, 0))
        return out
    out.append((f"CE{spi_device}", None, spi_device))
    return out


def _raw_has_signal(raw: list[int]) -> bool:
    return any(b != 0 for b in raw)


def _probe_config(
    label: str,
    bcm_cs: int | None,
    device: int,
    *,
    verbose: bool,
) -> tuple[int, XPT2046 | None, str]:
    """
    Try one CS wiring. Returns (score, touch instance or None, detail line).
    score > 0 means some SPI response; higher is better.
    """
    best_score = 0
    best_touch: XPT2046 | None = None
    best_detail = ""
    for spi_mode in (0, 3):
        holder: XPT2046 | None = None
        try:
            holder = _open_touch(bcm_cs, device)
            holder._spi.mode = spi_mode
            for _ in range(6):
                rx, ry, z, raw = holder._read_axes_once()
                score = sum(1 for b in raw if b != 0)
                if _valid_raw(rx, ry, z):
                    score += 100
                if score > best_score:
                    best_score = score
                    if best_touch is not None and best_touch is not holder:
                        best_touch.close()
                    best_touch = holder
                    best_detail = (
                        f"{label} mode={spi_mode} raw={[hex(b) for b in raw]} "
                        f"parsed=({rx},{ry}) z={z}"
                    )
                time.sleep(0.02)
            if holder is not None and holder is not best_touch:
                holder.close()
        except Exception as exc:
            if verbose:
                print(f"  {label} mode={spi_mode}: ERROR {exc}", file=sys.stderr)
            if holder is not None and holder is not best_touch:
                try:
                    holder.close()
                except Exception:
                    pass
    if verbose:
        status = "OK" if best_score > 0 else "no SPI data (all 0x00)"
        print(f"  {label}: {status}" + (f" — {best_detail}" if best_detail else ""), file=sys.stderr)
    return best_score, best_touch, best_detail


def scan_touch_bus(*, verbose: bool | None = None) -> tuple[XPT2046 | None, str]:
    """
    Try CE0, CE1, and GPIO chip-selects; return the best working config.
    Press the screen during the first few seconds for best results.
    """
    if verbose is None:
        verbose = _env_bool("TFT_TOUCH_SCAN", True)

    cs_env = (os.environ.get("TFT_TOUCH_CS_GPIO") or "").strip()
    auto_probe = _env_bool("TFT_TOUCH_AUTO_PROBE", True)
    spi_device = _env_int("TFT_TOUCH_SPI_DEVICE", 1)
    candidates = _candidate_list(cs_env, auto_probe, spi_device)

    if verbose:
        print(
            "Touch bus scan — lightly press the screen now (3s)...",
            file=sys.stderr,
        )

    winner: XPT2046 | None = None
    winner_label = ""
    winner_score = 0
    winner_detail = ""

    for label, bcm_cs, device in candidates:
        score, touch, detail = _probe_config(label, bcm_cs, device, verbose=verbose)
        if score > winner_score and touch is not None:
            if winner is not None:
                winner.close()
            winner = touch
            winner_label = label
            winner_score = score
            winner_detail = detail
        elif touch is not None:
            touch.close()

    if winner is not None:
        print(f"Touch: selected {winner_label} — {winner_detail}", file=sys.stderr)
        return winner, winner_label

    if verbose:
        print(
            "\nTouch scan: every config returned 0x00 on MISO. Checklist:\n"
            "  1) In /boot/firmware/config.txt avoid 'dtoverlay=spi0-1cs' (needs 2 CE lines).\n"
            "  2) Many boards tie T_CS to LCD CS (pin 24) not pin 26 — CE0 should then work.\n"
            "  3) Confirm MISO (GPIO 9) is connected on the module flex.\n"
            "  4) Run scan while pressing: TFT_TOUCH_SCAN=1 python3 pi_tft/xpt2046_touch.py\n",
            file=sys.stderr,
        )
    return None, ""


def open_optional_xpt2046() -> XPT2046 | None:
    flag = (os.environ.get("TFT_TOUCH_ENABLE") or "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return None

    cs_env = (os.environ.get("TFT_TOUCH_CS_GPIO") or "").strip()
    spi_device = _env_int("TFT_TOUCH_SPI_DEVICE", 1)

    touch, _label = scan_touch_bus(verbose=_env_bool("TFT_TOUCH_SCAN", True))
    if touch is not None:
        return touch

    if cs_env:
        raise RuntimeError(
            f"TFT_TOUCH_CS_GPIO={cs_env} — no SPI response on any tested CS line. "
            "See scan checklist printed above."
        )

    # Last resort: open requested device so diagnostics can still run.
    touch = _open_touch(None, spi_device)
    print(
        f"Warning: touch not detected; using CE{spi_device} anyway (expect raw=0). "
        "Try: export TFT_TOUCH_SPI_DEVICE=0 for shared LCD/T_CS wiring.",
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

    print("=== Touch bus scan ===", file=sys.stderr)
    touch, _ = scan_touch_bus(verbose=True)
    if touch is None:
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

    always = _env_bool("TFT_TOUCH_DIAG_ALWAYS", True)
    print(
        "Touch diagnostic — press screen / buttons (Ctrl+C to quit). "
        "Shows every sample while TFT_TOUCH_DIAG_ALWAYS=1.",
        file=sys.stderr,
    )
    try:
        while True:
            rx, ry, z, raw = touch._read_axes_once()
            irq = (
                irq_is_pressed(irq_pin, touched_when_low)
                if irq_pin is not None and touch.irq_enabled
                else False
            )
            poll = _valid_raw(rx, ry, z)
            if always or poll or irq or touch.debug:
                pt = touch.read_pixel(320, 240)
                px = pt.x if pt else -1
                py = pt.y if pt else -1
                print(
                    f"irq={irq} spi={poll} raw=({rx},{ry}) z={z} "
                    f"pixel=({px},{py}) bytes={len(raw)}",
                    file=sys.stderr,
                )
            time.sleep(0.12)
    except KeyboardInterrupt:
        pass
    finally:
        if irq_pin is not None:
            cleanup_irq_gpio(irq_pin)
        touch.close()


if __name__ == "__main__":
    _diag_loop()
