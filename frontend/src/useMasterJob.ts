import { useRef, useState } from "react";
import { master as masterApi, masterStatus } from "./api";
import type { MasterResult, MasterVariant } from "./types";

/**
 * Async master-job hook.
 *
 * The backend POST /master endpoint returns a job_id within ~1s, then the
 * actual render happens in a background thread. This hook:
 *   1. POSTs the file and captures the job_id.
 *   2. Polls /master/{job_id}/status every 1.5s with backoff (capped at 3s).
 *   3. Updates `partialVariants` as each preset finishes, so the UI can show
 *      per-preset progress.
 *   4. Resolves to a full `MasterResult` when status === "ready" or "error".
 *
 * `reset()` aborts any in-flight poll — call when the user uploads a new file.
 */
export interface UseMasterJob {
  result: MasterResult | null;
  partialVariants: MasterVariant[];
  jobId: string | null;
  error: string | null;
  polling: boolean;
  start: (file: File) => Promise<void>;
  reset: () => void;
}

const INITIAL_POLL_MS = 1500;
const MAX_POLL_MS = 3000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000; // give up after 5 min

export function useMasterJob(): UseMasterJob {
  const [result, setResult] = useState<MasterResult | null>(null);
  const [partialVariants, setPartialVariants] = useState<MasterVariant[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const pollRef = useRef<{ aborted: boolean }>({ aborted: false });

  async function start(file: File) {
    setError(null);
    setResult(null);
    setPartialVariants([]);
    pollRef.current = { aborted: false };
    setPolling(true);

    try {
      // 1) Submit the job. Response is { job_id, status, status_url }.
      const submit = await masterApi(file);
      setJobId(submit.job_id);

      // 2) Poll until ready/error.
      let delay = INITIAL_POLL_MS;
      const deadline = Date.now() + POLL_TIMEOUT_MS;
      while (!pollRef.current.aborted) {
        if (Date.now() > deadline) {
          throw new Error("Mastering timed out after 5 minutes.");
        }
        const state = await masterStatus(submit.job_id);

        // Update partial state so the UI can show progress per preset.
        if (state.variants) {
          setPartialVariants(state.variants);
        }

        if (state.status === "ready") {
          setResult({ job_id: submit.job_id, variants: state.variants });
          setPolling(false);
          return;
        }
        if (state.status === "error") {
          throw new Error(state.error ?? "Mastering failed.");
        }

        await new Promise((res) => setTimeout(res, delay));
        delay = Math.min(delay + 500, MAX_POLL_MS);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Mastering failed.");
      setPolling(false);
    }
  }

  function reset() {
    pollRef.current.aborted = true;
    setResult(null);
    setPartialVariants([]);
    setJobId(null);
    setError(null);
    setPolling(false);
  }

  return { result, partialVariants, jobId, error, polling, start, reset };
}
