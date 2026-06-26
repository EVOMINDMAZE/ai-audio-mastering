// Type contract that mirrors backend/app/models.py.
// Keep these in sync with the FastAPI Pydantic response models.

export interface SpectrumPeak {
  frequency_hz: number;
  magnitude_db: number;
}

export interface AnalysisResult {
  job_id: string;
  bpm: number;
  rms_dbfs: number;
  peak_dbfs: number;
  lufs_integrated: number;
  true_peak_dbtp: number;
  spectrum_peaks: SpectrumPeak[];
  mud_flag: boolean;
  clipping_flag: boolean;
  duration_s: number;
  sample_rate: number;
}

export interface MasterMetrics {
  in_lufs: number;
  out_lufs: number;
  in_peak_dbtp: number;
  out_peak_dbtp: number;
  applied_gain_db: number;
}

export interface MasterVariant {
  preset_id: string;
  label: string;
  description: string;
  download_url: string;
  metrics: MasterMetrics;
}

export interface MasterResult {
  job_id: string;
  variants: MasterVariant[];
}
