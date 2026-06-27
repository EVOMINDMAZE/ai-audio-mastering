// Thin API client for the FastAPI backend.
//
// In dev, the Vite proxy forwards /api/* to http://127.0.0.1:8000/* (see vite.config.ts),
// so the browser sees a single origin and we don't need to configure CORS for dev.

import type { AnalysisResult, MasterResult } from "./types";

// Base path. With the Vite proxy this becomes http://localhost:5173/api -> :8000.
export const API_BASE = "/api";

async function postFile<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // fallthrough — use statusText
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return (await res.json()) as T;
}

export async function analyze(file: File): Promise<AnalysisResult> {
  return postFile<AnalysisResult>("/analyze", file);
}

// Master submission is now async — POST returns a job_id within ~1s and
// the actual render happens in a background thread. See useMasterJob.
export interface MasterSubmitResponse {
  job_id: string;
  status: "queued";
  status_url: string;
}

export async function master(file: File): Promise<MasterSubmitResponse> {
  return postFile<MasterSubmitResponse>("/master", file);
}

export interface MasterStatusResponse {
  job_id: string;
  status: "queued" | "processing" | "ready" | "error";
  error: string | null;
  variants: MasterResult["variants"];
}

export async function masterStatus(jobId: string): Promise<MasterStatusResponse> {
  // Poll with retry on transient 502/503/504 — Render free-tier workers are
  // killed and restarted on the 15-min sleep boundary, and the edge proxy
  // returns 502 to in-flight requests during that window. After a worker
  // restart the in-memory job registry is empty, so a retry that succeeds
  // HTTP-wise will then 404; the caller surfaces that case as a clearer
  // "render lost" message.
  let lastErr: Error | null = null;
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = await fetch(`${API_BASE}/master/${jobId}/status`);
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
      lastErr = new Error(`${res.status} ${res.statusText}`);
      continue;
    }
    if (res.status === 404) {
      throw new Error(
        "Render lost — Render restarted the worker mid-render (15-min " +
          "sleep kicked in). Upload the file again to retry."
      );
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail ?? detail;
      } catch {
        // fallthrough
      }
      throw new Error(`${res.status} ${detail}`);
    }
    return (await res.json()) as MasterStatusResponse;
  }
  throw lastErr ?? new Error("Status poll failed after retries.");
}

// Build a URL the browser can fetch directly to stream the mastered WAV
// for a single preset. The FastAPI handler at GET /download/{job_id}/{preset_id}
// serves the matching file from /tmp/audio_jobs/.
export function presetDownloadUrl(jobId: string, presetId: string): string {
  return `${API_BASE}/download/${jobId}/${presetId}`;
}

// ---- AI master (LLM-driven preset picking) ---------------------------------

export interface AiMasterInputFeatures {
  lufs: number | null;
  peak_dbtp: number | null;
  bpm: number | null;
  mud_flag: boolean | null;
  clipping_flag: boolean | null;
  duration_s: number | null;
}

export interface AiMasterStartResponse {
  job_id: string;
  status: "queued";
  status_url: string;
  source: "llm" | "fallback";
  preset_id: string;
  overrides: Record<string, number>;
  reasoning: string;
  input_features: AiMasterInputFeatures;
}

export interface AiMasterVariantMetrics {
  in_lufs: number | null;
  out_lufs: number | null;
  in_peak_dbtp: number | null;
  out_peak_dbtp: number | null;
  applied_gain_db: number | null;
  limiter_reduction_db: number | null;
}

export interface AiMasterVariant {
  preset_id: string;
  label: string;
  description: string;
  download_url: string;
  metrics: AiMasterVariantMetrics | null;
  source?: "llm" | "fallback";
  overrides?: Record<string, number>;
  reasoning?: string;
  input_features?: AiMasterInputFeatures;
}

export interface AiMasterStatusResponse {
  job_id: string;
  status: "queued" | "processing" | "ready" | "error";
  error: string | null;
  variants: AiMasterVariant[];
}

export async function aiMasterStart(file: File): Promise<AiMasterStartResponse> {
  return postFile<AiMasterStartResponse>("/ai-master", file);
}

export async function aiMasterStatus(jobId: string): Promise<AiMasterStatusResponse> {
  let lastErr: Error | null = null;
  for (let attempt = 0; attempt < 5; attempt++) {
    const res = await fetch(`${API_BASE}/ai-master/${jobId}/status`);
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
      lastErr = new Error(`${res.status} ${res.statusText}`);
      continue;
    }
    if (res.status === 404) {
      throw new Error("Job lost — worker restarted. Upload again to retry.");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail ?? detail;
      } catch {
        // fallthrough
      }
      throw new Error(`${res.status} ${detail}`);
    }
    return (await res.json()) as AiMasterStatusResponse;
  }
  throw lastErr ?? new Error("ai-master status poll failed after retries.");
}

export function aiMasterDownloadUrl(jobId: string): string {
  return `${API_BASE}/ai-master/${jobId}/download`;
}
