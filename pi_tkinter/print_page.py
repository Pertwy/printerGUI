#!/usr/bin/env python3
from __future__ import annotations

import base64
import io
import os
import threading
from dataclasses import dataclass
from typing import Optional

import boto3
import requests
from PIL import Image, ImageTk

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ModuleNotFoundError as exc:
    if exc.name == "_tkinter":
        raise SystemExit(
            "Tkinter is not available in this Python build.\n"
            "On Raspberry Pi OS install it with: sudo apt install -y python3-tk\n"
            "On macOS install a Python build that includes Tk (python.org installer),\n"
            "or use Homebrew tcl-tk and reinstall Python against it."
        ) from exc
    raise


VISIBLE_COUNT = 5
THUMB_SIZE = 96
MAX_ITEMS = 200


def normalize_prefix(prefix: str) -> str:
    trimmed = prefix.strip().lstrip("/")
    if not trimmed:
        return ""
    return trimmed if trimmed.endswith("/") else f"{trimmed}/"


def load_env_file(path: str = ".env") -> None:
    """Minimal .env loader so this script works without python-dotenv."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class ImageChoice:
    key: str
    url: str
    photo: Optional[ImageTk.PhotoImage] = None


class PrintPageApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Pi Printer - Print Page")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", self._exit_fullscreen)

        self.choices: list[ImageChoice] = []
        self.selected_index: int = -1
        self.window_start: int = 0
        self.loading = False
        self.is_printing = False

        load_env_file()
        self.server_base = (os.environ.get("PRINTER_API_BASE") or "http://localhost:3000").rstrip("/")
        self.s3_client, self.bucket, self.list_prefix = self._build_s3_client()

        self.status_var = tk.StringVar(value="Loading from S3...")
        self._build_ui()
        self.refresh_images()

    def _exit_fullscreen(self, _event=None) -> None:
        self.root.attributes("-fullscreen", False)

    def _build_s3_client(self):
        region = (os.environ.get("VITE_AWS_REGION") or "").strip()
        bucket = (os.environ.get("VITE_S3_BUCKET") or "").strip()
        upload_prefix = normalize_prefix(os.environ.get("VITE_S3_UPLOAD_PREFIX", ""))
        list_prefix = normalize_prefix(os.environ.get("VITE_S3_LIST_PREFIX", ""))
        primary_prefix = upload_prefix or list_prefix

        if not region:
            raise RuntimeError("Missing VITE_AWS_REGION in env/.env")
        if not bucket:
            raise RuntimeError("Missing VITE_S3_BUCKET in env/.env")

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

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(outer, text="Choose image to print", font=("TkDefaultFont", 14, "bold"))
        title.pack(anchor=tk.W, pady=(0, 8))

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(0, 8))

        self.refresh_button = ttk.Button(actions, text="Refresh images", command=self.refresh_images)
        self.refresh_button.pack(side=tk.LEFT)

        self.status_label = ttk.Label(actions, textvariable=self.status_var)
        self.status_label.pack(side=tk.LEFT, padx=12)

        carousel = ttk.Frame(outer)
        carousel.pack(fill=tk.BOTH, expand=True, pady=8)

        self.prev_button = ttk.Button(carousel, text="‹", width=3, command=self.prev_page)
        self.prev_button.pack(side=tk.LEFT, padx=(0, 8))

        self.thumb_frame = ttk.Frame(carousel)
        self.thumb_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.next_button = ttk.Button(carousel, text="›", width=3, command=self.next_page)
        self.next_button.pack(side=tk.LEFT, padx=(8, 0))

        bottom = ttk.Frame(outer)
        bottom.pack(fill=tk.X, pady=(10, 0))

        self.print_button = ttk.Button(
            bottom,
            text="Print From Server",
            command=self.print_selected,
            state=tk.DISABLED,
        )
        self.print_button.pack(side=tk.RIGHT)

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def set_busy(self, *, loading: Optional[bool] = None, printing: Optional[bool] = None) -> None:
        if loading is not None:
            self.loading = loading
        if printing is not None:
            self.is_printing = printing

        controls_enabled = not self.loading and not self.is_printing
        self.refresh_button.configure(state=(tk.NORMAL if controls_enabled else tk.DISABLED))
        self.prev_button.configure(state=tk.NORMAL if controls_enabled and self.window_start > 0 else tk.DISABLED)

        last_window_start = max(0, len(self.choices) - VISIBLE_COUNT)
        can_next = controls_enabled and self.window_start < last_window_start
        self.next_button.configure(state=tk.NORMAL if can_next else tk.DISABLED)

        can_print = controls_enabled and 0 <= self.selected_index < len(self.choices)
        self.print_button.configure(
            state=(tk.NORMAL if can_print else tk.DISABLED),
            text=("Printing..." if self.is_printing else "Print From Server"),
        )

    def refresh_images(self) -> None:
        if self.loading or self.is_printing:
            return
        self.set_busy(loading=True)
        self.set_status("Loading from S3...")
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

            choices: list[ImageChoice] = []
            for key, _ in items:
                url = self.s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=3600,
                )
                choices.append(ImageChoice(key=key, url=url))

            for item in choices[:VISIBLE_COUNT]:
                item.photo = self._download_thumb(item.url)

            self.root.after(0, lambda: self._apply_choices(choices))
        except Exception as exc:
            self.root.after(0, lambda: self._on_refresh_error(str(exc)))

    def _download_thumb(self, url: str) -> Optional[ImageTk.PhotoImage]:
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            image = Image.open(io.BytesIO(r.content))
            image.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image)
        except Exception:
            return None

    def _on_refresh_error(self, msg: str) -> None:
        self.set_status(f"Failed to load images: {msg}")
        self.choices = []
        self.selected_index = -1
        self.window_start = 0
        self.render_thumbnails()
        self.set_busy(loading=False)

    def _apply_choices(self, choices: list[ImageChoice]) -> None:
        self.choices = choices
        self.window_start = 0
        self.selected_index = 0 if choices else -1
        if choices:
            self.set_status(f"Loaded {len(choices)} image(s).")
        else:
            self.set_status("No images in the bucket. Upload from your React app.")
        self.render_thumbnails()
        self.set_busy(loading=False)

    def render_thumbnails(self) -> None:
        for child in self.thumb_frame.winfo_children():
            child.destroy()

        visible = self.choices[self.window_start : self.window_start + VISIBLE_COUNT]
        for idx, item in enumerate(visible):
            absolute_idx = self.window_start + idx
            if item.photo is None:
                item.photo = self._download_thumb(item.url)

            box = ttk.Frame(self.thumb_frame, padding=5)
            box.grid(row=0, column=idx, padx=4, sticky="n")

            selected = absolute_idx == self.selected_index
            if item.photo:
                button = tk.Button(
                    box,
                    image=item.photo,
                    relief=tk.SOLID,
                    bd=3 if selected else 1,
                    highlightthickness=2 if selected else 0,
                    highlightbackground="#2d7ff9",
                    command=lambda i=absolute_idx: self.select_index(i),
                    width=THUMB_SIZE,
                    height=THUMB_SIZE,
                )
            else:
                button = tk.Button(
                    box,
                    text="No preview",
                    relief=tk.SOLID,
                    bd=3 if selected else 1,
                    command=lambda i=absolute_idx: self.select_index(i),
                    width=12,
                    height=6,
                )
            button.pack()

        self.set_busy()

    def select_index(self, index: int) -> None:
        if self.loading or self.is_printing:
            return
        self.selected_index = index
        self.render_thumbnails()

    def prev_page(self) -> None:
        if self.window_start <= 0 or self.loading or self.is_printing:
            return
        self.window_start = max(0, self.window_start - 1)
        self.render_thumbnails()

    def next_page(self) -> None:
        last_start = max(0, len(self.choices) - VISIBLE_COUNT)
        if self.window_start >= last_start or self.loading or self.is_printing:
            return
        self.window_start = min(last_start, self.window_start + 1)
        self.render_thumbnails()

    def print_selected(self) -> None:
        if self.loading or self.is_printing:
            return
        if not (0 <= self.selected_index < len(self.choices)):
            return
        self.set_busy(printing=True)
        self.set_status("Printing...")
        threading.Thread(target=self._print_worker, daemon=True).start()

    def _print_worker(self) -> None:
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

            self.root.after(0, lambda: self._on_print_success(print_resp.text))
        except Exception as exc:
            self.root.after(0, lambda: self._on_print_error(str(exc)))

    def _on_print_success(self, response_text: str) -> None:
        self.set_status(f"Printed. Server: {response_text.strip() or 'OK'}")
        self.set_busy(printing=False)

    def _on_print_error(self, msg: str) -> None:
        self.set_status(f"Print failed: {msg}")
        self.set_busy(printing=False)
        messagebox.showerror("Print failed", msg)


def main() -> None:
    root = tk.Tk()
    try:
        app = PrintPageApp(root)
        app.set_busy()
    except Exception as exc:
        messagebox.showerror("Startup error", str(exc))
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
