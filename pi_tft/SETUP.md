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

## 9. Touch (not implemented yet)

XPT2046 is left as **TODO** in `print_page_tft.py` (`T_CLK`, `T_CS`, `T_DIN`, `T_DO`, `T_IRQ`).

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

For more context see the main **`README.md`** (ILI9341 TFT section).
