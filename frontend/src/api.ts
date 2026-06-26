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
  const res = await fetch(`${API_BASE}/master/${jobId}/status`);
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

// Build a URL the browser can fetch directly to stream the mastered WAV
// for a single preset. The FastAPI handler at GET /download/{job_id}/{preset_id}
// serves the matching file from /tmp/audio_jobs/.
export function presetDownloadUrl(jobId: string, presetId: string): string {
  return `${API_BASE}/download/${jobId}/${presetId}`;
}
