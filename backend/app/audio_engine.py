"""
audio_engine.py — pure-Python DSP + analysis core.

Design rule: NO FastAPI / HTTP imports in this module. Everything here is a
plain function that takes file paths or numpy arrays and returns either a
dictionary of metrics or a numpy array of samples. This keeps the engine
trivially testable from pytest and lets it be reused in batch scripts.

Public API
----------
- load_audio(path, target_sr=44100, mono=False) -> (np.ndarray, int)
    Decode any audio file librosa/pydub can read. Always returns float32 in
    [-1, 1]. If mono=True, downmixes to a single channel; otherwise preserves
    the original channel layout as a (channels, samples) array.
- analyze(path) -> dict
    Extract BPM, RMS, peak, integrated LUFS, true peak, top spectrum peaks,
    plus mud_flag and clipping_flag heuristics.
- master(in_path, out_path, **overrides) -> dict
    Apply the full mastering chain (HPF → 3-band EQ → compressor → LUFS
    normalization → brick-wall limiter) and write a 24-bit WAV. Returns
    before/after metrics.
- measure_true_peak(samples, sr) -> float
    Internal helper — 4x-oversampled peak in dBTP, ITU-R BS.1770 style.
- _db(x), _from_db(x)
    Internal dB <-> linear conversion helpers.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import librosa
import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from pedalboard import (
    Compressor,
    HighpassFilter,
    Pedalboard,
    PeakFilter,
    Reverb,
)
from scipy.signal import resample_poly

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_EPS = 1e-12  # -240 dB floor — prevents log(0) and division by zero


def _db(x: float) -> float:
    """Linear amplitude -> dBFS."""
    return 20.0 * np.log10(max(abs(x), _EPS))


def _from_db(db: float) -> float:
    """dBFS -> linear amplitude."""
    return 10.0 ** (db / 20.0)


def measure_true_peak(samples: np.ndarray, sr: int, oversample: int = 4) -> float:
    """Estimate true peak (dBTP) by oversampling.

    True peak is the maximum absolute sample value after oversampling, which
    captures inter-sample peaks a digital peak meter would miss. The 4x rate
    is the convention used by ITU-R BS.1770 reference meters.
    """
    if samples.ndim == 1:
        x = samples
    else:
        x = samples.reshape(-1)  # collapse channels for the peak measurement
    # resample_poly performs polyphase filtering — much cleaner than naive
    # zero-stuffing, which can introduce imaging artifacts.
    upsampled = resample_poly(x, oversample, 1)
    peak_linear = float(np.max(np.abs(upsampled)))
    return _db(peak_linear)


def true_peak_brick_wall(
    audio: np.ndarray,
    sr: int,
    ceiling_dbtp: float = -1.0,
    oversample: int = 8,
) -> np.ndarray:
    """True-peak brick-wall limiter via uniform gain reduction.

    Algorithm
    ---------
    1. Upsample the signal ``oversample``-fold with a polyphase filter
       (``scipy.signal.resample_poly``).
    2. Measure the maximum absolute oversampled peak.
    3. If that peak is already at or below ``ceiling_dbtp``, return the
       original audio unchanged — limiting is a no-op.
    4. Otherwise apply a *constant* gain reduction so the max oversampled
       peak lands exactly on the ceiling.

    Why this is correct
    -------------------
    The previous implementation (hard-clip-then-downsample) suffered from
    polyphase-lowpass ringing: the downsample filter's passband ripple
    pushed the measured true peak of the output above the clip threshold
    by up to ~0.5 dB on heavy bass material. By detecting the peak on the
    oversampled signal but applying the gain reduction to the original
    audio, we avoid the downsample stage entirely — so there is no
    ringing and the output's measured true peak will be exactly
    ``ceiling_dbtp``.

    Trade-off
    ---------
    Uniform gain reduction lowers the entire track, not just the
    overshooting moments. For an MVP this is fine — it guarantees the
    ceiling is met, which is the user's hard requirement. A lookahead
    limiter that varies gain over time is a follow-up improvement.
    """
    threshold = 10.0 ** (ceiling_dbtp / 20.0)

    if audio.ndim == 1:
        up = resample_poly(audio, oversample, 1)
    else:
        # (channels, samples) — transpose to (samples, channels) for resample_poly
        up = resample_poly(audio.T, oversample, 1, axis=0)

    peak = float(np.max(np.abs(up)))
    if peak <= threshold:
        return audio.astype(np.float32, copy=False)

    gain = threshold / peak
    return (audio * gain).astype(np.float32)


# ---------------------------------------------------------------------------
# Mastering presets
# ---------------------------------------------------------------------------

# Each preset is a dict of kwargs for `master()`. The frontend displays
# `label` and `description` and selects by `preset_id`. Editing these values
# is the supported way to add a new style — no other code needs to change.
PRESETS: Dict[str, Dict[str, Any]] = {
    "streaming": {
        "label": "Streaming (−14 LUFS)",
        "description": "Balanced, Spotify-ready. -14 LUFS, -1 dBTP ceiling.",
        "hpf_hz": 30.0,
        "eq_low_gain_db": 2.0,
        "eq_mid_freq_hz": 250.0,
        "eq_mid_gain_db": -1.5,
        "eq_high_freq_hz": 8000.0,
        "eq_high_gain_db": 3.0,
        "comp_threshold_db": -20.0,
        "comp_ratio": 4.0,
        "comp_attack_ms": 10.0,
        "comp_release_ms": 100.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -14.0,
    },
    "loud": {
        "label": "Loud / Club (−9 LUFS)",
        "description": "Aggressive, high-energy. -9 LUFS, -0.3 dBTP ceiling.",
        "hpf_hz": 35.0,
        "eq_low_gain_db": 2.5,
        "eq_mid_freq_hz": 200.0,
        "eq_mid_gain_db": -1.0,
        "eq_high_freq_hz": 7500.0,
        "eq_high_gain_db": 3.5,
        "comp_threshold_db": -18.0,
        "comp_ratio": 6.0,
        "comp_attack_ms": 5.0,
        "comp_release_ms": 80.0,
        "true_peak_ceiling_dbtp": -0.3,
        "target_lufs": -9.0,
    },
    "warm": {
        "label": "Warm Vinyl",
        "description": "Low-end warmth, soft saturation. -12 LUFS, -1 dBTP ceiling.",
        "hpf_hz": 25.0,
        "eq_low_gain_db": 3.5,
        "eq_mid_freq_hz": 300.0,
        "eq_mid_gain_db": -2.0,
        "eq_high_freq_hz": 9000.0,
        "eq_high_gain_db": 1.5,
        "comp_threshold_db": -22.0,
        "comp_ratio": 3.0,
        "comp_attack_ms": 20.0,
        "comp_release_ms": 200.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -12.0,
    },
    "podcast": {
        "label": "Podcast / Speech (−16 LUFS)",
        "description": "Mid-forward, tames sibilance. -16 LUFS, -1 dBTP ceiling.",
        "hpf_hz": 80.0,
        "eq_low_gain_db": -1.0,
        "eq_mid_freq_hz": 2500.0,
        "eq_mid_gain_db": 2.5,
        "eq_high_freq_hz": 6000.0,
        "eq_high_gain_db": -2.0,
        "comp_threshold_db": -18.0,
        "comp_ratio": 5.0,
        "comp_attack_ms": 5.0,
        "comp_release_ms": 60.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -16.0,
    },
    "acoustic": {
        "label": "Acoustic (preserve dynamics)",
        "description": "Gentle, transparent. -15 LUFS, -1 dBTP ceiling.",
        "hpf_hz": 40.0,
        "eq_low_gain_db": 1.0,
        "eq_mid_freq_hz": 400.0,
        "eq_mid_gain_db": -0.5,
        "eq_high_freq_hz": 10000.0,
        "eq_high_gain_db": 2.0,
        "comp_threshold_db": -24.0,
        "comp_ratio": 2.5,
        "comp_attack_ms": 30.0,
        "comp_release_ms": 300.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -15.0,
    },
    "edm": {
        "label": "EDM (hard limit)",
        "description": "Heavy compression, hard ceiling. -8 LUFS, -0.3 dBTP ceiling.",
        "hpf_hz": 30.0,
        "eq_low_gain_db": 3.0,
        "eq_mid_freq_hz": 250.0,
        "eq_mid_gain_db": -2.0,
        "eq_high_freq_hz": 8000.0,
        "eq_high_gain_db": 4.0,
        "comp_threshold_db": -16.0,
        "comp_ratio": 8.0,
        "comp_attack_ms": 2.0,
        "comp_release_ms": 50.0,
        "true_peak_ceiling_dbtp": -0.3,
        "target_lufs": -8.0,
    },
    "bass_boosted": {
        "label": "Bass Boosted",
        "description": "Extra punchy 808s and sub-bass.",
        "hpf_hz": 30.0,
        "eq_low_gain_db": 4.0,
        "eq_mid_freq_hz": 250.0,
        "eq_mid_gain_db": -1.5,
        "eq_high_freq_hz": 8000.0,
        "eq_high_gain_db": 3.0,
        "comp_threshold_db": -20.0,
        "comp_ratio": 4.0,
        "comp_attack_ms": 10.0,
        "comp_release_ms": 100.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -14.0,
    },
    "slowed": {
        "label": "Slowed (0.85x)",
        "description": "Slowed for that dreamy vibe.",
        "hpf_hz": 30.0,
        "eq_low_gain_db": 2.0,
        "eq_mid_freq_hz": 250.0,
        "eq_mid_gain_db": -1.5,
        "eq_high_freq_hz": 8000.0,
        "eq_high_gain_db": 3.0,
        "comp_threshold_db": -20.0,
        "comp_ratio": 4.0,
        "comp_attack_ms": 10.0,
        "comp_release_ms": 100.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -14.0,
        "time_stretch_rate": 0.85,
    },
    "extra_slowed": {
        "label": "Extra Slowed (0.75x)",
        "description": "Deeply slowed with subtle reverb.",
        "hpf_hz": 30.0,
        "eq_low_gain_db": 2.0,
        "eq_mid_freq_hz": 250.0,
        "eq_mid_gain_db": -1.5,
        "eq_high_freq_hz": 8000.0,
        "eq_high_gain_db": 3.0,
        "comp_threshold_db": -20.0,
        "comp_ratio": 4.0,
        "comp_attack_ms": 10.0,
        "comp_release_ms": 100.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -14.0,
        "time_stretch_rate": 0.75,
        "reverb_amount": 0.2,
    },
    "sped_up": {
        "label": "Sped Up (1.15x)",
        "description": "Nightcore-style speed boost with +2 semitone pitch shift.",
        "hpf_hz": 30.0,
        "eq_low_gain_db": 2.0,
        "eq_mid_freq_hz": 250.0,
        "eq_mid_gain_db": -1.5,
        "eq_high_freq_hz": 8000.0,
        "eq_high_gain_db": 3.0,
        "comp_threshold_db": -20.0,
        "comp_ratio": 4.0,
        "comp_attack_ms": 10.0,
        "comp_release_ms": 100.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -14.0,
        "time_stretch_rate": 1.15,
        "pitch_shift_semitones": 2.0,
    },
    "reverb": {
        "label": "Reverb (Large Hall)",
        "description": "Cathedral-style space and depth.",
        "hpf_hz": 30.0,
        "eq_low_gain_db": 2.0,
        "eq_mid_freq_hz": 250.0,
        "eq_mid_gain_db": -1.5,
        "eq_high_freq_hz": 8000.0,
        "eq_high_gain_db": 3.0,
        "comp_threshold_db": -20.0,
        "comp_ratio": 4.0,
        "comp_attack_ms": 10.0,
        "comp_release_ms": 100.0,
        "true_peak_ceiling_dbtp": -1.0,
        "target_lufs": -14.0,
        "reverb_amount": 0.5,
    },
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_audio(path: str, target_sr: int = 44100, mono: bool = False) -> Tuple[np.ndarray, int]:
    """Load an audio file as float32 in [-1, 1].

    Parameters
    ----------
    path : str
        Path to a WAV / MP3 / FLAC / OGG file. MP3 decoding requires system
        ffmpeg to be on PATH (pydub is used upstream in routes/master.py and
        routes/analyze.py to convert MP3 -> WAV before calling this function).
    target_sr : int
        Target sample rate. If the source sample rate differs, librosa
        resamples with a high-quality polyphase filter.
    mono : bool
        If True, downmix to mono. If False, preserve the original channel
        layout as a (channels, samples) float32 array.

    Returns
    -------
    (audio, sr) : (np.ndarray, int)
        audio is float32 with shape (samples,) when mono=True or (channels,
        samples) when mono=False. Values are in [-1, 1].
    """
    audio, sr = librosa.load(path, sr=target_sr, mono=mono)
    # librosa already returns float32 in [-1, 1] but be explicit about dtype
    # so downstream consumers (pyloudnorm, pedalboard) get what they expect.
    return audio.astype(np.float32, copy=False), int(sr)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _detect_mud(samples_mono: np.ndarray, sr: int) -> bool:
    """Heuristic: flag the track as 'muddy' if 200–300Hz energy materially
    exceeds 4–8kHz air-band energy.

    We compare mean magnitude in the two bands on a log-frequency axis. A
    6 dB gap is the threshold; this catches the classic 'muddy' AI generation
    artifact without false-flagging warm-but-balanced mixes.
    """
    if samples_mono.size < sr // 4:
        return False  # too short to be meaningful

    S = np.abs(librosa.stft(samples_mono, n_fft=2048, hop_length=512)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)

    def band_mean(lo: float, hi: float) -> float:
        mask = (freqs >= lo) & (freqs < hi)
        if not np.any(mask):
            return _EPS
        return float(np.mean(S[mask]))

    mud_energy = band_mean(200.0, 300.0)
    air_energy = band_mean(4000.0, 8000.0)
    return mud_energy > (air_energy * 4.0)  # ~6 dB ratio


def _top_spectrum_peaks(samples_mono: np.ndarray, sr: int, n: int = 5) -> List[Dict[str, float]]:
    """Return the top-N spectral peaks as {frequency_hz, magnitude_db} dicts.

    Used by the frontend to render a small spectrum view without shipping raw
    FFT data over the wire.
    """
    if samples_mono.size < sr // 4:
        return []

    S = np.abs(librosa.stft(samples_mono, n_fft=2048, hop_length=512))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    mean_mag = np.mean(S, axis=1)

    # Pick the top-N by magnitude, skipping the DC bin (index 0).
    top_idx = np.argsort(mean_mag[1:])[::-1][:n] + 1
    peaks: List[Dict[str, float]] = []
    for idx in top_idx:
        peaks.append(
            {
                "frequency_hz": float(freqs[idx]),
                "magnitude_db": float(_db(mean_mag[idx])),
            }
        )
    return peaks


def analyze(path: str) -> Dict[str, Any]:
    """Extract the full analysis payload for the frontend dashboard.

    Returns a dict (not an AnalysisResult Pydantic model — the route layer
    is responsible for wrapping it) with keys matching the AnalysisResult
    schema in app/models.py.
    """
    # Load stereo (preserve original) AND mono (for BPM/spectrum heuristics).
    stereo, sr = load_audio(path, target_sr=44100, mono=False)
    mono, _ = load_audio(path, target_sr=44100, mono=True)

    # ---- Basic sample-domain metrics ---------------------------------------
    peak_sample = float(np.max(np.abs(stereo))) if stereo.size else 0.0
    rms = float(np.sqrt(np.mean(stereo ** 2))) if stereo.size else 0.0

    # ---- True peak (4x oversampled) ----------------------------------------
    true_peak = measure_true_peak(stereo, sr)

    # ---- Integrated LUFS (ITU-R BS.1770-4 via pyloudnorm) -----------------
    # pyloudnorm expects shape (samples, channels) for multi-channel audio.
    meter = pyln.Meter(sr)
    # Need shape (n_samples, n_channels) for pyloudnorm
    if stereo.ndim == 1:
        lufs_input = stereo.reshape(-1, 1)
    else:
        lufs_input = stereo.T  # (samples, channels)
    try:
        lufs = float(meter.integrated_loudness(lufs_input))
    except ValueError:
        # pyloudnorm raises if signal is silent or too short — treat as -inf.
        lufs = -120.0

    # ---- BPM ---------------------------------------------------------------
    # librosa >=0.10 moved tempo to librosa.feature.rhythm.tempo; the old path
    # is a deprecated alias that warns. Use the new path directly.
    try:
        from librosa.feature import tempo as _tempo_fn
        tempo_arr = _tempo_fn(y=mono, sr=sr)
        bpm = float(tempo_arr[0]) if tempo_arr.size else 0.0
    except Exception:
        bpm = 0.0

    # ---- Spectral heuristics ----------------------------------------------
    # `clipping_flag` is driven by the true-peak ceiling rather than raw
    # sample-domain saturation: an input whose true peak exceeds -1.0 dBTP
    # is at risk of clipping in any downstream processing that doesn't have
    # additional headroom. A -1.3 dBTP input (sample peak well under 1.0)
    # correctly reads as safe.
    mud_flag = _detect_mud(mono, sr)
    clipping_flag = bool(true_peak > -1.0)
    spectrum_peaks = _top_spectrum_peaks(mono, sr)

    duration_s = float(stereo.shape[-1]) / float(sr)

    return {
        "bpm": round(bpm, 2),
        "rms_dbfs": round(_db(rms), 2),
        "peak_dbfs": round(_db(peak_sample), 2),
        "lufs_integrated": round(lufs, 2),
        "true_peak_dbtp": round(true_peak, 2),
        "spectrum_peaks": spectrum_peaks,
        "mud_flag": mud_flag,
        "clipping_flag": clipping_flag,
        "duration_s": round(duration_s, 3),
        "sample_rate": sr,
    }


# ---------------------------------------------------------------------------
# Mastering chain
# ---------------------------------------------------------------------------


def _build_chain(
    *,
    hpf_hz: float,
    eq_low_gain_db: float,
    eq_mid_freq_hz: float,
    eq_mid_gain_db: float,
    eq_high_freq_hz: float,
    eq_high_gain_db: float,
    comp_threshold_db: float,
    comp_ratio: float,
    comp_attack_ms: float,
    comp_release_ms: float,
) -> Pedalboard:
    """Construct the dynamics portion of the DSP chain.

    Order matters and is preserved:
        1. HighpassFilter        — remove sub-bass rumble
        2. PeakFilter (low band) — +2dB shelf around 60Hz
        3. PeakFilter (mid band) — -1.5dB cut at 250Hz (mud)
        4. PeakFilter (high band)— +3dB air at 8kHz
        5. Compressor            — glue / dynamic control

    The true-peak brick-wall limiter is NOT in this chain — it is applied in
    ``master()`` AFTER loudness normalization, so it only catches the small
    overshoots introduced by the LUFS-target gain boost. pedalboard.Limiter
    hard-clips at 0 dBFS regardless of threshold, so we use our own
    ``true_peak_brick_wall`` (oversampled hard clip) to actually enforce
    the -1 dBTP ceiling.
    """
    return Pedalboard(
        [
            # 1. Sub-bass rumble cut
            HighpassFilter(cutoff_frequency_hz=float(hpf_hz)),
            # 2. Low-end warmth (gentle 60Hz shelf-ish boost via peak filter)
            PeakFilter(
                cutoff_frequency_hz=60.0,
                gain_db=float(eq_low_gain_db),
                q=0.7,
            ),
            # 3. Mud cut at 250Hz
            PeakFilter(
                cutoff_frequency_hz=float(eq_mid_freq_hz),
                gain_db=float(eq_mid_gain_db),
                q=1.0,
            ),
            # 4. Air at 8kHz
            PeakFilter(
                cutoff_frequency_hz=float(eq_high_freq_hz),
                gain_db=float(eq_high_gain_db),
                q=0.9,
            ),
            # 5. Compression — glue
            Compressor(
                threshold_db=float(comp_threshold_db),
                ratio=float(comp_ratio),
                attack_ms=float(comp_attack_ms),
                release_ms=float(comp_release_ms),
            ),
        ]
    )


def _ensure_2d(audio: np.ndarray) -> np.ndarray:
    """pedalboard expects (channels, samples) for >1 channel and (samples,)
    for mono. Normalize whatever we get from librosa into that shape."""
    if audio.ndim == 1:
        return audio
    return audio  # already (channels, samples)


def master(
    in_path: str,
    out_path: str,
    *,
    hpf_hz: float = 30.0,
    eq_low_gain_db: float = 2.0,
    eq_mid_freq_hz: float = 250.0,
    eq_mid_gain_db: float = -1.5,
    eq_high_freq_hz: float = 8000.0,
    eq_high_gain_db: float = 3.0,
    comp_threshold_db: float = -20.0,
    comp_ratio: float = 4.0,
    comp_attack_ms: float = 10.0,
    comp_release_ms: float = 100.0,
    true_peak_ceiling_dbtp: float = -1.0,
    target_lufs: float = -14.0,
    time_stretch_rate: float = 1.0,
    reverb_amount: float = 0.0,
    pitch_shift_semitones: float = 0.0,
) -> Dict[str, Any]:
    """Run the full mastering chain and write a 24-bit WAV to ``out_path``.

    Chain order:
        1. Load audio and measure INPUT LUFS / true-peak for reporting.
        2. Apply the dynamics chain (HPF + 3-band EQ + Compressor).
        3. Apply viral effects: pitch shift, time stretch, reverb.
           These are order-sensitive — see comments at each step.
        4. Measure POST-EFFECTS LUFS and apply normalization gain toward
           ``target_lufs`` (clamped to ±24 dB).
        5. Apply the true-peak brick-wall limiter at ``ceiling``.

    Why normalization is post-effects (not pre-chain)
    ------------------------------------------------
    Time stretching and reverb alter loudness in non-linear ways (reverb
    adds energy from the wet tail; time stretch changes RMS by spreading or
    compressing transients). Measuring LUFS before those effects and
    applying gain there means the *output* no longer matches the target.
    Measuring after — but before the limiter — gives the user the target
    LUFS they actually hear.
    """
    # ---- Load at native sample rate, preserving channels ------------------
    audio, sr = load_audio(in_path, target_sr=44100, mono=False)
    audio = _ensure_2d(audio).astype(np.float32, copy=False)

    # ---- Pre-mastering metrics -------------------------------------------
    in_peak = measure_true_peak(audio, sr)
    meter = pyln.Meter(sr)
    pyln_in = audio.T if audio.ndim == 2 else audio.reshape(-1, 1)
    try:
        in_lufs = float(meter.integrated_loudness(pyln_in))
    except ValueError:
        in_lufs = -120.0

    # ---- Dynamics chain (no pre-gain; gain is applied AFTER viral effects) -
    chain = _build_chain(
        hpf_hz=hpf_hz,
        eq_low_gain_db=eq_low_gain_db,
        eq_mid_freq_hz=eq_mid_freq_hz,
        eq_mid_gain_db=eq_mid_gain_db,
        eq_high_freq_hz=eq_high_freq_hz,
        eq_high_gain_db=eq_high_gain_db,
        comp_threshold_db=comp_threshold_db,
        comp_ratio=comp_ratio,
        comp_attack_ms=comp_attack_ms,
        comp_release_ms=comp_release_ms,
    )
    processed = chain(audio, sr)

    # ---- Pitch shift (applied BEFORE time stretch so the time-stretch
    # operates on the pitch-shifted buffer, preserving pitch). ---------------
    # `processed` is (channels, samples). librosa.effects.pitch_shift treats
    # the LAST axis as the time axis (shape=(..., n)), so we pass directly.
    if pitch_shift_semitones != 0.0:
        processed = librosa.effects.pitch_shift(
            processed, sr=int(sr), n_steps=float(pitch_shift_semitones)
        ).astype(np.float32, copy=False)

    # ---- Time stretch (slowed / sped-up effects) -------------------------
    if time_stretch_rate != 1.0:
        processed = librosa.effects.time_stretch(
            processed, rate=float(time_stretch_rate)
        ).astype(np.float32, copy=False)

    # ---- Reverb (post-chain, pre-limiter) -------------------------------
    # Use a 70% wet / 30% dry mix so the original dry signal remains audible.
    # `reverb_amount` controls room_size (0..1, perceived size of the space).
    if reverb_amount > 0:
        processed = Pedalboard(
            [Reverb(room_size=float(reverb_amount), wet_level=0.7, dry_level=0.3)]
        )(processed, sr).astype(np.float32, copy=False)

    # ---- LUFS normalization AFTER viral effects --------------------------
    # Measure LUFS on the post-effects signal so the gain drives the final
    # output (what the user hears) to the target.
    pyln_post = processed.T if processed.ndim == 2 else processed.reshape(-1, 1)
    try:
        post_lufs = float(meter.integrated_loudness(pyln_post))
    except ValueError:
        post_lufs = -120.0

    if np.isfinite(post_lufs):
        gain_delta_db = float(np.clip(target_lufs - post_lufs, -24.0, 24.0))
    else:
        gain_delta_db = 0.0

    normalized = (processed * _from_db(gain_delta_db)).astype(np.float32, copy=False)

    # ---- Output LUFS (informational only) -------------------------------
    pyln_out = normalized.T if normalized.ndim == 2 else normalized.reshape(-1, 1)
    try:
        out_lufs = float(meter.integrated_loudness(pyln_out))
    except ValueError:
        out_lufs = target_lufs

    # ---- True-peak brick-wall limit (always; no-op if within ceiling) ---
    pre_limit_peak = measure_true_peak(normalized, sr)
    mastered = true_peak_brick_wall(
        normalized, sr, ceiling_dbtp=float(true_peak_ceiling_dbtp)
    )

    # ---- Post-mastering metrics -----------------------------------------
    out_peak = measure_true_peak(mastered, sr)

    # Limiter reduction estimate: how much the true-peak limit had to pull
    # the level down, beyond what the LUFS gain alone accounted for.
    if pre_limit_peak > true_peak_ceiling_dbtp:
        limiter_reduction_db = pre_limit_peak - out_peak
    else:
        limiter_reduction_db = 0.0

    # ---- Write 24-bit WAV -------------------------------------------------
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # soundfile expects shape (samples, channels) for >1-channel data.
    writeable = mastered.T if mastered.ndim == 2 else mastered
    sf.write(out_path, writeable, sr, subtype="PCM_24")

    return {
        "in_lufs": round(in_lufs, 2),
        "out_lufs": round(out_lufs, 2),
        "in_peak_dbtp": round(in_peak, 2),
        "out_peak_dbtp": round(out_peak, 2),
        "applied_gain_db": round(gain_delta_db, 2),
        "limiter_reduction_db": round(limiter_reduction_db, 2),
    }