import { useCallback, useEffect, useRef, useState } from "react";
import {
  aiMasterDownloadUrl,
  aiMasterStart,
  aiMasterStatus,
  type AiMasterStartResponse,
  type AiMasterStatusResponse,
} from "../api";
import { AnalysisExtendedPanel } from "./AnalysisExtendedPanel";

type Phase = "idle" | "submitting" | "polling" | "ready" | "error";

function AIMasterZoneImpl() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [file, setFile] = useState<File | null>(null);
  const [startResp, setStartResp] = useState<AiMasterStartResponse | null>(null);
  const [status, setStatus] = useState<AiMasterStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef(false);

  const onPick = (f: File | null) => {
    setFile(f);
    setError(null);
    setStartResp(null);
    setStatus(null);
    setPhase("idle");
  };

  const submit = useCallback(async () => {
    if (!file) return;
    setPhase("submitting");
    setError(null);
    try {
      const start = await aiMasterStart(file);
      setStartResp(start);
      setPhase("polling");
      stopRef.current = false;
      // Poll until ready or error. Single output (no preset loop), so 1s cadence is fine.
      for (;;) {
        if (stopRef.current) return;
        await new Promise((r) => setTimeout(r, 1000));
        const s = await aiMasterStatus(start.job_id);
        setStatus(s);
        if (s.status === "ready" || s.status === "error") break;
      }
      const final = await aiMasterStatus(start.job_id);
      setStatus(final);
      if (final.status === "ready") setPhase("ready");
      else setPhase("error");
    } catch (e) {
      setError((e as Error).message);
      setPhase("error");
    }
  }, [file]);

  useEffect(() => {
    return () => {
      stopRef.current = true;
    };
  }, []);

  const readyVariant = status?.variants?.[0];
  const inFeats = startResp?.input_features;

  return (
    <section className="ai-master-zone">
      <header>
        <h2>AI Mastering</h2>
        <p className="muted">
          Upload an audio file. The backend analyses it (LUFS, peak, BPM, mud), asks an LLM to
          pick the best of the 11 mastering presets and tune the parameters, then renders once.
        </p>
      </header>

      <div className="row">
        <input
          type="file"
          accept="audio/*"
          onChange={(e) => onPick(e.target.files?.[0] ?? null)}
          disabled={phase === "submitting" || phase === "polling"}
        />
        <button
          onClick={submit}
          disabled={!file || phase === "submitting" || phase === "polling"}
        >
          {phase === "submitting" ? "Submitting…" : "AI Master"}
        </button>
      </div>

      {error && <div className="error">{error}</div>}

      {startResp && (
        <div className="recommendation">
          <h3>AI recommendation</h3>
          <p>
            <strong>Source:</strong> {startResp.source === "llm" ? "DeepSeek LLM" : "Heuristic fallback (LLM unavailable)"}
            {" · "}
            <strong>Preset:</strong> {startResp.preset_id}
          </p>
          {startResp.reasoning && <p className="reasoning">"{startResp.reasoning}"</p>}

          {inFeats && (
            <details>
              <summary>Input features</summary>
              <ul className="features">
                <li>LUFS: {fmt(inFeats.lufs, "dB")}</li>
                <li>True peak: {fmt(inFeats.peak_dbtp, "dBTP")}</li>
                <li>BPM: {fmt(inFeats.bpm)}</li>
                <li>Mud flag: {String(inFeats.mud_flag ?? "—")}</li>
                <li>Clipping flag: {String(inFeats.clipping_flag ?? "—")}</li>
                <li>Duration: {fmt(inFeats.duration_s, "s")}</li>
              </ul>
            </details>
          )}

          {inFeats?.genre && (
            <AnalysisExtendedPanel
              data={{
                genre: inFeats.genre,
              }}
            />
          )}

          {Object.keys(startResp.overrides).length > 0 && (
            <details open>
              <summary>Parameter overrides</summary>
              <ul className="overrides">
                {Object.entries(startResp.overrides).map(([k, v]) => (
                  <li key={k}>
                    <code>{k}</code>: {typeof v === "number" ? v.toFixed(2) : String(v)}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {phase === "polling" && (
        <div className="polling">
          Rendering with AI-tuned parameters… ({status?.status ?? "queued"})
        </div>
      )}

      {phase === "ready" && readyVariant && (
        <div className="ready">
          <h3>Ready</h3>
          {readyVariant.metrics && (
            <ul className="metrics">
              <li>in LUFS: {fmt(readyVariant.metrics.in_lufs)}</li>
              <li>out LUFS: {fmt(readyVariant.metrics.out_lufs)}</li>
              <li>in peak: {fmt(readyVariant.metrics.in_peak_dbtp, "dBTP")}</li>
              <li>out peak: {fmt(readyVariant.metrics.out_peak_dbtp, "dBTP")}</li>
              <li>applied gain: {fmt(readyVariant.metrics.applied_gain_db, "dB")}</li>
              <li>limiter reduction: {fmt(readyVariant.metrics.limiter_reduction_db, "dB")}</li>
            </ul>
          )}
          {startResp && (
            <a className="download" href={aiMasterDownloadUrl(startResp.job_id)} download>
              Download AI-mastered WAV
            </a>
          )}
        </div>
      )}
    </section>
  );
}

export default AIMasterZoneImpl;

function fmt(v: number | null | undefined, unit = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(2)}${unit ? " " + unit : ""}`;
}