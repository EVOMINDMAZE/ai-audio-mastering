// ReferenceMasterZone.tsx — Matchering reference-based mastering UI.
// User picks a target audio file + a reference audio file; backend runs
// Matchering to match the target's sonic profile to the reference.
//
// Polling pattern (exponential backoff on 502/503/504) is delegated to the
// `referenceMasterStatus` helper in `../api` — this component just calls it
// on a 1 s cadence until the status flips to "ready" or "error".

import { useEffect, useRef, useState } from "react";
import {
  referenceMasterStart,
  referenceMasterStatus,
  referenceMasterDownloadUrl,
  type ReferenceMasterStartResponse,
  type ReferenceMasterStatusResponse,
} from "../api";

type Phase = "idle" | "uploading" | "queued" | "processing" | "ready" | "error";

const PRIMARY_BG = "#2563eb";
const DANGER = "#ef4444";
const SUCCESS = "#22c55e";
const TEXT_MUTED = "#9ca3af";

const buttonStyle = (enabled: boolean, color = PRIMARY_BG): React.CSSProperties => ({
  padding: "10px 20px",
  borderRadius: 6,
  border: "none",
  background: enabled ? color : "#374151",
  color: "#fff",
  fontWeight: 600,
  cursor: enabled ? "pointer" : "not-allowed",
  opacity: enabled ? 1 : 0.6,
});

const cardStyle: React.CSSProperties = {
  padding: 16,
  border: "1px solid rgba(255,255,255,0.1)",
  borderRadius: 8,
  background: "rgba(255,255,255,0.02)",
  marginBottom: 16,
};

