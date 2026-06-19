#!/usr/bin/env python3
"""Live XPT2046 touch diagnostics.

Run on the Pi with the print UI service stopped (it also uses SPI1):

    sudo systemctl stop tft-print-ui.service
    source ~/Desktop/printerGUI/.venv/bin/activate
    python3 pi_tft/touch_debug.py

Press and release the screen. You should see `pressure` jump well above the
threshold and `x`/`y` change while pressed. If nothing changes when you press,
it's a wiring problem (check T_CLK/T_DIN/T_DO/T_CS). If pressure changes but
stays below the threshold, lower TOUCH_Z_THRESHOLD.
"""
from __future__ import annotations

import time

try:
    from xpt2046_touch import XPT2046Touch
except ImportError:  # running from project root
    from pi_tft.xpt2046_touch import XPT2046Touch


def main() -> None:
    try:
        touch = XPT2046Touch()
    except Exception as exc:
        raise SystemExit(
            f"Could not open touch controller: {exc}\n"
            "Check that /dev/spidev1.0 exists (dtoverlay=spi1-1cs) and the print\n"
            "UI service is stopped so it isn't also using SPI1."
        )

    print("Reading touch... press the screen. Ctrl+C to quit.\n")
    try:
        while True:
            s = touch.debug_sample()
            touched = s["pressure"] >= s["threshold"]
            irq = {True: "down", False: "up", None: "n/a"}[s["irq"]]
            print(
                f"z1={s['z1']:4d} z2={s['z2']:4d} pressure={s['pressure']:5d} "
                f"(thr={s['threshold']}) x={s['x']:4d} y={s['y']:4d} "
                f"irq={irq} -> {'TOUCH' if touched else '----'}",
                end="\r",
                flush=True,
            )
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nDone.")
    finally:
        touch.close()


if __name__ == "__main__":
    main()
