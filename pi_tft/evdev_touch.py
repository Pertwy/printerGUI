"""
Linux evdev touch input (kernel ads7846 / xpt2046 driver).

Use when SPI MISO reads are all 0x00 but the panel still has a working touch
controller wired to the kernel (check: ls /dev/input/, evtest).

Enable overlay example in /boot/firmware/config.txt (adjust penirq/cs to your board):
  dtoverlay=ads7846,cs=1,penirq=17,penirq_pull=2,speed=1000000,swapxy=0,xmin=200,xmax=3900,ymin=200,ymax=3900

Then: export TFT_TOUCH_USE_EVDEV=1
"""

from __future__ import annotations

import fcntl
import os
import sys

from xpt2046_touch import TouchPoint, _env_bool, _env_int


def _find_touch_device():
    try:
        from evdev import InputDevice, ecodes, list_devices
    except ImportError as exc:
        raise RuntimeError(
            "evdev not installed. On the Pi run: pip install evdev"
        ) from exc

    keywords = ("touch", "ads", "7846", "xpt", "2046", "ts", "tsc", "pen")
    best = None
    for path in sorted(list_devices()):
        try:
            dev = InputDevice(path)
        except (OSError, PermissionError):
            continue
        name = (dev.name or "").lower()
        caps = dev.capabilities()
        has_abs = ecodes.EV_ABS in caps
        if not has_abs:
            continue
        abs_codes = {code for code, _ in caps.get(ecodes.EV_ABS, [])}
        has_xy = bool(
            abs_codes
            & {
                ecodes.ABS_X,
                ecodes.ABS_Y,
                ecodes.ABS_MT_POSITION_X,
                ecodes.ABS_MT_POSITION_Y,
            }
        )
        if not has_xy and not any(k in name for k in keywords):
            continue
        score = (10 if has_xy else 0) + sum(1 for k in keywords if k in name)
        if best is None or score > best[0]:
            best = (score, dev)
    return best[1] if best else None


class EvdevTouch:
    """Read touch coordinates from /dev/input/event* (kernel driver)."""

    backend = "evdev"

    def __init__(self, device) -> None:
        from evdev import ecodes

        self._dev = device
        self._ecodes = ecodes
        self.pressed = False
        self._raw_x = 0
        self._raw_y = 0
        self.irq_enabled = False
        self.poll_enabled = True
        self.debug = _env_bool("TFT_TOUCH_DEBUG", False)
        self.swap_xy = _env_bool("TFT_TOUCH_SWAP_XY", False)
        self.invert_x = _env_bool("TFT_TOUCH_INVERT_X", False)
        self.invert_y = _env_bool("TFT_TOUCH_INVERT_Y", False)
        self._xmin = _env_int("TFT_TOUCH_XMIN", 0)
        self._xmax = _env_int("TFT_TOUCH_XMAX", 0)
        self._ymin = _env_int("TFT_TOUCH_YMIN", 0)
        self._ymax = _env_int("TFT_TOUCH_YMAX", 0)
        self._grab = _env_bool("TFT_TOUCH_EVDEV_GRAB", False)
        try:
            flags = fcntl.fcntl(self._dev.fd, fcntl.F_GETFL)
            fcntl.fcntl(self._dev.fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except Exception:
            pass
        if self._grab:
            try:
                self._dev.grab()
            except Exception:
                pass

    def close(self) -> None:
        try:
            if self._grab:
                self._dev.ungrab()
        except Exception:
            pass
        try:
            self._dev.close()
        except Exception:
            pass

    def log_config(self) -> None:
        print(
            f"Touch: evdev device '{self._dev.name}' path={self._dev.path} "
            f"(SPI MISO not used — kernel driver)",
            file=sys.stderr,
        )

    def poll(self) -> None:
        """Drain pending evdev events (non-blocking)."""
        ecodes = self._ecodes
        try:
            for event in self._dev.read():
                if event.type == ecodes.EV_ABS:
                    if event.code in (ecodes.ABS_X, ecodes.ABS_MT_POSITION_X):
                        self._raw_x = event.value
                    elif event.code in (ecodes.ABS_Y, ecodes.ABS_MT_POSITION_Y):
                        self._raw_y = event.value
                elif event.type == ecodes.EV_KEY:
                    if event.code in (ecodes.BTN_TOUCH, ecodes.BTN_LEFT):
                        self.pressed = bool(event.value)
                elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                    pass
        except BlockingIOError:
            pass

    def is_finger_down(self) -> bool:
        self.poll()
        return self.pressed

    def read_pixel(self, width: int, height: int) -> TouchPoint | None:
        self.poll()
        if not self.pressed:
            return None
        rx, ry = self._raw_x, self._raw_y
        if self.swap_xy:
            rx, ry = ry, rx
        xmax = self._xmax
        ymax = self._ymax
        if xmax <= self._xmin or ymax <= self._ymin:
            try:
                ecodes = self._ecodes
                ai_x = self._dev.absinfo(ecodes.ABS_X)
                ai_y = self._dev.absinfo(ecodes.ABS_Y)
                xmin, xmax = ai_x.min, ai_x.max
                ymin, ymax = ai_y.min, ai_y.max
            except Exception:
                xmin = ymin = 0
                xmax, ymax = max(1, width - 1), max(1, height - 1)
        else:
            xmin, ymin = self._xmin, self._ymin
        xr = max(1, xmax - xmin)
        yr = max(1, ymax - ymin)
        nx = max(0.0, min(1.0, (rx - xmin) / xr))
        ny = max(0.0, min(1.0, (ry - ymin) / yr))
        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny
        return TouchPoint(x=int(nx * (width - 1)), y=int(ny * (height - 1)))


def open_evdev_touch(*, allow_auto: bool = False) -> EvdevTouch | None:
    flag = (os.environ.get("TFT_TOUCH_USE_EVDEV") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return None
    if flag not in ("1", "true", "yes", "on", "force") and not allow_auto:
        return None
    dev = _find_touch_device()
    if dev is None:
        return None
    return EvdevTouch(dev)


def list_input_devices() -> None:
    try:
        from evdev import InputDevice, list_devices
    except ImportError:
        print("Install evdev: pip install evdev", file=sys.stderr)
        return
    print("Input devices:", file=sys.stderr)
    for path in list_devices():
        try:
            d = InputDevice(path)
            print(f"  {path}: {d.name}", file=sys.stderr)
        except Exception as exc:
            print(f"  {path}: (unreadable: {exc})", file=sys.stderr)
