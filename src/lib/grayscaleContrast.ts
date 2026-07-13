/**
 * Draw `img` scaled to w×h, convert to black/white and apply Floyd-Steinberg dithering.
 * `ditherPercent` controls error diffusion strength: 0 = threshold only, 100 = standard.
 *
 * When `lightenBlacks` is true:
 * 1) Near-solid blacks get spatial grain before dithering (so they break up like
 *    naturally textured darks — e.g. the left vs right Muybridge horses).
 * 2) Remaining solid black after dither is thinned with a regular white-dot pattern.
 */

/** 8×8 Bayer matrix, values 0..63 — used to seed grain into flat blacks. */
const BAYER8 = [
  [0, 32, 8, 40, 2, 34, 10, 42],
  [48, 16, 56, 24, 50, 18, 58, 26],
  [12, 44, 4, 36, 14, 46, 6, 38],
  [60, 28, 52, 20, 62, 30, 54, 22],
  [3, 35, 11, 43, 1, 33, 9, 41],
  [51, 19, 59, 27, 49, 17, 57, 25],
  [15, 47, 7, 39, 13, 45, 5, 37],
  [63, 31, 55, 23, 61, 29, 53, 21],
] as const;

/** Treat luma below this as "flat black" that needs grain before dithering. */
const FLAT_BLACK_MAX = 48;
/** After grain, flat blacks sit in this band so Floyd–Steinberg creates dots. */
const GRAIN_LO = 72;
const GRAIN_HI = 140;

/** Base keep when lightening (0.5 = checkerboard). */
const BLACK_KEEP_DEFAULT = 0.5;
/** Stronger thinning when dither is at/above 100%. */
const BLACK_KEEP_HIGH_DITHER = 0.35;

export function drawDitheredBlackWhite(
  ctx: CanvasRenderingContext2D,
  img: CanvasImageSource,
  w: number,
  h: number,
  ditherPercent: number,
  lightenBlacks = false
): void {
  ctx.drawImage(img, 0, 0, w, h);
  const imageData = ctx.getImageData(0, 0, w, h);
  const d = imageData.data;
  const total = w * h;
  const gray = new Float32Array(total);

  for (let p = 0, i = 0; i < d.length; i += 4, p += 1) {
    gray[p] = 0.299 * d[i]! + 0.587 * d[i + 1]! + 0.114 * d[i + 2]!;
  }

  // Flat solid blacks never dither (error stays ~0). Seed Bayer grain so they
  // break up like naturally textured dark areas (left horse vs right horse).
  if (lightenBlacks) {
    for (let y = 0; y < h; y += 1) {
      for (let x = 0; x < w; x += 1) {
        const p = y * w + x;
        if (gray[p]! >= FLAT_BLACK_MAX) continue;
        const t = (BAYER8[y & 7]![x & 7]! + 0.5) / 64;
        gray[p] = GRAIN_LO + t * (GRAIN_HI - GRAIN_LO);
      }
    }
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

  const blackKeep =
    ditherPercent >= 100 ? BLACK_KEEP_HIGH_DITHER : BLACK_KEEP_DEFAULT;

  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const p = y * w + x;
      const i = p * 4;
      let v = gray[p]! < 128 ? 0 : 255;
      if (lightenBlacks && v === 0 && shouldPunchWhite(x, y, blackKeep)) {
        v = 255;
      }
      d[i] = v;
      d[i + 1] = v;
      d[i + 2] = v;
    }
  }
  ctx.putImageData(imageData, 0, 0);
}

/**
 * Deterministic pattern: keep ~keep of black pixels.
 * Uses a small repeating tile so silhouettes stay clean (not noisy).
 */
function shouldPunchWhite(x: number, y: number, keep: number): boolean {
  // 2×2 tile densities: 1.0, 0.75, 0.5, 0.25
  const cell = (x & 1) + ((y & 1) << 1); // 0..3
  if (keep >= 0.875) return false;
  if (keep >= 0.625) return cell === 3; // punch 1/4 → keep 75%
  if (keep >= 0.375) return (cell & 1) === 1; // punch 2/4 → keep 50%
  return cell !== 0; // punch 3/4 → keep 25%
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
  targetMinSide = 384,
  lightenBlacks = false
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
    drawDitheredBlackWhite(ctx, img, w, h, ditherPercent, lightenBlacks);
    return canvas;
  } finally {
    URL.revokeObjectURL(url);
  }
}
