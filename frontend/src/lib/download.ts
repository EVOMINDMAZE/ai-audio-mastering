/**
 * Range-based chunked download for large WAV files.
 *
 * The upstream proxy enforces a hard ~30 second timeout on a single
 * streaming response. For multi-megabyte mastered WAVs, downloading the
 * full file in one `fetch().blob()` call will be aborted mid-stream and
 * the user sees a confusing "Load failed" / "Failed to fetch" error.
 *
 * This helper downloads the file in 2MB chunks using HTTP Range requests.
 * Each chunk is a tiny response that completes well under the 30s budget.
 * The chunks are concatenated into a single Blob for downstream use.
 *
 * On the first request, if the server returns the full file with HTTP 200
 * (Range unsupported), we yield the blob directly — no chunking needed.
 *
 * Each chunk retries up to 3 times with exponential backoff before giving
 * up, which handles transient network drops on unstable connections.
 */
export async function fetchInChunks(
  url: string,
  signal: AbortSignal
): Promise<Blob> {
  const CHUNK_SIZE = 2 * 1024 * 1024; // 2 MB
  const MAX_RETRIES = 3;
  const chunks: ArrayBuffer[] = [];
  let offset = 0;
  let totalSize: number | undefined;

  while (totalSize === undefined || offset < totalSize) {
    if (signal.aborted) throw new Error("Aborted");

    const end = totalSize
      ? Math.min(offset + CHUNK_SIZE - 1, totalSize - 1)
      : offset + CHUNK_SIZE - 1;

    let attempt = 0;
    let res: Response | null = null;
    let lastError: unknown = null;

    while (attempt <= MAX_RETRIES) {
      try {
        res = await fetch(url, {
          headers: { Range: `bytes=${offset}-${end}` },
          signal,
        });
        if (res.ok) break;
        throw new Error(`HTTP ${res.status} ${res.statusText}`);
      } catch (err) {
        lastError = err;
        attempt += 1;
        if (attempt > MAX_RETRIES) throw lastError;
        await new Promise((r) => setTimeout(r, 1000 * Math.pow(2, attempt - 1)));
      }
    }

    if (!res) throw lastError ?? new Error("No response");

    // Server returned the full file (200) instead of a chunk (206). Use it.
    if (res.status === 200) {
      return res.blob();
    }

    // First successful 206 — figure out the total file size from
    // Content-Range so subsequent chunks know when to stop.
    if (totalSize === undefined) {
      const contentRange = res.headers.get("Content-Range");
      if (contentRange) {
        const m = contentRange.match(/\/(\d+)$/);
        if (m) totalSize = parseInt(m[1], 10);
      }
      if (totalSize === undefined) {
        throw new Error("Server returned 206 without Content-Range header");
      }
    }

    const buffer = await res.arrayBuffer();
    chunks.push(buffer);
    offset += buffer.byteLength;
  }

  return new Blob(chunks);
}

/**
 * Trigger a browser download of the given blob with a filename.
 * The object URL is revoked after a short delay so the browser has a tick
 * to start the download.
 */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}