import { useState, useEffect, useCallback } from "react";
import { apiUrl } from "../api";
import { getAllS3ImageChoices, type S3ImageChoice } from "../lib/s3";
import { blobToBase64 } from "../utils/image";

const VISIBLE_COUNT = 5;

export default function PrintPage() {
  const [choices, setChoices] = useState<S3ImageChoice[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [windowStart, setWindowStart] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isPrinting, setIsPrinting] = useState(false);

  const refreshChoices = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const { choices: next } = await getAllS3ImageChoices();
      setChoices(next);
      if (next.length) {
        setSelectedKey(next[0]!.key);
        setWindowStart(0);
      } else {
        setSelectedKey(null);
      }
    } catch (e) {
      setChoices([]);
      setSelectedKey(null);
      setLoadError(e instanceof Error ? e.message : "Failed to load images");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshChoices();
  }, [refreshChoices]);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const selected = choices.find((c) => c.key === selectedKey);
    if (!selected || isPrinting || loading) return;

    setIsPrinting(true);
    try {
      const imageRes = await fetch(selected.url);
      if (!imageRes.ok) throw new Error(`Image HTTP ${imageRes.status}`);
      const blob = await imageRes.blob();
      const imageBase64 = await blobToBase64(blob);

      const response = await fetch(apiUrl("/printimage"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ imageBase64 }),
      });
      const data = await response.text();
      console.log(data);
    } catch (error) {
      console.error("Error:", error);
    } finally {
      setIsPrinting(false);
    }
  }

  const lastWindowStart = Math.max(0, choices.length - VISIBLE_COUNT);
  const visible = choices.slice(windowStart, windowStart + VISIBLE_COUNT);

  return (
    <section id="center">
      <form onSubmit={handleSubmit}>
        <fieldset className="print-image-picker">
          <legend className="print-image-legend">Choose image to print</legend>
          <div className="print-image-actions">
            <button
              type="button"
              className="print-image-refresh"
              onClick={() => void refreshChoices()}
              disabled={loading || isPrinting}
            >
              Refresh images
            </button>
          </div>
          {loadError ? (
            <p className="print-image-error" role="alert">
              {loadError}
            </p>
          ) : null}
          {loading ? (
            <p className="print-image-loading" role="status">
              Loading from S3…
            </p>
          ) : choices.length === 0 ? (
            <p className="print-image-loading" role="status">
              No images in the bucket. Upload some on the Upload page.
            </p>
          ) : (
            <div className="print-carousel">
              <button
                type="button"
                className="print-carousel-arrow"
                aria-label="Previous images"
                onClick={() => setWindowStart((v) => Math.max(0, v - 1))}
                disabled={windowStart === 0}
              >
                ‹
              </button>
              <div
                className="print-image-options"
                role="radiogroup"
                aria-label="Print image"
              >
                {visible.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    role="radio"
                    aria-checked={selectedKey === item.key}
                    className={
                      "print-image-option" +
                      (selectedKey === item.key
                        ? " print-image-option--selected"
                        : "")
                    }
                    onClick={() => setSelectedKey(item.key)}
                  >
                    <img src={item.url} alt="" width={96} height={96} />
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="print-carousel-arrow"
                aria-label="Next images"
                onClick={() => setWindowStart((v) => Math.min(lastWindowStart, v + 1))}
                disabled={windowStart >= lastWindowStart}
              >
                ›
              </button>
            </div>
          )}
        </fieldset>
        <button
          className="counter"
          type="submit"
          disabled={isPrinting || loading || choices.length === 0}
        >
          {isPrinting ? "Printing…" : "Print From Server"}
        </button>
      </form>
    </section>
  );
}
