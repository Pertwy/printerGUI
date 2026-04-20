/**
 * Draw `img` scaled to w×h, convert to black/white and apply Floyd-Steinberg dithering.
 * `ditherPercent` controls error diffusion strength: 0 = threshold only, 100 = standard.
 */
export function drawDitheredBlackWhite(
  ctx: CanvasRenderingContext2D,
  img: CanvasImageSource,
  w: number,
  h: number,
  ditherPercent: number
): void {
  ctx.drawImage(img, 0, 0, w, h);
  const imageData = ctx.getImageData(0, 0, w, h);
  const d = imageData.data;
  const total = w * h;
  const gray = new Float32Array(total);

  for (let p = 0, i = 0; i < d.length; i += 4, p += 1) {
    gray[p] = 0.299 * d[i]! + 0.587 * d[i + 1]! + 0.114 * d[i + 2]!;
  }

  const strength = Math.max(0, ditherPercent) / 100;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const idx = y * w + x;
      const oldV = gray[idx]!;
      const newV = oldV < 128 ? 0 : 255;
      gray[idx] = newV;
      const err = (oldV - newV) * strength;
      if (x + 1 < w) gray[idx + 1] = gray[idx + 1]! + (err * 7) / 16;
      if (y + 1 < h) {
        if (x > 0) gray[idx + w - 1] = gray[idx + w - 1]! + (err * 3) / 16;
        gray[idx + w] = gray[idx + w]! + (err * 5) / 16;
        if (x + 1 < w) {
          gray[idx + w + 1] = gray[idx + w + 1]! + err / 16;
        }
      }
    }
  }

  for (let i = 0; i < d.length; i += 4) {
    const p = i / 4;
    const v = gray[p]! < 128 ? 0 : 255;
    d[i] = v;
    d[i + 1] = v;
    d[i + 2] = v;
  }
  ctx.putImageData(imageData, 0, 0);
}

export function getScaledSizeByMinSide(
  srcW: number,
  srcH: number,
  targetMinSide: number
): { w: number; h: number } {
  if (srcW <= 0 || srcH <= 0) {
    throw new Error("Invalid source image dimensions");
  }
  const minSide = Math.min(srcW, srcH);
  const scale = targetMinSide / minSide;
  return {
    w: Math.max(1, Math.round(srcW * scale)),
    h: Math.max(1, Math.round(srcH * scale)),
  };
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Failed to load image"));
    img.src = src;
  });
}

/** Renders `file` to a new canvas with min side = `targetMinSide`, then dithers. */
export async function renderFileToCanvas(
  file: File,
  ditherPercent: number,
  targetMinSide = 384
): Promise<HTMLCanvasElement> {
  const url = URL.createObjectURL(file);
  try {
    const img = await loadImage(url);
    const { w, h } = getScaledSizeByMinSide(
      img.naturalWidth,
      img.naturalHeight,
      targetMinSide
    );
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D context unavailable");
    drawDitheredBlackWhite(ctx, img, w, h, ditherPercent);
    return canvas;
  } finally {
    URL.revokeObjectURL(url);
  }
}
