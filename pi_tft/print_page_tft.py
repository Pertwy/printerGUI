#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import os
import sys
import threading
import time
from dataclasses import dataclass

# Allow `import xpt2046_touch` when cwd is project root (not `pi_tft/`).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import boto3
import requests
from luma.core.interface.serial import spi
from luma.lcd.device import ili9341
from PIL import Image, ImageDraw, ImageFont

MAX_ITEMS = 200
LOW_REFRESH_SECONDS = 0.2
AUTO_REFRESH_SECONDS = 30.0
# Bottom bar for Prev / Next / Print. Image uses full width × (height - bar).
BUTTON_BAR_HEIGHT = 44
IMAGE_BG = "#000000"


def button_bar_layout(width: int, height: int) -> tuple[int, int, int, list[tuple[str, tuple[int, int, int, int]]]]:
    """
    Return (bar_top, margin, button_height, [(name, (x0,y0,x1,y1)), ...])
    in the same pixel space as `render()` / touch mapping.
    """
    m = 3
    bar_top = height - BUTTON_BAR_HEIGHT
    btn_h = BUTTON_BAR_HEIGHT - 2 * m
    y0 = bar_top + m
    inner_w = width - 2 * m
    btn_w = inner_w // 3
    x0 = m
    x1 = x0 + btn_w - 1
    regions: list[tuple[str, tuple[int, int, int, int]]] = [
        ("prev", (x0, y0, x1, y0 + btn_h - 1)),
    ]
    x0 = x1 + 1
    x1 = x0 + btn_w - 1
    regions.append(("next", (x0, y0, x1, y0 + btn_h - 1)))
    x0 = x1 + 1
    x1 = width - m - 1
    regions.append(("print", (x0, y0, x1, y0 + btn_h - 1)))
    return bar_top, m, btn_h, regions


def normalize_prefix(prefix: str) -> str:
    trimmed = prefix.strip().lstrip("/")
    if not trimmed:
        return ""
    return trimmed if trimmed.endswith("/") else f"{trimmed}/"


