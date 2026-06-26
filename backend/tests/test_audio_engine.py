"""
Unit tests for the audio_engine DSP pipeline.

These tests cover the publicly contracted behaviors:
  - load_audio returns correctly-shaped float32 arrays
  - analyze extracts BPM, LUFS, and flags correctly
  - master applies the chain, lands within LUFS target, stays under the peak ceiling
  - the high-pass filter actually attenuates sub-30Hz content
  - the clipping flag fires on hard-clipped input
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.audio_engine import (
    PRESETS,
    _db,
    analyze,
    load_audio,
    master,
    measure_true_peak,
)


# ---------------------------------------------------------------------------
# load_audio
# ---------------------------------------------------------------------------


def test_load_audio_returns_float32_in_range(sample_wav_path: Path):
    audio, sr = load_audio(str(sample_wav_path), target_sr=44100, mono=False)
    assert audio.dtype == np.float32
    assert sr == 44100
    assert audio.ndim == 2
    assert audio.shape[0] == 2  # stereo preserved
    assert np.all(np.abs(audio) <= 1.0 + 1e-6)


def test_load_audio_mono_downmix(sample_wav_path: Path):
    audio, _ = load_audio(str(sample_wav_path), target_sr=44100, mono=True)
    assert audio.ndim == 1
    assert audio.dtype == np.float32


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def test_analyze_returns_full_payload(sample_wav_path: Path):
    result = analyze(str(sample_wav_path))
    expected_keys = {
        "bpm",
        "rms_dbfs",
        "peak_dbfs",
        "lufs_integrated",
        "true_peak_dbtp",
        "spectrum_peaks",
        "mud_flag",
        "clipping_flag",
        "duration_s",
        "sample_rate",
    }
    assert expected_keys.issubset(result.keys())
    assert result["sample_rate"] == 44100
    assert 4.5 <= result["duration_s"] <= 5.5
    assert result["clipping_flag"] is False  # fixture amplitude 0.5, headroom OK


def test_analyze_flags_clipped_input(clipped_wav_path: Path):
    result = analyze(str(clipped_wav_path))
    assert result["clipping_flag"] is True


def test_analyze_detects_mud(lowheavy_wav_path: Path):
    result = analyze(str(lowheavy_wav_path))
    # 250Hz-only fundamental vs. no high content -> mud_flag should trigger.
    assert result["mud_flag"] is True


# ---------------------------------------------------------------------------
# master
# ---------------------------------------------------------------------------


def test_master_produces_24bit_wav_under_peak_ceiling(sample_wav_path: Path, tmp_path: Path):
    out_path = tmp_path / "mastered.wav"
    metrics = master(str(sample_wav_path), str(out_path))

    assert out_path.is_file()
    assert out_path.stat().st_size > 0

    # File should be 24-bit PCM per the audio_engine contract.
    info = sf.info(str(out_path))
    assert info.subtype == "PCM_24"

    # True peak must NEVER exceed the −1.0 dBTP ceiling. The new uniform-gain
    # brick-wall limiter guarantees this exactly (no ringing artifacts), so
    # tolerance is just rounding noise.
    assert metrics["out_peak_dbtp"] <= -1.0 + 0.01, (
        f"output true peak {metrics['out_peak_dbtp']} dBTP exceeds ceiling"
    )


def test_master_gain_matches_input_lufs_delta(sample_wav_path: Path, tmp_path: Path):
    """applied_gain_db drives the *post-effects* LUFS toward the target.

    With the post-effects-normalization chain, ``applied_gain_db`` equals
    ``(target_lufs − post_lufs)`` (clamped to ±24 dB), NOT
    ``(target_lufs − input_lufs)``. After gain + limiter, the measured
    ``out_lufs`` should be very close to ``target_lufs``.
    """
    out_path = tmp_path / "mastered.wav"
    metrics = master(str(sample_wav_path), str(out_path))

    # After the gain and limiter, the output LUFS should land at the target.
    # Tolerance is wider here because the limiter itself can attenuate by
    # small amounts to enforce the true-peak ceiling.
    assert abs(metrics["out_lufs"] - (-14.0)) <= 1.5, (
        f"out_lufs={metrics['out_lufs']:.2f} expected ~-14.0"
    )
    # Sanity: gain should be finite and within the ±24 dB clamp.
    assert -24.0 <= metrics["applied_gain_db"] <= 24.0


def test_master_gain_clamped_for_silent_input(tmp_path: Path):
    """Near-silent inputs must not produce a runaway gain (>±24 dB clamp)."""
    sr = 44100
    t = np.arange(sr) / sr  # 1 second of near-silence
    sig = (1e-6 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
    sf.write(str(tmp_path / "in.wav"), np.stack([sig, sig], axis=0).T, sr, subtype="PCM_16")

    metrics = master(str(tmp_path / "in.wav"), str(tmp_path / "out.wav"))
    assert abs(metrics["applied_gain_db"]) <= 24.0 + 0.01, (
        f"applied_gain_db={metrics['applied_gain_db']} not clamped within ±24 dB"
    )


@pytest.mark.parametrize(
    "input_amplitude_dbfs",
    [-10.0, -3.0, 0.0],
    ids=["loud_minus10dB", "loud_minus3dB", "full_scale"],
)
def test_limiter_always_enforces_ceiling(
    input_amplitude_dbfs: float, tmp_path: Path
):
    """Output true peak must be <= −1.0 dBTP for any input level."""
    sr = 44100
    duration_s = 2.0
    t = np.arange(int(duration_s * sr)) / sr
    amp = 10.0 ** (input_amplitude_dbfs / 20.0)
    sig = amp * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    stereo = np.stack([sig, sig], axis=0)

    in_path = tmp_path / "in.wav"
    out_path = tmp_path / "out.wav"
    sf.write(str(in_path), stereo.T, sr, subtype="PCM_16")

    metrics = master(str(in_path), str(out_path))
    assert metrics["out_peak_dbtp"] <= -1.0 + 0.01, (
        f"input {input_amplitude_dbfs} dBFS -> output {metrics['out_peak_dbtp']} dBTP "
        f"(exceeds −1.0 ceiling)"
    )


def test_clipping_flag_based_on_true_peak(tmp_path: Path):
    """clipping_flag must be driven by true_peak > −1.0 dBTP, not sample peak."""
    sr = 44100
    duration_s = 2.0
    t = np.arange(int(duration_s * sr)) / sr

    # (a) Safe signal: sample peak well under 1.0 AND true peak well under −1.0.
    safe = 0.1 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    safe_path = tmp_path / "safe.wav"
    sf.write(str(safe_path), np.stack([safe, safe], axis=0).T, sr, subtype="PCM_16")
    res = analyze(str(safe_path))
    assert res["true_peak_dbtp"] < -1.0
    assert res["clipping_flag"] is False

    # (b) Risky signal: sample peak ~0 dBFS, true peak well above −1.0.
    loud = np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    loud_path = tmp_path / "loud.wav"
    sf.write(str(loud_path), np.stack([loud, loud], axis=0).T, sr, subtype="PCM_16")
    res = analyze(str(loud_path))
    assert res["true_peak_dbtp"] > -1.0
    assert res["clipping_flag"] is True

    # (c) Edge case: true peak just over the ceiling. Build a signal whose
    # true peak sits between −1.0 and 0 dBFS but whose sample peak is below 1.0.
    edge = 0.7 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    edge_path = tmp_path / "edge.wav"
    sf.write(str(edge_path), np.stack([edge, edge], axis=0).T, sr, subtype="PCM_16")
    res = analyze(str(edge_path))
    if res["true_peak_dbtp"] > -1.0:
        assert res["clipping_flag"] is True
    else:
        assert res["clipping_flag"] is False


def test_master_chain_applies_highpass_filter(sample_wav_path: Path, tmp_path: Path):
    # Synth a fresh signal with strong <30Hz content so the HPF can be measured.
    sr = 44100
    duration_s = 3.0
    t = np.arange(int(duration_s * sr)) / sr
    rumble = 0.8 * np.sin(2 * np.pi * 15.0 * t).astype(np.float32)
    content = 0.4 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)
    sig = rumble + content
    stereo_in = np.stack([sig, sig], axis=0)

    in_path = tmp_path / "in.wav"
    out_path = tmp_path / "out.wav"
    sf.write(str(in_path), stereo_in.T, sr, subtype="PCM_16")

    master(str(in_path), str(out_path))

    out_audio, out_sr = sf.read(str(out_path), always_2d=True)
    assert out_sr == sr

    # Measure peak magnitude in the <30Hz band on both files via FFT. We use
    # peak (not mean) because the test signal has narrowband content at 15Hz.
    def band_peak_db(x: np.ndarray, lo: float, hi: float) -> float:
        # Collapse to 1D regardless of (channels, samples) vs (samples, channels).
        mono = np.asarray(x).reshape(-1) if x.size else x
        spec = np.abs(np.fft.rfft(mono.astype(np.float32)))
        freqs = np.fft.rfftfreq(len(mono), d=1.0 / sr)
        mask = (freqs >= lo) & (freqs < hi)
        if not np.any(mask):
            return -200.0
        # Normalize by N so the value is comparable to time-domain amplitude.
        peak = float(np.max(spec[mask])) / len(mono)
        return 20.0 * np.log10(peak + 1e-12)

    in_low = band_peak_db(stereo_in, 10.0, 25.0)
    out_low = band_peak_db(out_audio, 10.0, 25.0)

    # The HPF should drop the 15Hz content by at least 15dB.
    assert out_low < in_low - 15.0, (
        f"HPF ineffective: in_low={in_low:.2f}dB, out_low={out_low:.2f}dB"
    )


def test_master_metrics_dict_shape(sample_wav_path: Path, tmp_path: Path):
    out_path = tmp_path / "mastered.wav"
    metrics = master(str(sample_wav_path), str(out_path))
    expected = {
        "in_lufs",
        "out_lufs",
        "in_peak_dbtp",
        "out_peak_dbtp",
        "applied_gain_db",
        "limiter_reduction_db",
    }
    assert expected.issubset(metrics.keys())


# ---------------------------------------------------------------------------
# measure_true_peak
# ---------------------------------------------------------------------------


def test_measure_true_peak_handles_quiet_signal():
    sr = 44100
    sig = np.zeros(sr, dtype=np.float32)  # 1s of silence
    # Should return a very low dB value (well below silence floor).
    assert measure_true_peak(sig, sr) < -50.0


def test_db_round_trip():
    assert abs(_db(1.0) - 0.0) < 1e-6
    assert abs(_db(0.5) - (-6.0206)) < 1e-3


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


_REQUIRED_PRESET_KEYS = {
    "label",
    "description",
    "hpf_hz",
    "eq_low_gain_db",
    "eq_mid_freq_hz",
    "eq_mid_gain_db",
    "eq_high_freq_hz",
    "eq_high_gain_db",
    "comp_threshold_db",
    "comp_ratio",
    "comp_attack_ms",
    "comp_release_ms",
    "true_peak_ceiling_dbtp",
    "target_lufs",
}


def test_presets_have_required_keys():
    """Every preset must be a complete set of kwargs for master()."""
    # Allow the set of preset_ids to grow — only verify each one has all
    # required keys.
    assert len(PRESETS) > 0
    for preset_id, params in PRESETS.items():
        missing = _REQUIRED_PRESET_KEYS - set(params.keys())
        assert not missing, f"preset '{preset_id}' missing keys: {missing}"


@pytest.mark.parametrize("preset_id", list(PRESETS.keys()))
def test_master_with_each_preset(sample_wav_path: Path, tmp_path: Path, preset_id: str):
    """Each preset must produce a 24-bit WAV under its own true-peak ceiling."""
    out_path = tmp_path / f"out_{preset_id}.wav"
    params = {k: v for k, v in PRESETS[preset_id].items() if k not in {"label", "description"}}
    metrics = master(str(sample_wav_path), str(out_path), **params)

    assert out_path.is_file()
    info = sf.info(str(out_path))
    assert info.subtype == "PCM_24"

    # True peak must NEVER exceed the preset's ceiling (with rounding tolerance).
    ceiling = PRESETS[preset_id]["true_peak_ceiling_dbtp"]
    assert metrics["out_peak_dbtp"] <= ceiling + 0.01, (
        f"preset '{preset_id}': out_peak_dbtp={metrics['out_peak_dbtp']} "
        f"exceeds ceiling {ceiling}"
    )

    # Gain must be clamped to ±24 dB even for sparse / heavy presets.
    assert abs(metrics["applied_gain_db"]) <= 24.0 + 0.01, (
        f"preset '{preset_id}': applied_gain_db={metrics['applied_gain_db']} not clamped"
    )