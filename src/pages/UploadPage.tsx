import { useCallback, useEffect, useRef, useState } from "react";
import {
  drawDitheredBlackWhite,
  getScaledSizeByMinSide,
  renderFileToCanvas,
} from "../lib/grayscaleContrast";
import { uploadJpegToS3 } from "../lib/s3";

type PendingImage = {
  id: string;
  file: File;
  url: string;
};

function PreviewCanvas({
  objectUrl,
  dither,
}: {
  objectUrl: string | null;
  dither: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !objectUrl) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const img = new Image();
    let cancelled = false;
    img.onload = () => {
      if (cancelled) return;
      const { w, h } = getScaledSizeByMinSide(
        img.naturalWidth,
        img.naturalHeight,
        384
      );
      canvas.width = w;
      canvas.height = h;
      drawDitheredBlackWhite(ctx, img, w, h, dither);
    };
    img.src = objectUrl;
    return () => {
      cancelled = true;
      img.onload = null;
    };
  }, [objectUrl, dither]);

  if (!objectUrl) return null;
  return <canvas ref={canvasRef} className="upload-preview-canvas" />;
}

function PendingRow({
  item,
  dither,
  onRemove,
}: {
  item: PendingImage;
  dither: number;
  onRemove: () => void;
}) {
  return (
    <div className="upload-row">
      <div className="upload-row-preview">
        <PreviewCanvas objectUrl={item.url} dither={dither} />
      </div>
      <div className="upload-row-meta">
        <span className="upload-row-name">{item.file.name}</span>
        <button type="button" className="upload-row-remove" onClick={onRemove}>
          Remove
        </button>
      </div>
    </div>
  );
}

export default function UploadPage() {
  const [items, setItems] = useState<PendingImage[]>([]);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const [dither, setDither] = useState(100);
  const [dragActive, setDragActive] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const addFiles = useCallback((fileList: FileList | File[]) => {
    const files = Array.from(fileList).filter((f) => /^image\//.test(f.type));
    if (!files.length) {
      setError("Please choose image files (JPEG, PNG, etc.).");
      return;
    }
    setError(null);
    setItems((prev) => {
      const next = [...prev];
      for (const file of files) {
        next.push({
          id: crypto.randomUUID(),
          file,
          url: URL.createObjectURL(file),
        });
      }
      return next;
    });
  }, []);

  useEffect(() => {
    return () => {
      for (const it of itemsRef.current) URL.revokeObjectURL(it.url);
    };
  }, []);

  const removeItem = useCallback((id: string) => {
    setItems((prev) => {
      const it = prev.find((p) => p.id === id);
      if (it) URL.revokeObjectURL(it.url);
      return prev.filter((p) => p.id !== id);
    });
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
    },
    [addFiles],
  );

  async function saveAll() {
    if (!items.length || saving) return;
    setSaving(true);
    setError(null);
    setStatus(null);
    try {
      let ok = 0;
      for (const item of items) {
        // Keep saved output identical to the previewed print-ready bitmap size.
        const canvas = await renderFileToCanvas(item.file, dither, 384);
        const blob = await new Promise<Blob | null>((resolve) =>
          canvas.toBlob(resolve, "image/jpeg", 0.92),
        );
        if (!blob) throw new Error("Could not encode image");
        await uploadJpegToS3(blob);
        ok += 1;
      }
      setStatus(`Saved ${ok} image(s) to S3.`);
      for (const it of items) URL.revokeObjectURL(it.url);
      setItems([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section id="center" className="upload-page">
      <h2 className="upload-heading">Upload images to print</h2>

      <div
        className={
          "upload-dropzone" + (dragActive ? " upload-dropzone--active" : "")
        }
        onDragEnter={(e) => {
          e.preventDefault();
          setDragActive(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={onDrop}
      >
        <input
          type="file"
          accept="image/*"
          multiple
          aria-label="Choose images to upload"
          className="upload-file-input"
          onChange={(e) => {
            if (e.target.files?.length) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <span className="upload-dropzone-text">
          Tap to choose images or drag and drop here
        </span>
      </div>

      {error ? (
        <p className="upload-error" role="alert">
          {error}
        </p>
      ) : null}
      {status ? (
        <p className="upload-status" role="status">
          {status}
        </p>
      ) : null}

      {items.length > 0 ? (
        <>
          <div className="upload-contrast-block">
            <label htmlFor="upload-contrast" className="upload-contrast-label">
              Dithering ({dither}%)
            </label>
            <input
              id="upload-contrast"
              type="range"
              min={0}
              max={200}
              value={dither}
              onChange={(e) => setDither(Number(e.target.value))}
              className="upload-contrast-range"
            />
          </div>

          <div className="upload-grid">
            {items.map((item) => (
              <PendingRow
                key={item.id}
                item={item}
                dither={dither}
                onRemove={() => removeItem(item.id)}
              />
            ))}
          </div>

          <button
            type="button"
            className="counter upload-save"
            onClick={() => void saveAll()}
            disabled={saving}
          >
            {saving ? "Saving…" : "Save to S3"}
          </button>
        </>
      ) : null}
    </section>
  );
}
