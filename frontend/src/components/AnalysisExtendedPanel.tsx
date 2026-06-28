// AnalysisExtendedPanel.tsx — renders the 6 new extended-analysis features
// plus genre label. Drop into AIMasterZone as a <details> block under the
// existing input-features list.

import type { CSSProperties, ReactNode } from "react";

export interface ExtendedAnalysis {
  crest_factor_db?: number | null;
  stereo_width?: number | null;
  spectral_centroid_hz?: number | null;
  spectral_flatness?: number | null;
  band_energy_low_mid_high?: number[] | null;
  perceived_loudness_db?: number | null;
  genre?: string | null;
}

interface Props {
  data: ExtendedAnalysis | null | undefined;
}

const ROW: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  padding: "6px 0",
  borderBottom: "1px solid rgba(255,255,255,0.06)",
  fontSize: 14,
};

const LABEL: CSSProperties = { color: "#9ca3af" };
const VALUE: CSSProperties = { fontVariantNumeric: "tabular-nums" };

// Colour-coded hint based on a numeric value + thresholds.
function hint(value: number, lowYellow: number, highGreen: number): { text: string; color: string } {
  if (value < lowYellow) return { text: "compressed", color: "#ef4444" };
  if (value < highGreen) return { text: "moderate", color: "#eab308" };
  return { text: "dynamic", color: "#22c55e" };
}

function fmt(n: number | null | undefined, digits = 1, suffix = ""): string {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits)}${suffix}`;
}

function Row({ label, value, hint }: { label: string; value: ReactNode; hint?: { text: string; color: string } }) {
  return (
    <div style={ROW}>
      <span style={LABEL}>{label}</span>
      <span style={{ ...VALUE, display: "flex", gap: 8, alignItems: "center" }}>
        {value}
        {hint && (
          <span style={{ color: hint.color, fontSize: 12, fontWeight: 500 }}>
            {hint.text}
          </span>
        )}
      </span>
    </div>
  );
}

export function AnalysisExtendedPanel({ data }: Props) {
  if (!data) return null;

  const cf = data.crest_factor_db ?? null;
  const sw = data.stereo_width ?? null;
  const sc = data.spectral_centroid_hz ?? null;
  const sf = data.spectral_flatness ?? null;
  const bands = data.band_energy_low_mid_high ?? null;
  const pl = data.perceived_loudness_db ?? null;
  const genre = data.genre ?? null;

  const cfHint = cf != null ? hint(cf, 6, 10) : undefined;
  const swHint =
    sw == null ? undefined :
    sw < 0.1 ? { text: "mono", color: "#9ca3af" } :
    sw > 0.7 ? { text: "wide", color: "#22c55e" } :
    { text: "normal", color: "#9ca3af" };

  return (
    <details style={{ marginTop: 12 }}>
      <summary style={{ cursor: "pointer", color: "#9ca3af", fontSize: 13, userSelect: "none" }}>
        Show extended analysis
      </summary>
      <div style={{ marginTop: 8, padding: 8, background: "rgba(255,255,255,0.02)", borderRadius: 6 }}>
        <Row label="Crest factor" value={fmt(cf, 1, " dB")} hint={cfHint} />
        <Row label="Stereo width" value={fmt(sw, 2)} hint={swHint} />
        <Row label="Spectral centroid" value={fmt(sc, 0, " Hz")} />
        <Row label="Spectral flatness" value={fmt(sf, 3)} />
        <Row
          label="Band energy (L/M/H)"
          value={
            bands && bands.length === 3
              ? `${(bands[0] * 100).toFixed(0)} / ${(bands[1] * 100).toFixed(0)} / ${(bands[2] * 100).toFixed(0)} %`
              : "—"
          }
        />
        <Row label="A-weighted loudness" value={fmt(pl, 1, " dB")} />
        {genre && (
          <Row label="Genre (predicted)" value={genre} />
        )}
      </div>
    </details>
  );
}

export default AnalysisExtendedPanel;