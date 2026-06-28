"""
DEPRECATED — kept for unit tests only.

The production mastering chain now uses ``pedalboard.Limiter`` (lookahead,
transient-aware gain reduction) instead of this uniform-gain brick-wall.
The function below pulls the *entire* signal down by however much the peak
exceeds the ceiling — so a 0.1 dB overshoot costs 0.1 dB of overall loudness,
and a 3 dB overshoot costs 3 dB. That hurts dynamics and is why the LLM
recommendation review flagged it as the #1 quality issue.

Don't import this from the production code paths — it lives here purely so
the existing unit tests that pin its behaviour don't have to be rewritten.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly


_EPS = 1e-12


def _db(x: float) -> float:
    return 20.0 * np.log10(max(abs(x), _EPS))


def true_peak_brick_wall(
    audio: np.ndarray,
    sr: int,
    ceiling_dbtp: float = -1.0,
    oversample: int = 8,
) -> np.ndarray:
    """Uniform-gain brick-wall — DEPRECATED, kept for unit tests only."""
    threshold = 10.0 ** (ceiling_dbtp / 20.0)

    if audio.ndim == 1:
        up = resample_poly(audio, oversample, 1)
    else:
        up = resample_poly(audio.T, oversample, 1, axis=0)

    peak = float(np.max(np.abs(up)))
    if peak <= threshold:
        return audio.astype(np.float32, copy=False)

    gain = threshold / peak
    return (audio * gain).astype(np.float32)