def load_env_file(path: str = ".env") -> str | None:
    """Load .env from cwd/script/project-parent so the script works from pi_tft or project root."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.abspath(path),
        os.path.abspath(os.path.join(script_dir, path)),
        os.path.abspath(os.path.join(script_dir, "..", path)),
    ]

    env_path = None
    for candidate in candidates:
        if os.path.exists(candidate):
            env_path = candidate
            break
    if env_path is None:
        return None

    with open(env_path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    return env_path


@dataclass
class ImageChoice:
    key: str
    url: str


class TFTPrintUI:
    """
    Direct-rendered UI for ILI9341 using PIL + luma.lcd.

    Run on Raspberry Pi:
      1) pip install -r pi_tkinter/requirements.txt
      2) python3 pi_tft/print_page_tft.py
    """

    def __init__(self) -> None:
        self.loaded_env_path = load_env_file()
        self.server_base = (os.environ.get("PRINTER_API_BASE") or "http://localhost:3000").rstrip("/")
        self.s3_client, self.bucket, self.list_prefix = self._build_s3_client()

        # GPIO/SPI wiring for ILI9341:
        # - SPI port=0, device=0
        # - DC pin: GPIO 24
        # - RESET pin: GPIO 25
        try:
            serial = spi(port=0, device=0, gpio_DC=24, gpio_RST=25)
        except ModuleNotFoundError as exc:
            if exc.name in {"RPi", "RPi.GPIO"}:
                raise SystemExit(
                    "Missing Raspberry Pi GPIO Python module.\n"
                    "Install one of these, then retry:\n"
                    "  sudo apt install -y python3-rpi-lgpio\n"
                    "or inside venv:\n"
                    "  pip install rpi-lgpio"
                ) from exc
            if exc.name == "spidev":
                raise SystemExit(
                    "Missing SPI Python module (spidev) for luma.lcd.\n"
                    "Inside your venv run:\n"
                    "  pip install spidev\n"
                    "or reinstall deps:\n"
                    "  pip install -r pi_tkinter/requirements.txt\n"
                    "Also ensure SPI is enabled (sudo raspi-config → Interface Options → SPI)."
                ) from exc
            raise
        self.device = ili9341(serial, rotate=1)
        self.width, self.height = self.device.size

        self.font = ImageFont.load_default()
        self.choices: list[ImageChoice] = []
        self.selected_index = -1
        self.loading = False
        self.is_printing = False
        self.status_text = "Starting..."
        self.last_error = ""
        self.selected_preview: Image.Image | None = None
        self.selected_preview_key: str | None = None

        self.state_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.needs_redraw = True
        self.last_draw_signature = ""
        self.last_refresh_started = 0.0

        self._touch = None
        self._touch_thread: threading.Thread | None = None

        self.set_status("Loading from S3...")
        self.refresh_images()
        self._maybe_start_touch()

    def _build_s3_client(self):
        region = (os.environ.get("VITE_AWS_REGION") or "").strip()
        bucket = (os.environ.get("VITE_S3_BUCKET") or "").strip()
        upload_prefix = normalize_prefix(os.environ.get("VITE_S3_UPLOAD_PREFIX", ""))
        list_prefix = normalize_prefix(os.environ.get("VITE_S3_LIST_PREFIX", ""))
        primary_prefix = upload_prefix or list_prefix

        if not region:
            raise RuntimeError(
                f"Missing VITE_AWS_REGION. Looked for .env near script and cwd. Loaded: {self.loaded_env_path or 'none'}"
            )
        if not bucket:
            raise RuntimeError(
                f"Missing VITE_S3_BUCKET. Looked for .env near script and cwd. Loaded: {self.loaded_env_path or 'none'}"
            )

        access_key = (os.environ.get("VITE_AWS_ACCESS_KEY_ID") or "").strip()
        secret_key = (os.environ.get("VITE_AWS_SECRET_ACCESS_KEY") or "").strip()
        session_token = (os.environ.get("VITE_AWS_SESSION_TOKEN") or "").strip()

        if not access_key or not secret_key:
            raise RuntimeError(
                "Missing AWS credentials (VITE_AWS_ACCESS_KEY_ID / VITE_AWS_SECRET_ACCESS_KEY)"
            )

        kwargs = {
            "region_name": region,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }
        if session_token:
            kwargs["aws_session_token"] = session_token
        client = boto3.client("s3", **kwargs)
        return client, bucket, primary_prefix

    def _maybe_start_touch(self) -> None:
        flag = (os.environ.get("TFT_TOUCH_ENABLE") or "1").strip().lower()
        if flag in ("0", "false", "no", "off"):
            return
        try:
            import xpt2046_touch as xt

            self._touch = xt.open_optional_xpt2046()
        except Exception as exc:
            print(f"Warning: touch disabled ({exc}).", file=sys.stderr)
            return
        if self._touch is None:
            return
        self._touch.log_config()
        try:
            rx, ry, z = xt._sample_touch(self._touch)
            print(f"Touch startup sample: raw=({rx},{ry}) z={z}", file=sys.stderr)
            if not xt._valid_raw(rx, ry, z):
                print(
                    "Touch: no SPI response yet — while pressing screen run: "
                    "TFT_TOUCH_DEBUG=1 python3 pi_tft/xpt2046_touch.py",
                    file=sys.stderr,
                )
        except Exception:
            pass
        self._touch_thread = threading.Thread(target=self._touch_loop, name="xpt2046-touch", daemon=True)
        self._touch_thread.start()

    def _touch_action_for_point(self, x: int, y: int) -> str | None:
        pad = int((os.environ.get("TFT_TOUCH_HIT_PAD") or "8").strip() or "8")
        _, _, _, regions = button_bar_layout(self.width, self.height)
        for name, (x0, y0, x1, y1) in regions:
            if (x0 - pad) <= x <= (x1 + pad) and (y0 - pad) <= y <= (y1 + pad):
                return name
        return None

    def _dispatch_touch_action(self, action: str) -> None:
        if action == "prev":
            self.prev_item()
        elif action == "next":
            self.next_item()
        elif action == "print":
            self.print_selected()

    def _touch_loop(self) -> None:
        import xpt2046_touch as xt

        touch = self._touch
        if touch is None:
            return
        irq_pin: int | None = None
        touched_when_low = True
        if touch.irq_enabled:
            try:
                irq_pin, touched_when_low = xt.setup_irq_gpio()
                if xt.detect_irq_stuck_low(irq_pin, touched_when_low):
                    print(
                        "Warning: T_IRQ stuck LOW — ignoring IRQ; using SPI touch only.",
                        file=sys.stderr,
                    )
                    touch.irq_enabled = False
                    irq_pin = None
            except Exception as exc:
                print(f"Warning: touch IRQ disabled ({exc}); using SPI poll only.", file=sys.stderr)
                touch.irq_enabled = False
        if not touch.poll_enabled and irq_pin is None:
            print("Warning: touch has no IRQ and poll is off; buttons will not work.", file=sys.stderr)
            return
        try:
            while not self.stop_event.is_set():
                if not xt.touch_input_active(touch, irq_pin, touched_when_low):
                    time.sleep(0.03)
                    continue
                time.sleep(0.006)
                if not xt.touch_input_active(touch, irq_pin, touched_when_low):
                    continue
                pt = touch.read_pixel(self.width, self.height)
                if pt is None:
                    continue
                action = self._touch_action_for_point(pt.x, pt.y)
                if touch.debug:
                    print(
                        f"touch pixel=({pt.x},{pt.y}) action={action or 'none'}",
                        file=sys.stderr,
                    )
                if action is not None:
                    self._dispatch_touch_action(action)
                deadline = time.time() + 2.0
                while xt.touch_input_active(touch, irq_pin, touched_when_low) and not self.stop_event.is_set():
                    if time.time() > deadline:
                        break
                    time.sleep(0.008)
                time.sleep(0.16)
        finally:
            if irq_pin is not None:
                xt.cleanup_irq_gpio(irq_pin)

    def set_status(self, message: str) -> None:
        with self.state_lock:
            self.status_text = message
            self.needs_redraw = True

    def refresh_images(self) -> None:
        with self.state_lock:
            if self.loading or self.is_printing:
                return
            self.loading = True
            self.last_refresh_started = time.time()
            self.status_text = "Loading from S3..."
            self.needs_redraw = True
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self) -> None:
        try:
            response = self.s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=self.list_prefix or "",
                MaxKeys=1000,
            )
            items = []
            for obj in response.get("Contents", []):
                key = obj.get("Key") or ""
                if not key or key.endswith("/"):
                    continue
                lower = key.lower()
                if not lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                    continue
                items.append((key, obj.get("LastModified")))

            items.sort(key=lambda x: x[1] or 0, reverse=True)
            items = items[:MAX_ITEMS]

            choices = []
            for key, _ in items:
                url = self.s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=3600,
                )
                choices.append(ImageChoice(key=key, url=url))
            self._apply_choices(choices)
        except Exception as exc:
            with self.state_lock:
                self.loading = False
                self.last_error = str(exc)
                self.status_text = f"Load failed: {exc}"
                self.needs_redraw = True

    def _apply_choices(self, choices: list[ImageChoice]) -> None:
        with self.state_lock:
            self.choices = choices
            self.selected_index = 0 if choices else -1
            self.selected_preview = None
            self.selected_preview_key = None
            self.loading = False
            self.last_error = ""
            if choices:
                self.status_text = f"Loaded {len(choices)} image(s)"
            else:
                self.status_text = "No images found in S3 prefix"
            self.needs_redraw = True
        self._ensure_selected_preview()

    def _ensure_selected_preview(self) -> None:
        with self.state_lock:
            if not (0 <= self.selected_index < len(self.choices)):
                return
            selected = self.choices[self.selected_index]
            if self.selected_preview_key == selected.key and self.selected_preview is not None:
                return
            selected_url = selected.url
            selected_key = selected.key
        try:
            resp = requests.get(selected_url, timeout=15)
            resp.raise_for_status()
            image = Image.open(io.BytesIO(resp.content)).convert("RGB")
            max_w = self.width
            max_h = max(1, self.height - BUTTON_BAR_HEIGHT)
            image.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
            with self.state_lock:
                self.selected_preview = image
                self.selected_preview_key = selected_key
                self.needs_redraw = True
        except Exception:
            with self.state_lock:
                self.selected_preview = None
                self.selected_preview_key = selected_key
                self.needs_redraw = True

    def print_selected(self) -> None:
        with self.state_lock:
            if self.loading or self.is_printing:
                return
            if not (0 <= self.selected_index < len(self.choices)):
                return
            self.is_printing = True
            self.status_text = "Printing..."
            self.needs_redraw = True
        threading.Thread(target=self._print_worker, daemon=True).start()

    def _print_worker(self) -> None:
        with self.state_lock:
            selected = self.choices[self.selected_index]
        try:
            fetch_resp = requests.post(
                f"{self.server_base}/fetch-for-print",
                json={"url": selected.url},
                timeout=45,
            )
            fetch_resp.raise_for_status()
            image_base64 = base64.b64encode(fetch_resp.content).decode("ascii")

            print_resp = requests.post(
                f"{self.server_base}/printimage",
                json={"imageBase64": image_base64},
                timeout=60,
            )
            print_resp.raise_for_status()
            with self.state_lock:
                self.is_printing = False
                self.status_text = f"Printed: {print_resp.text.strip() or 'OK'}"
                self.needs_redraw = True
        except Exception as exc:
            with self.state_lock:
                self.is_printing = False
                self.last_error = str(exc)
                self.status_text = f"Print failed: {exc}"
                self.needs_redraw = True

    def next_item(self) -> None:
        with self.state_lock:
            if self.loading or self.is_printing or len(self.choices) <= 1:
                return
            self.selected_index = (self.selected_index + 1) % len(self.choices)
            self.selected_preview = None
            self.selected_preview_key = None
            self.needs_redraw = True
        self._ensure_selected_preview()

    def prev_item(self) -> None:
        with self.state_lock:
            if self.loading or self.is_printing or len(self.choices) <= 1:
                return
            self.selected_index = (self.selected_index - 1) % len(self.choices)
            self.selected_preview = None
            self.selected_preview_key = None
            self.needs_redraw = True
        self._ensure_selected_preview()

    def maybe_auto_refresh(self) -> None:
        with self.state_lock:
            can_refresh = not self.loading and not self.is_printing
            elapsed = time.time() - self.last_refresh_started
        if can_refresh and elapsed >= AUTO_REFRESH_SECONDS:
            self.refresh_images()

    def _draw_button(self, draw: ImageDraw.ImageDraw, bounds, label: str, active: bool) -> None:
        fill = "#2a2a2a" if active else "#1a1a1a"
        outline = "#6ec1ff" if active else "#555555"
        draw.rectangle(bounds, fill=fill, outline=outline, width=2)
        x0, y0, x1, y1 = bounds
        tw = int(draw.textlength(label, font=self.font))
        th = 10
        draw.text((x0 + (x1 - x0 - tw) // 2, y0 + (y1 - y0 - th) // 2), label, fill="white", font=self.font)

    def render(self) -> None:
        with self.state_lock:
            total = len(self.choices)
            index = self.selected_index

            signature = "|".join(
                [
                    str(total),
                    str(index),
                    str(self.loading),
                    str(self.is_printing),
                    str(bool(self.selected_preview)),
                    str(self.selected_preview.size if self.selected_preview else ""),
                    str(self.selected_preview_key or ""),
                ]
            )
            should_draw = self.needs_redraw or (signature != self.last_draw_signature)
            if not should_draw:
                return
            self.needs_redraw = False
            self.last_draw_signature = signature
            loading = self.loading
            is_printing = self.is_printing
            preview = self.selected_preview.copy() if self.selected_preview else None

        image_area_h = max(1, self.height - BUTTON_BAR_HEIGHT)
        img = Image.new("RGB", self.device.size, IMAGE_BG)
        draw = ImageDraw.Draw(img)

        draw.rectangle((0, 0, self.width - 1, image_area_h - 1), fill=IMAGE_BG, outline=IMAGE_BG)
        if preview:
            px = max(0, (self.width - preview.width) // 2)
            py = max(0, (image_area_h - preview.height) // 2)
            img.paste(preview, (px, py))

        _, _, _, regions = button_bar_layout(self.width, self.height)
        can_prev_next = not loading and not is_printing and total > 1
        can_print = not loading and not is_printing and 0 <= index < total
        for (label, bounds), active in zip(
            regions,
            [can_prev_next, can_prev_next, can_print],
        ):
            self._draw_button(draw, bounds, label.capitalize(), active)

        try:
            from xpt2046_touch import spi_bus_lock
        except ImportError:
            spi_bus_lock = None
        if spi_bus_lock is not None:
            with spi_bus_lock:
                self.device.display(img)
        else:
            self.device.display(img)

    def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                self.maybe_auto_refresh()
                self.render()
                time.sleep(LOW_REFRESH_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_event.set()
            if self._touch_thread is not None:
                self._touch_thread.join(timeout=1.5)
            touch = getattr(self, "_touch", None)
            if touch is not None:
                try:
                    touch.close()
                except Exception:
                    pass


def main() -> None:
    app = TFTPrintUI()
    app.run()


if __name__ == "__main__":
    main()
