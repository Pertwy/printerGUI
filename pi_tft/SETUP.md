# Raspberry Pi — ILI9341 TFT print UI (fresh device)

Use this checklist on a new Pi to match a working `luma.lcd` + SPI + GPIO setup.

## 1. Hardware / OS

- Raspberry Pi with SPI ILI9341 (wiring matches `print_page_tft.py`: SPI0 CE0, DC GPIO 24, RST GPIO 25, `rotate=1`).
- Raspberry Pi OS (or Debian on Pi) with network access.

## 2. Enable SPI

```bash
sudo raspi-config
```

**Interface Options → SPI → Yes**, then reboot if prompted.

Confirm the device exists:

```bash
ls -l /dev/spidev0.0
```

If missing after reboot, check **`/boot/firmware/config.txt`** (or **`/boot/config.txt`**) for `dtparam=spi=on`.

### Touchscreen: also enable the auxiliary SPI1 bus

The ILI9341 display is on **SPI0** (`/dev/spidev0.0`). The **XPT2046 touch controller** is wired to the Pi's second bus, **SPI1**, so it gets its own pins (no splicing onto the display's bus). Enable it by adding this line to **`/boot/firmware/config.txt`** (or **`/boot/config.txt`**):

```
dtoverlay=spi1-1cs
```

Reboot, then confirm both buses exist:

```bash
ls -l /dev/spidev0.0 /dev/spidev1.0
```

If you are not wiring the touch panel, you can skip this; the UI runs display-only.

> **Note — backlight pin conflict:** `spi1-1cs` uses **GPIO 18** as SPI1 CE0, but that is also luma's *default* ILI9341 backlight pin. To avoid a `GPIO not allocated` error at display startup, the app moves the backlight pin to **GPIO 12** (override with the `TFT_BACKLIGHT_GPIO` env var). No wiring change is needed for boards whose backlight isn't GPIO-controlled.

## 3. System packages (build GPIO + SPI Python extensions)

These are required so **`rpi-lgpio`** / **`lgpio`** and **`spidev`** can compile in a venv on modern Pi OS (e.g. Bookworm / Trixie):

```bash
sudo apt update
sudo apt install -y liblgpio-dev swig build-essential python3-dev
```

Optional (only if you also run the **Tkinter** UI): `sudo apt install -y python3-tk`

## 4. Node print API (same machine as the Pi UI)

The TFT app talks to **`PRINTER_API_BASE`** (default `http://localhost:3000`) for `/fetch-for-print` and `/printimage`. Install Node on the Pi if the server runs there, then from the project root:

```bash
cd ~/Desktop/printerGUI   # or your clone path
npm install
npm run server
```

Or install **`print-server`** as **systemd** using `scripts/print-server.service.example` (edit paths/user first).

## 5. Project + environment

- Copy or clone the repo to the Pi (e.g. `~/Desktop/printerGUI`).
- Copy **`.env`** from your React/Vite setup into the **project root** (same folder as `package.json`). The TFT script loads **`../.env`** when run from `pi_tft/` and expects at least:
  - `VITE_AWS_REGION`
  - `VITE_S3_BUCKET`
  - `VITE_AWS_ACCESS_KEY_ID` / `VITE_AWS_SECRET_ACCESS_KEY` (and optional `VITE_AWS_SESSION_TOKEN`)
  - `VITE_S3_UPLOAD_PREFIX` and/or `VITE_S3_LIST_PREFIX` as you use today

If the print API is not on localhost:

```bash
export PRINTER_API_BASE=http://127.0.0.1:3000   # or your server URL
```

## 6. Python venv and dependencies

```bash
cd ~/Desktop/printerGUI
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r pi_tkinter/requirements.txt
```

That file includes **`luma.lcd`**, **`rpi-lgpio`** (ARM Linux only), **`spidev`** (ARM Linux only), plus **`boto3`**, **`Pillow`**, **`requests`**.

If **`pip install`** fails on **`lgpio`**: install **`liblgpio-dev`** (see step 3).

If it fails on **`swig`**: `sudo apt install -y swig`.

## 7. Run the TFT UI

```bash
source ~/Desktop/printerGUI/.venv/bin/activate
python3 ~/Desktop/printerGUI/pi_tft/print_page_tft.py
```

Leave the process running so the panel keeps the last drawn image.

## 8. Boot on power-on (optional)

