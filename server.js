import fs from "fs";
import path from "path";
import os from "os";
import { createRequire } from "module";
import express from "express";
import cors from "cors";
import { Jimp } from "jimp";

const require = createRequire(import.meta.url);
const escpos = require("escpos");
escpos.File = require("escpos-file");

const app = express();
app.use(cors());
app.use(express.json({ limit: "15mb" }));

// 🖨️ Printer setup
const device = new escpos.File("/dev/usb/lp0");
const printer = new escpos.Printer(device);

// ------------------------
// 🧪 TEST
// ------------------------
app.get("/test", (req, res) => {
  device.open(() => {
    printer.text("Hello from /dev/usb/lp0").cut().close();
  });
  res.send("Test print sent");
});

// ------------------------
// 🖼️ PRINT IMAGE
// ------------------------
/**
 * Fetch image bytes from a presigned S3 URL server-side so the browser does not need
 * S3 CORS for GET (img tags still work without CORS; fetch() does not).
 */
app.post("/fetch-for-print", async (req, res) => {
  const raw = req.body?.url;
  if (typeof raw !== "string" || !raw.trim()) {
    return res.status(400).send("Missing url");
  }
  let u;
  try {
    u = new URL(raw.trim());
  } catch {
    return res.status(400).send("Invalid url");
  }
  if (u.protocol !== "https:") {
    return res.status(400).send("Only https URLs allowed");
  }
  const h = u.hostname;
  if (
    !h.endsWith(".amazonaws.com") ||
    !h.includes(".s3.")
  ) {
    return res.status(403).send("URL host not allowed");
  }

  try {
    const upstream = await fetch(raw.trim());
    if (!upstream.ok) {
      return res.status(upstream.status).send("Upstream fetch failed");
    }
    const ct =
      upstream.headers.get("content-type") || "application/octet-stream";
    const buf = Buffer.from(await upstream.arrayBuffer());
    res.setHeader("Content-Type", ct);
    res.setHeader("Cache-Control", "private, max-age=60");
    res.send(buf);
  } catch (e) {
    console.error("fetch-for-print", e);
    res.status(502).send("Fetch failed");
  }
});

app.post("/printimage", async (req, res) => {
  console.log("Print request received");

  let processedPath;

  try {
    const imageBase64 = req.body?.imageBase64;
    if (typeof imageBase64 !== "string" || !imageBase64.trim()) {
      return res.status(400).send("Missing imageBase64");
    }

    let inputBuffer;
    try {
      inputBuffer = Buffer.from(imageBase64, "base64");
    } catch {
      return res.status(400).send("Invalid base64");
    }
    if (!inputBuffer.length) {
      return res.status(400).send("Empty image");
    }

    const jimpImage = await Jimp.read(inputBuffer);

    // Rotate landscape images
    if (jimpImage.bitmap.width > jimpImage.bitmap.height) {
      jimpImage.rotate(90);
    }

    processedPath = path.join(os.tmpdir(), `print-processed-${Date.now()}.jpg`);
    await jimpImage.write(processedPath);

    await new Promise((resolve, reject) => {
      escpos.Image.load(processedPath, function (image) {
        if (!image) {
          return reject(new Error("escpos.Image.load failed"));
        }
        device.open(() => {
          try {
            printer.align("ct").raster(image).feed(2).cut().close();
            resolve();
          } catch (e) {
            reject(e);
          }
        });
      });
    });

    console.log("Image printed");
    res.send("Printed");
  } catch (err) {
    console.error(err);
    res.status(500).send("Error printing image");
  } finally {
    if (processedPath) {
      fs.promises.unlink(processedPath).catch(() => {});
    }
  }
});

// Printer API only — bind to loopback so the browser talks to `/printimage` via the Vite dev proxy,
// not directly to port 3000 (avoids extra CORS issues on LAN devices).

app.listen(3000, "localhost", () => {
  console.log("Print server running on http://localhost:3000");
});
