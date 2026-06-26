import type { AnalysisResult } from "../types";

interface Props {
  analysis: AnalysisResult;
}

/**
 * Results dashboard. Shows the key audio metrics as labeled cards plus a
 * flag strip (mud / clipping) and the top spectrum peaks.
 *
 * No charting library — the spectrum peaks are rendered as a tiny SVG bar
 * plot to keep the bundle small.
 */
export default function AnalysisDashboard({ analysis }: Props) {
  const a = analysis;

  return (
    <section className="rounded-2xl border border-ink-700 bg-ink-900/60 p-5 shadow-2xl shadow-black/40">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
          Analysis
        </h2>
        <div className="flex items-center gap-2">
          <Flag label="Muddy" active={a.mud_flag} tone="amber" />
          <Flag label="Clipping" active={a.clipping_flag} tone="red" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Metric label="LUFS (Integrated)" value={a.lufs_integrated} unit="LUFS" digits={1} />
        <Metric label="True Peak" value={a.true_peak_dbtp} unit="dBTP" digits={1} />
        <Metric label="Sample Peak" value={a.peak_dbfs} unit="dBFS" digits={1} />
        <Metric label="RMS" value={a.rms_dbfs} unit="dBFS" digits={1} />
        <Metric label="BPM" value={a.bpm} unit="" digits={1} />
        <Metric label="Duration" value={a.duration_s} unit="s" digits={2} />
      </div>

      <div className="divider my-5" />

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Top Spectrum Peaks
          </h3>
          <span className="text-xs text-zinc-600">SR {a.sample_rate.toLocaleString()} Hz</span>
        </div>
        <SpectrumPlot peaks={a.spectrum_peaks} />
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  unit,
  digits,
}: {
  label: string;
  value: number;
  unit: string;
  digits: number;
}) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-800/50 px-4 py-3">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="font-mono text-xl font-medium text-zinc-100">
          {Number.isFinite(value) ? value.toFixed(digits) : "—"}
        </span>
        {unit && <span className="text-xs text-zinc-500">{unit}</span>}
      </div>
    </div>
  );
}

type FlagTone = "amber" | "red";

function Flag({ label, active, tone = "amber" }: { label: string; active: boolean; tone?: FlagTone }) {
  const activeClass =
    tone === "red"
      ? "bg-red-500/10 text-red-300 ring-1 ring-red-500/40"
      : "bg-amber-500/10 text-amber-300 ring-1 ring-amber-500/30";
  const dotClass = tone === "red" ? "bg-red-300" : "bg-amber-300";

  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider transition",
        active
          ? activeClass
          : "bg-emerald-500/10 text-emerald-300 ring-1 ring-emerald-500/20",
      ].join(" ")}
      title={active ? `Warning: ${label.toLowerCase()} threshold exceeded` : `${label}: OK`}
    >
      <span
        className={[
          "h-1.5 w-1.5 rounded-full",
          active ? dotClass : "bg-emerald-300",
        ].join(" ")}
      />
      {label}
    </span>
  );
}

function SpectrumPlot({
  peaks,
}: {
  peaks: { frequency_hz: number; magnitude_db: number }[];
}) {
  if (!peaks.length) {
    return <div className="text-xs text-zinc-600">No spectrum data.</div>;
  }

  // Log-frequency axis 20Hz–20kHz.
  const minF = 20;
  const maxF = 20000;
  const logMin = Math.log10(minF);
  const logMax = Math.log10(maxF);
  const xOf = (f: number) =>
    ((Math.log10(Math.max(f, minF)) - logMin) / (logMax - logMin)) * 100;

  // Normalize magnitude to [0,1] for bar height.
  const maxMag = Math.max(...peaks.map((p) => p.magnitude_db));
  const minMag = Math.min(...peaks.map((p) => p.magnitude_db));
  const range = Math.max(maxMag - minMag, 1);
  const hOf = (db: number) => ((db - minMag) / range) * 100;

  return (
    <div className="rounded-lg border border-ink-700 bg-ink-800/50 p-4">
      <div className="relative h-24 w-full">
        {/* Decorative log-spaced vertical guides */}
        {[100, 1000, 10000].map((f) => (
          <div
            key={f}
            className="absolute top-0 bottom-0 w-px bg-ink-700"
            style={{ left: `${xOf(f)}%` }}
          />
        ))}
        {peaks.map((p, i) => {
          const left = xOf(p.frequency_hz);
          const height = Math.max(8, hOf(p.magnitude_db));
          return (
            <div
              key={i}
              className="absolute bottom-0 w-1 rounded-sm bg-accent-500/80"
              style={{
                left: `calc(${left}% - 2px)`,
                height: `${height}%`,
              }}
              title={`${p.frequency_hz.toFixed(0)} Hz · ${p.magnitude_db.toFixed(1)} dB`}
            />
          );
        })}
      </div>
      <div className="mt-2 flex justify-between font-mono text-[10px] text-zinc-600">
        <span>20 Hz</span>
        <span>200 Hz</span>
        <span>2 kHz</span>
        <span>20 kHz</span>
      </div>
    </div>
  );
}