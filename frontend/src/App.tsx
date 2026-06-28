import { useState } from "react";
import { analyze } from "./api";
import { useMasterJob } from "./useMasterJob";
import type { AnalysisResult, MasterResult } from "./types";
import UploadZone from "./components/UploadZone";
import BassBoostZone from "./components/BassBoostZone";
import AIMasterZone from "./components/AIMasterZone";
import { ReferenceMasterZone } from "./components/ReferenceMasterZone";
import AnalysisDashboard from "./components/AnalysisDashboard";
import MasterPanel from "./components/MasterPanel";

type Status = "idle" | "analyzing" | "analyzed" | "mastering" | "mastered" | "error";

/**
 * Top-level app shell. Holds the shared state machine:
 *
 *   idle -> analyzing -> analyzed -> mastering -> mastered
 *                     \-> error (retry)
 *
 * `master` button is disabled until analysis succeeds. Re-upload resets to idle.
 *
 * `originalUrl` is a Blob URL created from the uploaded File so the player
 * can A/B the unmastered input against the mastered output. It is revoked
 * on reset / re-upload to avoid leaking the underlying buffer.
 *
 * The master submission is async (POST /master returns job_id in ~1s; render
 * happens in a background thread on the server). The useMasterJob hook owns
 * the polling; `polling` doubles as the "mastering" status in the state machine.
 */
export default function App() {
  const [file, setFile] = useState<File | null>(null);
  const [originalUrl, setOriginalUrl] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);

  const {
    result: masterResult,
    partialVariants,
    error: masterError,
    polling: masterPolling,
    start: startMaster,
    reset: resetMaster,
  } = useMasterJob();

  async function handleAnalyze(f: File) {
    // Release any previous Blob URL before allocating a new one.
    if (originalUrl) URL.revokeObjectURL(originalUrl);
    setOriginalUrl(URL.createObjectURL(f));
    setFile(f);
    setAnalysis(null);
    setAnalyzeError(null);
    resetMaster(); // cancel any in-flight master poll
    setStatus("analyzing");
    try {
      const result = await analyze(f);
      setAnalysis(result);
      setStatus("analyzed");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Analysis failed.";
      setAnalyzeError(msg);
      setStatus("error");
    }
  }

  function handleMaster() {
    if (!file) return;
    setStatus("mastering");
    void startMaster(file);
  }

  // Promote the status to "mastered" once the hook has a final result.
  // We watch the hook's polling flag: it goes false when status === "ready" or "error".
  // Use a small effect-free derivation: any time `result` or `error` flips, sync.
  // (Kept inline for simplicity — if this grows, lift into useEffect.)
  const derivedStatus: Status =
    status === "mastering" && (masterResult || (masterError && !masterPolling))
      ? masterResult
        ? "mastered"
        : "analyzed"
      : status;

  function handleReset() {
    if (originalUrl) URL.revokeObjectURL(originalUrl);
    setOriginalUrl(null);
    setFile(null);
    setAnalysis(null);
    setAnalyzeError(null);
    setStatus("idle");
    resetMaster();
  }

  return (
    <div className="bg-stage min-h-full">
      <div className="mx-auto flex min-h-screen max-w-5xl flex-col px-6 py-10">
        <Header />

        <main className="flex-1">
          {status === "idle" || status === "error" ? (
            <div className="space-y-4">
              <UploadZone
                onFile={handleAnalyze}
                disabled={status === "error"}
                error={analyzeError}
              />
              <BassBoostZone />
              <AIMasterZone />
              <ReferenceMasterZone />
            </div>
          ) : (
            <div className="space-y-6">
              <div className="flex items-center justify-between rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-zinc-200">
                    {file?.name}
                  </div>
                  <div className="text-xs text-zinc-500">
                    {file ? `${(file.size / 1024 / 1024).toFixed(2)} MB` : ""}
                  </div>
                </div>
                <button
                  onClick={handleReset}
                  className="rounded-md px-3 py-1.5 text-xs font-medium text-zinc-400 transition hover:bg-ink-800 hover:text-zinc-200"
                >
                  Upload another
                </button>
              </div>

              {status === "analyzing" && <StatusBlock label="Analyzing audio…" />}
              {analysis && status !== "analyzing" && (
                <AnalysisDashboard analysis={analysis} />
              )}

              {analysis && (
                <MasterPanel
                  status={derivedStatus}
                  onMaster={handleMaster}
                  result={masterResult as MasterResult | null}
                  partialVariants={partialVariants}
                  totalPresets={masterResult?.variants.length || partialVariants.length || 11}
                  originalUrl={originalUrl}
                  error={masterError}
                />
              )}
            </div>
          )}
        </main>

        <Footer />
      </div>
    </div>
  );
}

function Header() {
  return (
    <header className="mb-10 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-accent-500 to-accent-600 shadow-lg shadow-accent-600/20">
          <svg viewBox="0 0 24 24" className="h-5 w-5 text-white" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12h2l2-6 3 12 2-8 2 4h7" />
          </svg>
        </div>
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-100">
            AI Audio Mastering
          </h1>
          <p className="text-xs text-zinc-500">
            Drop a WAV or MP3 — analyze, then master to −14 LUFS / −1 dBTP.
          </p>
        </div>
      </div>
      <a
        href="http://127.0.0.1:8000/docs"
        target="_blank"
        rel="noreferrer"
        className="text-xs font-medium text-zinc-500 transition hover:text-zinc-300"
      >
        API docs →
      </a>
    </header>
  );
}

function Footer() {
  return (
    <footer className="mt-12 flex items-center justify-between text-xs text-zinc-600">
      <span>Phase 1 MVP · pedalboard + pyloudnorm DSP</span>
      <span className="font-mono">backend: FastAPI · frontend: Vite + React</span>
    </footer>
  );
}

function StatusBlock({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-ink-700 bg-ink-900/60 px-4 py-6">
      <Spinner />
      <span className="text-sm text-zinc-300">{label}</span>
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
