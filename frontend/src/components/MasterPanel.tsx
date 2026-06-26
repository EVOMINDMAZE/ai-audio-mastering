import { useEffect, useMemo, useRef, useState } from "react";
import type { MasterResult, MasterVariant } from "../types";
import { fetchInChunks } from "../lib/download";

interface Props {
  status: "analyzing" | "analyzed" | "mastering" | "mastered" | "idle" | "error";
  onMaster: () => void;
  result: MasterResult | null;
  partialVariants: MasterVariant[];
  totalPresets: number;
  originalUrl: string | null;
  error: string | null;
}

/**
 * Mastering panel: Master button + per-preset switcher + A/B compare + custom audio player.
 *
 * Async-aware: while a master job is rendering, we receive `partialVariants`
 * (the variants that have finished so far) from the useMasterJob hook. Chips
 * whose preset_id is in partialVariants are clickable; the rest show a spinner.
 */
export default function MasterPanel({
  status,
  onMaster,
  result,
  partialVariants,
  totalPresets,
  originalUrl,
  error,
}: Props) {
  const busy = status === "mastering";

  return (
    <section className="rounded-2xl border border-ink-700 bg-ink-900/60 p-5 shadow-2xl shadow-black/40">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
            Master
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            {totalPresets} curated presets — rendered in parallel; switch and A/B in real time.
          </p>
        </div>
        <button
          onClick={onMaster}
          disabled={busy}
          className={[
            "inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition",
            busy
              ? "cursor-not-allowed bg-ink-700 text-zinc-400"
              : "bg-accent-500 text-white shadow-lg shadow-accent-600/30 hover:bg-accent-400 active:scale-[0.98]",
          ].join(" ")}
        >
          {busy ? (
            <>
              <Spinner /> Rendering {partialVariants.length}/{totalPresets}…
            </>
          ) : result ? (
            <>
              <RefreshIcon /> Re-master
            </>
          ) : (
            <>
              <SparkIcon /> Master
            </>
          )}
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-900/50 bg-red-950/40 px-4 py-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {originalUrl && (result || partialVariants.length > 0) && (
        <PresetPlayer
          result={result}
          partialVariants={partialVariants}
          totalPresets={totalPresets}
          originalUrl={originalUrl}
        />
      )}

      {busy && partialVariants.length === 0 && (
        <div className="mt-5 flex items-center gap-3 rounded-lg border border-ink-700 bg-ink-800/40 px-4 py-3 text-xs text-zinc-400">
          <Spinner />
          <span>
            Submitting job — first preset will appear in a few seconds. The Master
            button stays clickable once the upload returns (≈ 1 s).
          </span>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Preset switcher + A/B player
// ---------------------------------------------------------------------------

type Source = "original" | "mastered";
type SourceKey = "original" | string;

interface LoadedTrack {
  key: SourceKey;
  label: string;
  url: string;
  kind: "original" | "mastered";
  variant?: MasterVariant;
}

function PresetPlayer({
  result,
  partialVariants,
  totalPresets,
  originalUrl,
}: {
  result: MasterResult | null;
  partialVariants: MasterVariant[];
  totalPresets: number;
  originalUrl: string;
}) {
  const variants = result?.variants ?? partialVariants;
  const [activePresetId, setActivePresetId] = useState<string | null>(variants[0]?.preset_id ?? null);
  const [source, setSource] = useState<Source>("mastered");
  const [loadedPresetUrls, setLoadedPresetUrls] = useState<Record<string, string>>({});
  const [preloadError, setPreloadError] = useState<string | null>(null);
  const [preloadCount, setPreloadCount] = useState(0);
  const loadedUrlRef = useRef<string[]>([]);

  useEffect(() => {
    if (activePresetId === null && variants.length > 0) {
      setActivePresetId(variants[0].preset_id);
    }
  }, [variants, activePresetId]);

  const activeVariant: MasterVariant | undefined = useMemo(
    () => variants.find((v) => v.preset_id === activePresetId) ?? variants[0],
    [variants, activePresetId]
  );

  const cleanupLoadedUrls = () => {
    for (const url of loadedUrlRef.current) URL.revokeObjectURL(url);
    loadedUrlRef.current = [];
  };

  useEffect(() => {
    if (!result) return;

    const controller = new AbortController();
    let cancelled = false;
    cleanupLoadedUrls();
    setLoadedPresetUrls({});
    setPreloadError(null);
    setPreloadCount(0);

    void (async () => {
      try {
        const entries: [string, string][] = [];
        // Download sequentially to avoid hitting browser/proxy concurrent connection
        // limits for large WAV files. Also gives smooth progress updates.
        for (const variant of result.variants) {
          if (cancelled) break;
          let blob: Blob;
          try {
            blob = await fetchInChunks(variant.download_url, controller.signal);
          } catch (err) {
            throw new Error(`Failed to load ${variant.label}: ${err instanceof Error ? err.message : String(err)}`);
          }
          if (cancelled) break;
          const url = URL.createObjectURL(blob);
          loadedUrlRef.current.push(url);
          setPreloadCount((count) => count + 1);
          entries.push([variant.preset_id, url]);
        }
        if (cancelled) return;
        setLoadedPresetUrls(Object.fromEntries(entries));
      } catch (e) {
        if (cancelled || controller.signal.aborted) return;
        cleanupLoadedUrls();
        setLoadedPresetUrls({});
        setPreloadError(e instanceof Error ? e.message : "Failed to load mastered audio.");
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
      cleanupLoadedUrls();
    };
  }, [result]);

  useEffect(() => {
    return () => cleanupLoadedUrls();
  }, []);

  if (!activeVariant) {
    return (
      <div className="mt-5 rounded-lg border border-ink-700 bg-ink-800/40 p-4 text-sm text-zinc-400">
        Rendering your first preset…
      </div>
    );
  }

  const readyForInstantAB = result !== null && Object.keys(loadedPresetUrls).length === totalPresets;
  const loadedTracks: LoadedTrack[] = useMemo(() => {
    if (!readyForInstantAB || !result) return [];
    return [
      { key: "original", label: "Original (unmastered)", url: originalUrl, kind: "original" as const },
      ...result.variants.map((variant) => ({
        key: variant.preset_id,
        label: `Mastered · ${variant.label}`,
        url: loadedPresetUrls[variant.preset_id],
        kind: "mastered" as const,
        variant,
      })),
    ].filter((track) => Boolean(track.url));
  }, [loadedPresetUrls, originalUrl, readyForInstantAB, result]);

  return (
    <div className="mt-5 space-y-4">
      <div className="flex items-center gap-3 rounded-lg border border-ink-700 bg-ink-800/40 p-1.5">
        <SourceButton
          label="Original"
          active={source === "original"}
          onClick={() => setSource("original")}
          disabled={!readyForInstantAB}
        />
        <SourceButton
          label="Mastered"
          active={source === "mastered"}
          onClick={() => setSource("mastered")}
          disabled={!readyForInstantAB}
        />
        <span className="ml-auto text-[10px] uppercase tracking-wider text-zinc-500">
          A / B compare
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {variants.map((v) => (
          <PresetChip
            key={v.preset_id}
            variant={v}
            active={v.preset_id === activePresetId}
            disabled={!result}
            onClick={() => setActivePresetId(v.preset_id)}
          />
        ))}
        {result === null && totalPresetsPlaceholder(partialVariants.length, totalPresets).map((i) => (
          <PendingChip key={`pending-${i}`} index={i} />
        ))}
      </div>

      {result === null && (
        <div className="rounded-xl border border-ink-700 bg-ink-800/60 p-4">
          <div className="flex items-center gap-2 text-sm text-zinc-300">
            <Spinner />
            <span>Rendering {partialVariants.length}/{totalPresets} presets...</span>
          </div>
        </div>
      )}

      {result !== null && !readyForInstantAB && (
        <div className="rounded-xl border border-ink-700 bg-ink-800/60 p-4">
          {preloadError ? (
            <div className="text-sm text-red-300">{preloadError}</div>
          ) : (
            <div className="flex items-center gap-2 text-sm text-zinc-300">
              <Spinner />
              <span>
                Loading audio {preloadCount}/{totalPresets}...
              </span>
            </div>
          )}
          <div className="mt-2 text-[10px] uppercase tracking-wider text-zinc-500">
            Playback unlocks once all presets are fully loaded for instant A/B.
          </div>
        </div>
      )}

      {readyForInstantAB && (
        <>
          <div className="rounded-lg border border-emerald-900/40 bg-emerald-950/20 px-3 py-2 text-[11px] uppercase tracking-wider text-emerald-300">
            Ready for instant A/B
          </div>
          <SyncedPlayer
            tracks={loadedTracks}
            activeKey={source === "original" ? "original" : activeVariant.preset_id}
            sourceLabel={source === "original" ? "Original (unmastered)" : `Mastered · ${activeVariant.label}`}
            downloadHref={source === "mastered" ? activeVariant.download_url : undefined}
            downloadName={source === "mastered" ? filenameFor(activeVariant) : undefined}
          />
        </>
      )}

      {source === "mastered" && <PresetMetrics variant={activeVariant} />}
    </div>
  );
}

function filenameFor(v: MasterVariant): string {
  // Use the filename embedded in the download URL: /api/download/{job_id}/{preset_id}
  const parts = v.download_url.split("/");
  const jobId = parts[parts.length - 2];
  return `${jobId}_${v.preset_id}_mastered.wav`;
}

function totalPresetsPlaceholder(ready: number, total: number): number[] {
  const out: number[] = [];
  for (let i = ready; i < total; i++) out.push(i);
  return out;
}

function SourceButton({
  label,
  active,
  onClick,
  disabled = false,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={[
        "flex-1 rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition",
        active
          ? "bg-accent-500/20 text-accent-300 ring-1 ring-accent-500/40"
          : "text-zinc-400 hover:bg-ink-700/60 hover:text-zinc-200",
        disabled ? "cursor-not-allowed opacity-40 hover:bg-transparent hover:text-zinc-400" : "",
      ].join(" ")}
    >
      {label}
    </button>
  );
}

function PresetChip({
  variant,
  active,
  disabled,
  onClick,
}: {
  variant: MasterVariant;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={variant.description}
      className={[
        "rounded-full border px-3 py-1.5 text-xs font-medium transition",
        active
          ? "border-accent-500/60 bg-accent-500/15 text-accent-200"
          : "border-ink-600 bg-ink-800/40 text-zinc-300 hover:border-ink-500 hover:bg-ink-800",
        disabled ? "cursor-not-allowed opacity-40 hover:bg-ink-800/40" : "",
      ].join(" ")}
    >
      {variant.label}
    </button>
  );
}

function PendingChip({ index }: { index: number }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-dashed border-ink-700 bg-ink-800/20 px-3 py-1.5 text-xs text-zinc-500"
      title="Rendering…"
    >
      <Spinner small />
      <span>Rendering…</span>
      <span className="sr-only">{`preset ${index}`}</span>
    </span>
  );
}

function PresetMetrics({ variant }: { variant: MasterVariant }) {
  const m = variant.metrics;
  const cells = [
    { label: "Loudness", before: m.in_lufs, after: m.out_lufs, unit: "LUFS", digits: 1 },
    { label: "True Peak", before: m.in_peak_dbtp, after: m.out_peak_dbtp, unit: "dBTP", digits: 1 },
  ];
  const gain = m.applied_gain_db;
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-800/50 p-3">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
        {variant.label} — {variant.description}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {cells.map((c) => (
          <div key={c.label} className="rounded-md border border-ink-700 bg-ink-800/40 px-3 py-2">
            <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
              {c.label}
            </div>
            <div className="mt-0.5 grid grid-cols-[1fr_auto_1fr] items-baseline gap-2 font-mono">
              <span className="text-xs text-zinc-400">{c.before.toFixed(c.digits)}</span>
              <Arrow />
              <span className="text-sm font-medium text-accent-400">
                {c.after.toFixed(c.digits)}
                <span className="ml-1 text-[10px] text-zinc-500">{c.unit}</span>
              </span>
            </div>
          </div>
        ))}
        <div className="rounded-md border border-ink-700 bg-ink-800/40 px-3 py-2">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Normalization Gain
          </div>
          <div className="mt-0.5 font-mono text-sm font-medium text-zinc-100">
            {gain >= 0 ? "+" : ""}
            {gain.toFixed(2)}
            <span className="ml-1 text-[10px] text-zinc-500">dB</span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Synchronized multi-audio player
// ---------------------------------------------------------------------------

function SyncedPlayer({
  tracks,
  activeKey,
  sourceLabel,
  downloadHref,
  downloadName,
}: {
  tracks: LoadedTrack[];
  activeKey: SourceKey;
  sourceLabel: string;
  downloadHref?: string;
  downloadName?: string;
}) {
  const audioRefs = useRef<Record<string, HTMLAudioElement | null>>({});
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(0);
  const [readyKeys, setReadyKeys] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setPlaying(false);
    setCurrent(0);
    setDuration(0);
    setReadyKeys({});
  }, [tracks]);

  const allReady = tracks.length > 0 && tracks.every((track) => readyKeys[track.key]);

  useEffect(() => {
    const activeEl = audioRefs.current[activeKey];
    const baseTime = activeEl?.currentTime ?? 0;
    for (const track of tracks) {
      const el = audioRefs.current[track.key];
      if (!el) continue;
      if (track.key !== activeKey && Math.abs(el.currentTime - baseTime) > 0.08) {
        el.currentTime = baseTime;
      }
      el.muted = track.key !== activeKey;
      el.volume = track.key === activeKey ? 1 : 0;
    }
    if (Number.isFinite(baseTime)) {
      setCurrent(baseTime);
    }
    if (activeEl?.duration) {
      setDuration(activeEl.duration);
    }
  }, [activeKey, tracks]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.code !== "Space") return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      e.preventDefault();
      toggle();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function syncAllTo(t: number) {
    const clamped = Math.max(0, Math.min(t, duration || t));
    for (const track of tracks) {
      const el = audioRefs.current[track.key];
      if (!el) continue;
      try {
        el.currentTime = clamped;
      } catch {
        // Ignore transient seek errors until metadata is ready.
      }
    }
    setCurrent(clamped);
  }

  async function playAll() {
    const base = audioRefs.current[activeKey]?.currentTime ?? current;
    syncAllTo(base);
    const promises = tracks.map(async (track) => {
      const el = audioRefs.current[track.key];
      if (!el) return;
      el.muted = track.key !== activeKey;
      el.volume = track.key === activeKey ? 1 : 0;
      await el.play();
    });
    const results = await Promise.allSettled(promises);
    const failed = results.some((r) => r.status === "rejected");
    setPlaying(!failed);
  }

  function pauseAll() {
    for (const track of tracks) {
      const el = audioRefs.current[track.key];
      el?.pause();
    }
    setPlaying(false);
  }

  function toggle() {
    if (!allReady) return;
    if (playing) {
      pauseAll();
    } else {
      void playAll();
    }
  }

  function seekTo(t: number) {
    syncAllTo(t);
  }

  function skip(deltaSec: number) {
    seekTo((audioRefs.current[activeKey]?.currentTime ?? current) + deltaSec);
  }

  function onSeekBarClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    seekTo(ratio * duration);
  }

  const pct = duration > 0 ? (current / duration) * 100 : 0;

  return (
    <div className="rounded-xl border border-ink-700 bg-ink-800/60 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
            Now Playing
          </div>
          <div className="truncate text-sm font-medium text-zinc-200">{sourceLabel}</div>
        </div>
        {downloadHref && downloadName && (
          <a
            href={downloadHref}
            download={downloadName}
            className="inline-flex items-center gap-1.5 rounded-md border border-ink-600 bg-ink-700 px-2.5 py-1 text-xs font-medium text-zinc-200 transition hover:bg-ink-600"
          >
            <DownloadIcon /> Download .wav
          </a>
        )}
      </div>

      <div
        onClick={onSeekBarClick}
        className="group relative h-2 w-full cursor-pointer overflow-hidden rounded-full bg-ink-700"
      >
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-accent-500 transition-[width] duration-100"
          style={{ width: `${pct}%` }}
        />
        <div
          className="absolute top-1/2 h-3 w-3 -translate-y-1/2 -translate-x-1/2 rounded-full bg-accent-300 opacity-0 shadow-md transition group-hover:opacity-100"
          style={{ left: `${pct}%` }}
        />
      </div>

      <div className="mt-1 flex items-center justify-between font-mono text-[10px] text-zinc-500">
        <span>{formatTime(current)}</span>
        <span>{formatTime(duration)}</span>
      </div>

      <div className="mt-3 flex items-center justify-center gap-2">
        <button
          onClick={() => skip(-10)}
          aria-label="Back 10 seconds"
          className="grid h-9 w-9 place-items-center rounded-full border border-ink-600 bg-ink-700 text-zinc-200 transition hover:bg-ink-600"
        >
          <SkipBackIcon />
        </button>
        <button
          onClick={toggle}
          aria-label={playing ? "Pause" : "Play"}
          className="grid h-11 w-11 place-items-center rounded-full bg-accent-500 text-white shadow-lg shadow-accent-600/30 transition hover:bg-accent-400 active:scale-95"
        >
          {playing ? <PauseIcon /> : <PlayIcon />}
        </button>
        <button
          onClick={() => skip(10)}
          aria-label="Forward 10 seconds"
          className="grid h-9 w-9 place-items-center rounded-full border border-ink-600 bg-ink-700 text-zinc-200 transition hover:bg-ink-600"
        >
          <SkipForwardIcon />
        </button>
      </div>

      {!allReady && (
        <div className="mt-2 text-center text-[10px] text-zinc-600">Loading audio bank…</div>
      )}

      {tracks.map((track) => (
        <audio
          key={track.key}
          ref={(el) => {
            audioRefs.current[track.key] = el;
          }}
          src={track.url}
          preload="auto"
          muted={track.key !== activeKey}
          onLoadedMetadata={(e) => {
            setReadyKeys((prev) => ({ ...prev, [track.key]: true }));
            if (track.key === activeKey) {
              setDuration(e.currentTarget.duration || 0);
            }
          }}
          onTimeUpdate={(e) => {
            if (track.key !== activeKey) return;
            const next = e.currentTarget.currentTime;
            setCurrent(next);
            for (const other of tracks) {
              if (other.key === track.key) continue;
              const el = audioRefs.current[other.key];
              if (!el) continue;
              if (Math.abs(el.currentTime - next) > 0.12) {
                el.currentTime = next;
              }
            }
          }}
          onPlay={() => {
            if (track.key === activeKey) setPlaying(true);
          }}
          onPause={() => {
            if (track.key === activeKey) setPlaying(false);
          }}
          onEnded={() => {
            if (track.key === activeKey) {
              pauseAll();
              setCurrent(audioRefs.current[track.key]?.duration ?? duration);
            }
          }}
          className="hidden"
        />
      ))}
    </div>
  );
}

function formatTime(s: number): string {
  if (!Number.isFinite(s) || s < 0) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function Arrow() {
  return (
    <svg viewBox="0 0 16 16" className="h-3 w-3 text-zinc-600" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 8h10" />
      <path d="m9 4 4 4-4 4" />
    </svg>
  );
}

function Spinner({ small = false }: { small?: boolean }) {
  const sz = small ? "h-3 w-3" : "h-4 w-4";
  return (
    <svg className={`${sz} animate-spin`} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.3" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

function SparkIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2v4" />
      <path d="M12 18v4" />
      <path d="m4.93 4.93 2.83 2.83" />
      <path d="m16.24 16.24 2.83 2.83" />
      <path d="M2 12h4" />
      <path d="M18 12h4" />
      <path d="m4.93 19.07 2.83-2.83" />
      <path d="m16.24 7.76 2.83-2.83" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12a9 9 0 0 1 15.5-6.3L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.5 6.3L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v12" />
      <path d="m6 9 6 6 6-6" />
      <path d="M5 21h14" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor">
      <path d="M8 5v14l11-7z" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="currentColor">
      <rect x="6" y="5" width="4" height="14" rx="1" />
      <rect x="14" y="5" width="4" height="14" rx="1" />
    </svg>
  );
}

function SkipBackIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 19 2 12l9-7z" />
      <path d="M22 19 13 12l9-7z" />
    </svg>
  );
}

function SkipForwardIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m13 19 9-7-9-7z" />
      <path d="M2 19l9-7-9-7z" />
    </svg>
  );
}