1. Edit and install **`scripts/print-server.service.example`** → `/etc/systemd/system/print-server.service`.
2. Edit and install **`scripts/tft-print-ui.service.example`** → `/etc/systemd/system/tft-print-ui.service` (**WorkingDirectory**, **User**, **ExecStart** → your `.venv` Python and paths).
3. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now print-server.service
sudo systemctl enable --now tft-print-ui.service
```

## 9. Display + touch wiring (reference)

**Display (SPI0, `/dev/spidev0.0`):**

| Signal | BCM GPIO | Physical pin |
|--------|----------|--------------|
| CS | **8** (CE0) | 24 |
| DC | **24** | 18 |
| RESET | **25** | 22 |
| MOSI | **10** | 19 |
| SCK | **11** | 23 |

**Touch — XPT2046 (SPI1, `/dev/spidev1.0`):**

| Screen pin | Signal | BCM GPIO | Physical pin |
|------------|--------|----------|--------------|
| T_CLK | SPI1 SCLK | **21** | 40 |
| T_DIN | SPI1 MOSI | **20** | 38 |
| T_DO | SPI1 MISO | **19** | 35 |
| T_CS | SPI1 CE0 | **18** | 12 |
| T_IRQ | touch interrupt (optional) | **26** | 37 |

The screen's `SDO(MISO)` (display data-out) is not needed — leave it unconnected.

The TFT UI shows **Prev / Next / Print** on screen. With the touch panel wired and calibrated (see section 10), those buttons are tappable. If touch is not wired/enabled, the UI still runs display-only.

## 10. Touchscreen calibration

After wiring the touch panel and enabling SPI1 (sections 2 and 9), calibrate the resistive panel once so taps line up with the buttons:

```bash
source ~/Desktop/printerGUI/.venv/bin/activate
python3 ~/Desktop/printerGUI/pi_tft/touch_calibrate.py
```

Tap the target shown in each corner of the screen with a stylus. The script writes **`pi_tft/touch_cal.json`** (loaded automatically by `print_page_tft.py`) and also prints equivalent `TOUCH_*` environment variables.

Notes:
- Calibration values can also be overridden via env vars: `TOUCH_MIN_X`, `TOUCH_MAX_X`, `TOUCH_MIN_Y`, `TOUCH_MAX_Y`, `TOUCH_SWAP_XY`, `TOUCH_INVERT_X`, `TOUCH_INVERT_Y`.
- If taps register but on the wrong button, re-run calibration (orientation flags `SWAP_XY`/`INVERT_*` are auto-detected from your corner taps).
- `TOUCH_Z_THRESHOLD` (default `400`) tunes touch sensitivity; `TOUCH_IRQ_GPIO` (default `26`) sets the IRQ pin, set to `0` to disable IRQ and use pressure-only detection.
- After calibrating, restart the UI (`sudo systemctl restart tft-print-ui.service`).

## 11. Quick troubleshooting

| Symptom | Things to check |
|--------|-------------------|
| `Missing VITE_AWS_*` | `.env` in **project root**; variable names and spelling |
| `No module named 'RPi'` | `pip install rpi-lgpio` + **`liblgpio-dev`** |
| `cannot find -llgio` | `sudo apt install -y liblgpio-dev` |
| `No module named 'spidev'` | `pip install spidev` or full **`pip install -r pi_tkinter/requirements.txt`** |
| `swig` errors | `sudo apt install -y swig` |
| Blank / no display | SPI enabled; wiring; `port=0` / `device=0`; DC/RST GPIOs |
| Print fails | `npm run server` running; `PRINTER_API_BASE`; firewall |
| `GPIO not allocated` (backlight) at display init | SPI1 CE0 (GPIO 18) clashes with luma's default backlight pin; app uses `TFT_BACKLIGHT_GPIO` (default 12) to avoid it — pull latest code |
| `[touch] disabled` at startup | `dtoverlay=spi1-1cs` in config.txt; `/dev/spidev1.0` exists; touch wiring (section 9) |
| Taps do nothing | Run `pi_tft/touch_calibrate.py`; check `T_CS`→pin 12 and `T_IRQ`→pin 37; lower `TOUCH_Z_THRESHOLD` |
| Taps hit wrong button | Re-run calibration; verify `SWAP_XY`/`INVERT_*` in `pi_tft/touch_cal.json` |

For more context see the main **`README.md`** (ILI9341 TFT section).
