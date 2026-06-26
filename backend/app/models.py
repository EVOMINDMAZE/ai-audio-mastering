"""
Pydantic v2 response models for the API.

These models are the public contract between the FastAPI backend and the
frontend (or any other consumer). Keep field names stable; adding optional
fields is backwards-compatible, renaming or removing fields is not.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SpectrumPeak(BaseModel):
    """One prominent peak in the magnitude spectrum."""

    frequency_hz: float = Field(..., description="Approximate center frequency in Hz.")
    magnitude_db: float = Field(..., description="Magnitude at that frequency in dBFS.")


class AnalysisResult(BaseModel):
    """Metadata extracted from a single audio file."""

    job_id: str
    bpm: float = Field(..., description="Estimated tempo in beats per minute.")
    rms_dbfs: float = Field(..., description="Root-mean-square level in dBFS.")
    peak_dbfs: float = Field(..., description="Sample peak in dBFS (no oversampling).")
    lufs_integrated: float = Field(
        ..., description="Integrated loudness per ITU-R BS.1770-4 (LUFS)."
    )
    true_peak_dbtp: float = Field(
        ..., description="4x-oversampled true peak in dBTP."
    )
    spectrum_peaks: list[SpectrumPeak] = Field(
        default_factory=list,
        description="Top peaks in the magnitude spectrum (Hz, dB).",
    )
    mud_flag: bool = Field(
        ..., description="True when 200–300Hz energy materially exceeds 4–8kHz energy."
    )
    clipping_flag: bool = Field(
        ...,
        description=(
            "True when the input's true peak exceeds -1.0 dBTP "
            "(i.e. the file is at risk of clipping in downstream "
            "processing that has no additional headroom)."
        ),
    )
    duration_s: float
    sample_rate: int


class MasterMetrics(BaseModel):
    """Before/after metrics for a single mastering pass."""

    in_lufs: float
    out_lufs: float
    in_peak_dbtp: float
    out_peak_dbtp: float
    applied_gain_db: float = Field(
        ..., description="Loudness-normalization gain applied before the limiter."
    )


class MasterVariant(BaseModel):
    """One rendered preset for a mastered job.

    A single job produces one variant per preset defined in
    ``audio_engine.PRESETS`` so the UI can A/B them without re-mastering.
    """

    preset_id: str = Field(..., description="Identifier of the preset (e.g. 'streaming').")
    label: str = Field(..., description="Human-readable preset name.")
    description: str = Field(..., description="Short description of the preset's character.")
    download_url: str = Field(..., description="Per-preset API path to fetch the WAV.")
    metrics: MasterMetrics


class MasterResult(BaseModel):
    """Result of a mastering job — one variant per preset."""

    job_id: str
    variants: list[MasterVariant] = Field(
        default_factory=list,
        description="All rendered presets for this job (one per audio_engine.PRESETS entry).",
    )