export function ReferenceMasterZone() {
  const [target, setTarget] = useState<File | null>(null);
  const [reference, setReference] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [status, setStatus] = useState<ReferenceMasterStatusResponse | null>(null);
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current != null) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => {
    return () => stopPolling();
  }, []);

  async function handleSubmit() {
    if (!target || !reference) return;
    setError(null);
    setStatus(null);
    setJobId(null);
    setPhase("uploading");
    try {
      const start: ReferenceMasterStartResponse = await referenceMasterStart(target, reference);
      setJobId(start.job_id);
      setPhase("queued");
    } catch (e) {
      setError((e as Error).message);
      setPhase("error");
    }
  }

  // Poll status while a job is in flight. The api helper already retries
  // 502/503/504 with exponential backoff, so we only need to handle the
  // 404 "job lost" case and fatal server errors here.
  useEffect(() => {
    if (!jobId) return;
    if (phase === "ready" || phase === "error") return;

    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      try {
        const s = await referenceMasterStatus(jobId);
        if (cancelled) return;
        setStatus(s);
        if (s.status === "ready") {
          setPhase("ready");
          return;
        }
        if (s.status === "error") {
          setError(s.error ?? "Reference mastering failed.");
          setPhase("error");
          return;
        }
        setPhase(s.status === "queued" ? "queued" : "processing");
      } catch (e) {
        // 404 "job lost" is fatal — surface immediately rather than spinning.
        const msg = (e as Error).message;
        if (msg.includes("Job lost") || msg.includes("Job not found")) {
          setError(msg);
          setPhase("error");
          return;
        }
        // Other transient errors: log and keep polling.
        console.warn("reference-master status poll error:", e);
      }
      if (!cancelled) {
        pollRef.current = window.setTimeout(tick, 2000);
      }
    };

    pollRef.current = window.setTimeout(tick, 1000);
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [jobId, phase]);

  const ready = phase === "ready" && status;
  const outputMetrics = ready && status?.variants?.[0]?.metrics;
  const genreLabel = status?.metadata?.genre?.label ?? null;

  return (
    <div style={cardStyle}>
      <h3 style={{ margin: 0, marginBottom: 4, fontSize: 16, fontWeight: 600 }}>
        Reference Mastering
      </h3>
      <p style={{ margin: 0, marginBottom: 12, color: TEXT_MUTED, fontSize: 13 }}>
        Upload your mix plus a reference track. Matchering will match your target's loudness,
        EQ, and stereo image to the reference.
      </p>

      {/* ---- File inputs ----------------------------------------------- */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          marginBottom: 12,
        }}
      >
        <label style={{ display: "block" }}>
          <span style={{ fontSize: 13, color: TEXT_MUTED }}>Target (your mix)</span>
          <input
            type="file"
            accept="audio/*"
            onChange={(e) => setTarget(e.target.files?.[0] ?? null)}
            disabled={
              phase === "uploading" || phase === "queued" || phase === "processing"
            }
            style={{ display: "block", marginTop: 4, width: "100%" }}
          />
          {target && (
            <span style={{ fontSize: 12, color: TEXT_MUTED }}>
              {(target.size / 1024 / 1024).toFixed(2)} MB
            </span>
          )}
        </label>
        <label style={{ display: "block" }}>
          <span style={{ fontSize: 13, color: TEXT_MUTED }}>
            Reference (the sound you want)
          </span>
          <input
            type="file"
            accept="audio/*"
            onChange={(e) => setReference(e.target.files?.[0] ?? null)}
            disabled={
              phase === "uploading" || phase === "queued" || phase === "processing"
            }
            style={{ display: "block", marginTop: 4, width: "100%" }}
          />
          {reference && (
            <span style={{ fontSize: 12, color: TEXT_MUTED }}>
              {(reference.size / 1024 / 1024).toFixed(2)} MB
            </span>
          )}
        </label>
      </div>

      <button
        onClick={handleSubmit}
        disabled={
          !target ||
          !reference ||
          phase === "uploading" ||
          phase === "queued" ||
          phase === "processing"
        }
        style={buttonStyle(!!target && !!reference)}
      >
        {phase === "uploading" ? "Uploading…" : "Match to reference"}
      </button>

      {/* ---- Status / progress ----------------------------------------- */}
      {(phase === "queued" || phase === "processing") && (
        <p style={{ marginTop: 12, color: TEXT_MUTED, fontSize: 13 }}>
          {phase === "queued" ? "Queued…" : "Matchering is running…"}
        </p>
      )}

      {error && (
        <div
          style={{
            marginTop: 12,
            padding: 10,
            background: "rgba(239,68,68,0.1)",
            borderRadius: 6,
            color: DANGER,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      {/* ---- Results --------------------------------------------------- */}
      {ready && outputMetrics && (
        <div style={{ marginTop: 16 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
                <th style={{ textAlign: "left", padding: 6, color: TEXT_MUTED }}>Metric</th>
                <th style={{ textAlign: "right", padding: 6, color: TEXT_MUTED }}>Input</th>
                <th style={{ textAlign: "right", padding: 6, color: TEXT_MUTED }}>Output</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td style={{ padding: 6 }}>LUFS</td>
                <td style={{ padding: 6, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                  {outputMetrics.in_lufs != null ? outputMetrics.in_lufs.toFixed(2) : "—"}
                </td>
                <td
                  style={{
                    padding: 6,
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                    color: SUCCESS,
                    fontWeight: 600,
                  }}
                >
                  {outputMetrics.out_lufs != null ? outputMetrics.out_lufs.toFixed(2) : "—"}
                </td>
              </tr>
              <tr>
                <td style={{ padding: 6 }}>True peak (dBTP)</td>
                <td style={{ padding: 6, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                  {outputMetrics.in_peak_dbtp != null
                    ? outputMetrics.in_peak_dbtp.toFixed(2)
                    : "—"}
                </td>
                <td
                  style={{
                    padding: 6,
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                    color: SUCCESS,
                    fontWeight: 600,
                  }}
                >
                  {outputMetrics.out_peak_dbtp != null
                    ? outputMetrics.out_peak_dbtp.toFixed(2)
                    : "—"}
                </td>
              </tr>
              <tr>
                <td style={{ padding: 6 }}>Applied gain (dB)</td>
                <td style={{ padding: 6, textAlign: "right" }}>—</td>
                <td style={{ padding: 6, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                  {outputMetrics.applied_gain_db != null
                    ? outputMetrics.applied_gain_db.toFixed(2)
                    : "—"}
                </td>
              </tr>
              <tr>
                <td style={{ padding: 6 }}>Limiter reduction (dB)</td>
                <td style={{ padding: 6, textAlign: "right" }}>—</td>
                <td style={{ padding: 6, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                  {outputMetrics.limiter_reduction_db != null
                    ? outputMetrics.limiter_reduction_db.toFixed(2)
                    : "—"}
                </td>
              </tr>
            </tbody>
          </table>

          {genreLabel && (
            <p style={{ marginTop: 8, fontSize: 13, color: TEXT_MUTED }}>
              Predicted genre: <strong style={{ color: "#fff" }}>{genreLabel}</strong>
            </p>
          )}

          <a
            href={jobId ? referenceMasterDownloadUrl(jobId) : "#"}
            download
            style={{
              ...buttonStyle(true, SUCCESS),
              display: "inline-block",
              textDecoration: "none",
              marginTop: 12,
            }}
          >
            Download matched WAV
          </a>
        </div>
      )}
    </div>
  );
}

export default ReferenceMasterZone;