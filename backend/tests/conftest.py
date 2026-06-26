"""
conftest.py — pytest fixtures for the audio_engine test suite.

The fixtures here are self-contained: they synthesize audio from numpy rather
than checking in real WAV files. This keeps the repo small and the tests
deterministic across machines.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _synth_stereo(
    duration_s: float = 5.0,
    sr: int = 44100,
    freqs: tuple[float, ...] = (440.0, 880.0),
    amplitude: float = 0.5,
) -> np.ndarray:
    """Synthesize a stereo signal as a (2, samples) float32 array.

    Both channels get the same content (mono material panned to both sides)
    — sufficient for exercising the mastering chain.
    """
    n = int(duration_s * sr)
    t = np.arange(n) / sr
    sig = np.zeros(n, dtype=np.float32)
    for f in freqs:
        sig += np.sin(2 * np.pi * f * t).astype(np.float32)
    sig /= len(freqs)
    sig *= amplitude
    return np.stack([sig, sig], axis=0)


@pytest.fixture(scope="session")
def sample_wav_path() -> Path:
    """5-second 440/880Hz stereo test tone, generated once per test session."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURES_DIR / "sample.wav"
    if not path.exists():
        audio = _synth_stereo()
        sf.write(path, audio.T, 44100, subtype="PCM_16")
    return path


@pytest.fixture(scope="session")
def lowheavy_wav_path() -> Path:
    """Signal heavily weighted to 250Hz — used to exercise the mud flag."""
    path = FIXTURES_DIR / "lowheavy.wav"
    if not path.exists():
        audio = _synth_stereo(
            duration_s=5.0,
            freqs=(250.0,),  # single muddy fundamental
            amplitude=0.7,
        )
        sf.write(path, audio.T, 44100, subtype="PCM_16")
    return path


@pytest.fixture(scope="session")
def clipped_wav_path() -> Path:
    """Hard-clipped 440Hz signal — every cycle hits the rails."""
    path = FIXTURES_DIR / "clipped.wav"
    if not path.exists():
        sig = _synth_stereo(freqs=(440.0,), amplitude=0.5)[0]
        # Drive the signal above 1.0 first, then clip to the rails so the
        # saved file contains many samples at exactly ±1.0 (full-scale).
        sig = sig * 2.5
        sig = np.clip(sig, -1.0, 1.0)
        stereo = np.stack([sig, sig], axis=0)
        sf.write(path, stereo.T, 44100, subtype="PCM_16")
    return path