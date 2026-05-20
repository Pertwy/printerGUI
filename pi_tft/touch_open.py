"""Open the best available touch backend (SPI XPT2046 or kernel evdev)."""

from __future__ import annotations

import os
import sys

from xpt2046_touch import _env_bool, _raw_has_signal, _sample_touch, _valid_raw, scan_touch_bus


def spi_miso_loopback_test() -> bool:
    """
    Quick MISO check: jumper MOSI (pin 19) to MISO (pin 21), run this test.
    Returns True if loopback bytes are non-zero.
    """
    try:
        import spidev
    except ImportError:
        print("spidev not installed", file=sys.stderr)
        return False

    print(
        "SPI loopback: connect Pi pin 19 (MOSI) to pin 21 (MISO) with a jumper, then press Enter.",
        file=sys.stderr,
    )
    try:
        input()
    except EOFError:
        pass

    spi = spidev.SpiDev()
    try:
        spi.open(0, 0)
        spi.max_speed_hz = 500_000
        spi.mode = 0
        pattern = [0xA5, 0x5A, 0xF0, 0x0F]
        rx = spi.xfer2(pattern)
        print(f"  sent   = {[hex(b) for b in pattern]}", file=sys.stderr)
        print(f"  received = {[hex(b) for b in rx]}", file=sys.stderr)
        ok = rx == pattern
        if ok:
            print("  MISO loopback OK — SPI read path works.", file=sys.stderr)
        else:
            print("  MISO loopback FAILED — check jumper / GPIO 9 wiring.", file=sys.stderr)
        return ok
    finally:
        spi.close()


def open_touch_input():
    """
    Return a touch backend (XPT2046 or EvdevTouch) or None.
    """
    use_evdev_only = (os.environ.get("TFT_TOUCH_USE_EVDEV") or "").strip().lower() == "force"

    if not use_evdev_only:
        touch, label = scan_touch_bus(verbose=_env_bool("TFT_TOUCH_SCAN", True))
        if touch is not None:
            rx, ry, z = _sample_touch(touch)
            if _valid_raw(rx, ry, z) or _raw_has_signal(
                list(touch._read_axes_once()[3])
            ):
                return touch
            touch.close()
            print(
                f"Touch: SPI opened ({label}) but MISO still all zero.",
                file=sys.stderr,
            )

    try:
        from evdev_touch import open_evdev_touch

        ev = open_evdev_touch(allow_auto=True)
        if ev is not None:
            print("Touch: using kernel evdev input.", file=sys.stderr)
            return ev
    except Exception as exc:
        print(f"Touch: evdev unavailable ({exc}).", file=sys.stderr)

    print_miso_help()
    if not use_evdev_only:
        from xpt2046_touch import open_optional_xpt2046

        return open_optional_xpt2046()
    return None


def print_miso_help() -> None:
    print(
        "\n*** SPI touch reads all 0x00 — display can still work ***\n"
        "The ILI9341 LCD is write-only and does not use MISO (GPIO 9, pin 21).\n"
        "The XPT2046 touch controller MUST have T_DO wired to MISO or reads stay zero.\n"
        "\nCheck on your module flex:\n"
        "  T_DO / T_DOUT / SDO  →  Pi pin 21 (MISO, GPIO 9)\n"
        "  T_CS                →  Pi pin 26 (CE1) OR shared with LCD CS pin 24\n"
        "\nTests:\n"
        "  1) Loopback:  python3 -c \"from touch_open import spi_miso_loopback_test; spi_miso_loopback_test()\"\n"
        "     (run from pi_tft/ directory)\n"
        "  2) Input devs:  pip install evdev && python3 -c \"from evdev_touch import list_input_devices; list_input_devices()\"\n"
        "  3) Kernel touch: add ads7846 overlay — see pi_tft/evdev_touch.py header\n",
        file=sys.stderr,
    )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--loopback":
        spi_miso_loopback_test()
    elif len(sys.argv) > 1 and sys.argv[1] == "--list-input":
        from evdev_touch import list_input_devices

        list_input_devices()
    else:
        print_miso_help()
        t = open_touch_input()
        if t:
            t.log_config()
