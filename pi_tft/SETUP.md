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
ls -l /dev/spidev0.1
```

Touch uses **`/dev/spidev0.1`** by default (hardware **CE1**, GPIO 7). If `spidev0.1` is missing, enable both CE0 and CE1 (default Pi SPI overlay usually provides both).

If missing after reboot, check **`/boot/firmware/config.txt`** (or **`/boot/config.txt`**) for `dtparam=spi=on`.

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

## 9. XPT2046 touchscreen (optional)

The app reads touch on a **second SPI chip-select** (default **`spidev0.1`**, i.e. **CE1 / GPIO 7** for **T_CS**), and **T_IRQ** on a GPIO (default **BCM 17** — change if your board differs).

Silkscreen → Pi (typical):

**Your wiring (matches `print_page_tft.py` display + touch defaults):**

| Screen | BCM GPIO | Physical pin |
|--------|----------|--------------|
| Display CS | **8** (CE0) | 24 |
| Display DC | **24** | 18 |
| Display RESET | **25** | 22 |
| MOSI / SCK | 10 / 11 | 19 / 23 |
| **T_CS** | **7** (CE1) | **26** |
| **T_IRQ** | **17** | **11** |

Note: **physical pin 22** is display **RESET (GPIO 25)** — not touch. Touch CS is **pin 26 = GPIO 7**, not BCM GPIO 22.

| Module | Function | BCM / bus |
|--------|-----------|-----------|
| T_CLK  | SPI clock | GPIO 11 |
| T_DIN  | MOSI      | GPIO 10 |
| T_DO   | MISO      | GPIO 9 |
| T_CS   | Touch CS  | **GPIO 7 / CE1** (header pin 26) — use `export TFT_TOUCH_SPI_DEVICE=1` (not GPIO bit-bang on 7) |
| T_IRQ  | Touch IRQ | **GPIO 17** (pin 11); stuck LOW is ignored automatically |

Environment (see also `pi_tft/xpt2046_touch.py` docstring):

- **`TFT_TOUCH_ENABLE`**: `1` (default) or `0` to disable touch.
- **`TFT_TOUCH_SPI_PORT`** / **`TFT_TOUCH_SPI_DEVICE`**: default `0` / `1`.
- **`TFT_TOUCH_IRQ_GPIO`**: BCM number for **T_IRQ** (default `17`).
- **`TFT_TOUCH_IRQ_ACTIVE`**: `low` (default) or `high` when pressed.
- **Calibration**: `TFT_TOUCH_XMIN`, `TFT_TOUCH_XMAX`, `TFT_TOUCH_YMIN`, `TFT_TOUCH_YMAX` (raw ADC, defaults ~200–3900).
- **Orientation**: `TFT_TOUCH_SWAP_XY=1`, `TFT_TOUCH_INVERT_X=1`, `TFT_TOUCH_INVERT_Y=1` if pointer does not match buttons.

Touches on the bottom **Prev / Next / Print** bars invoke the same actions as before. Taps on the image area are ignored.

**Buttons do nothing?** (common fixes)

1. Confirm touch started — on launch you should see stderr like: `Touch: /dev/spidev0.1 IRQ=on poll=on ...`. If you see `Warning: touch disabled`, fix SPI/GPIO deps first.
2. Run the diagnostic (press buttons and watch pixel coords):
   ```bash
   source .venv/bin/activate
   TFT_TOUCH_DEBUG=1 python3 pi_tft/xpt2046_touch.py
   ```
3. If **`pixel=(x,y)`** moves when you touch but **`action=none`**, calibration is off — try in `.env` or export before run:
   ```bash
   export TFT_TOUCH_SWAP_XY=1
   export TFT_TOUCH_INVERT_Y=1
   ```
   Tune **`TFT_TOUCH_XMIN`/`XMAX`/`YMIN`/`YMAX`** until bottom-bar taps show **y** near **196–240** (on a 240px-tall screen).
4. If **`touch raw bytes: ['0x0', ...]`** (all zeros): the touch IC is not answering on that chip-select. Run:
   ```bash
   TFT_TOUCH_SCAN=1 TFT_TOUCH_DEBUG=1 python3 pi_tft/xpt2046_touch.py
   ```
   Press the screen during the scan. If **CE0** wins, T_CS is tied to **display CS (pin 24)** → `export TFT_TOUCH_SPI_DEVICE=0`. Check `/boot/firmware/config.txt` does **not** contain `dtoverlay=spi0-1cs`.
5. If **`GPIO busy`** on `TFT_TOUCH_CS_GPIO=7`: pin 26 is **hardware CE1** — do **not** bit-bang GPIO 7. Use:
   ```bash
   unset TFT_TOUCH_CS_GPIO
   export TFT_TOUCH_SPI_DEVICE=1
   export TFT_TOUCH_IRQ_GPIO=17
   export TFT_TOUCH_USE_IRQ=0
   TFT_TOUCH_DEBUG=1 python3 pi_tft/xpt2046_touch.py
   ```
   (`TFT_TOUCH_CS_GPIO=7` also works — it maps to CE1 automatically.)
5. IRQ on **GPIO 17** is already the default; if IRQ is wrong but SPI works, polling is on by default (`TFT_TOUCH_POLL=1`).

If touch is wrong or missing **`/dev/spidev0.1`**, wire **T_CS** to **CE1** or adjust **`TFT_TOUCH_SPI_*`** per your schematic.

## 10. Quick troubleshooting

| Symptom | Things to check |
|--------|-------------------|
| `Missing VITE_AWS_*` | `.env` in **project root**; variable names and spelling |
| `No module named 'RPi'` | `pip install rpi-lgpio` + **`liblgpio-dev`** |
| `cannot find -llgio` | `sudo apt install -y liblgpio-dev` |
| `No module named 'spidev'` | `pip install spidev` or full **`pip install -r pi_tkinter/requirements.txt`** |
| `swig` errors | `sudo apt install -y swig` |
| Blank / no display | SPI enabled; wiring; `port=0` / `device=0`; DC/RST GPIOs |
| Print fails | `npm run server` running; `PRINTER_API_BASE`; firewall |
| Touch misses buttons | Set **`TFT_TOUCH_IRQ_GPIO`**; tune **`TFT_TOUCH_*MIN/MAX`** and **`TFT_TOUCH_SWAP_XY`** / **`INVERT_*`** |
| No `/dev/spidev0.1` | Wire **T_CS** to **CE1**; SPI enabled; check **`ls /dev/spidev*`** |

For more context see the main **`README.md`** (ILI9341 TFT section).
