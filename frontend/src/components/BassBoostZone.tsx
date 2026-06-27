import { useCallback, useRef, useState, DragEvent, ChangeEvent } from "react";
import { fetchInChunks, triggerBlobDownload } from "../lib/download";

const ACCEPT = ".wav,.mp3,.flac,.ogg,audio/wav,audio/mpeg,audio/flac,audio/ogg";

interface Props {
  className?: string;
}

/**
 * "Just Bass Boost" — single-purpose upload zone.
 *
 * Drops / picks a file, POSTs it to `/api/bass-boost`, polls
 * `/api/bass-boost/{job_id}/status` until the render is ready, then
 * downloads the WAV from `/api/bass-boost/{job_id}/download`.
 *
 * The download supports HTTP Range so the upstream proxy's 30-second
 * timeout can't truncate large files. We still use `fetch` + `blob()`
 * for the final download (which can take longer than 30s) — if the user's
 * browser connection itself is too slow, we'll fall back to a direct
 * anchor click on the same URL.
 */
export default function BassBoostZone({ className }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [lastFilename, setLastFilename] = useState<string | null>(null);
  const [stage, setStage] = useState<"idle" | "uploading" | "rendering" | "downloading">(
    "idle"
  );
  const abortRef = useRef<AbortController | null>(null);

  const validate = (file: File): string | null => {
    const lower = file.name.toLowerCase();
    if (
      !lower.endsWith(".wav") &&
      !lower.endsWith(".mp3") &&
      !lower.endsWith(".flac") &&
      !lower.endsWith(".ogg")
    ) {
      return `Unsupported file type: ${file.name || "(no name)"}`;
    }
    if (file.size > 50 * 1024 * 1024) {
      return "File exceeds the 50 MB limit.";
    }
    return null;
  };

  const submit = useCallback(
    async (file: File) => {
      const validationError = validate(file);
      if (validationError) {
        setLocalError(validationError);
        return;
      }
      setLocalError(null);
      setBusy(true);
      setLastFilename(file.name);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        // 1. Upload
        setStage("uploading");
        const form = new FormData();
        form.append("file", file);
        const submitRes = await fetch("/api/bass-boost", {
          method: "POST",
          body: form,
          signal: controller.signal,
        });
        if (!submitRes.ok) {
          let detail = submitRes.statusText;
          try {
            const body = await submitRes.json();
            detail = body.detail ?? detail;
          } catch {
            /* fallthrough */
          }
          throw new Error(`Upload failed: ${submitRes.status} ${detail}`);
        }
        const { job_id: jobId, status_url } = (await submitRes.json()) as {
          job_id: string;
          status: string;
          status_url: string;
        };

        // 2. Poll for render completion
        setStage("rendering");
        const statusUrl = status_url.startsWith("http")
          ? status_url
          : status_url.replace(/^\/api/, "/api");
        let attempts = 0;
        let lastStatus: { status: string; error?: string | null } | null = null;
        while (attempts < 240) {
          // Up to ~4 minutes at 1s intervals
          if (controller.signal.aborted) throw new Error("Cancelled.");
          await new Promise((r) => setTimeout(r, 1000));
          attempts += 1;
          const res = await fetch(statusUrl, { signal: controller.signal });
          if (res.status === 502 || res.status === 503 || res.status === 504) {
            // Transient cold-start on Render free tier (worker just restarted
            // after the 15-min sleep). Retry with backoff — the job_id is
            // server-side, and if the registry survived the restart the next
            // poll will succeed. If the job itself is gone (404), we surface
            // a clearer message below.
            await new Promise((r) => setTimeout(r, 3000));
            attempts -= 1; // don't count transient errors against the 240-attempt budget
            continue;
          }
          if (res.status === 404) {
            throw new Error(
              "Render lost — Render restarted the worker mid-render (15-min " +
                "sleep kicked in). Upload the file again to retry."
            );
          }
          if (!res.ok) {
            throw new Error(`Status poll failed: ${res.status} ${res.statusText}`);
          }
          lastStatus = (await res.json()) as {
            status: string;
            error?: string | null;
          };
          if (lastStatus.status === "ready") break;
          if (lastStatus.status === "error") {
            throw new Error(lastStatus.error || "Render failed.");
          }
        }
        if (!lastStatus || lastStatus.status !== "ready") {
          throw new Error("Render timed out after 4 minutes.");
        }

        // 3. Download the rendered WAV in 2 MB Range chunks so a 50 MB file
        //    doesn't trip the upstream proxy's 30-second timeout.
        setStage("downloading");
        const downloadUrl = `/api/bass-boost/${jobId}/download`;
        const blob = await fetchInChunks(downloadUrl, controller.signal);
        const cd = (await fetch(downloadUrl, {
          headers: { Range: "bytes=0-0" },
          signal: controller.signal,
        })
          .then((r) => r.headers.get("Content-Disposition"))
          .catch(() => "")) ?? "";
        const match = cd.match(/filename="?([^";]+)"?/);
        const filename =
          match?.[1] ?? `${file.name.replace(/\.[^.]+$/, "")}-bass-boosted.wav`;
        triggerBlobDownload(blob, filename);
        setStage("idle");
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          setLocalError("Cancelled.");
        } else {
          const msg = e instanceof Error ? e.message : "Bass boost failed.";
          setLocalError(msg);
        }
        setStage("idle");
      } finally {
        setBusy(false);
        abortRef.current = null;
      }
    },
    []
  );

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    if (busy) return;
    const file = e.dataTransfer.files?.[0];
    if (file) void submit(file);
  };

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) void submit(file);
    e.target.value = "";
  };

  const stageLabel: Record<typeof stage, string> = {
    idle: "",
    uploading: "Uploading…",
    rendering: "Rendering bass-boosted WAV…",
    downloading: "Preparing download…",
  };

  return (
    <div
      className={[
        "rounded-2xl border border-ink-700 bg-ink-900/40 p-4",
        className ?? "",
      ].join(" ")}
    >
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100">Just Bass Boost</h3>
          <p className="text-xs text-zinc-500">
            Drop a track, get a bass-boosted WAV. No analysis, no preview.
          </p>
        </div>
        {lastFilename && (
          <span className="rounded-full bg-ink-800 px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-400">
            Last: {lastFilename}
          </span>
        )}
      </div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => !busy && inputRef.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !busy) {
            inputRef.current?.click();
          }
        }}
        className={[
          "group relative flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center transition",
          busy
            ? "cursor-progress border-ink-700 bg-ink-900/40"
            : dragging
            ? "cursor-copy border-accent-500 bg-accent-500/5"
            : "cursor-pointer border-ink-700 bg-ink-900/60 hover:border-ink-600 hover:bg-ink-900/80",
        ].join(" ")}
      >
        {busy ? (
          <div className="flex items-center gap-3 text-sm text-zinc-300">
            <Spinner />
            {stageLabel[stage] || "Working…"}
          </div>
        ) : (
          <>
            <div className="text-base font-medium text-zinc-200">
              Drop a track, or{" "}
              <span className="text-accent-400">browse</span>
            </div>
            <div className="text-xs text-zinc-500">
              WAV · MP3 · FLAC · OGG — up to 50&nbsp;MB
            </div>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          onChange={onChange}
          className="hidden"
          disabled={busy}
        />
      </div>
      {localError && (
        <div className="mt-3 rounded-lg border border-red-900/50 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {localError}
        </div>
      )}
    </div>
  );
}

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin text-accent-400" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}