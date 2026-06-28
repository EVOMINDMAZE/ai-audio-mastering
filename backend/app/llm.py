"""
LLM-driven mastering parameter recommender.

Wraps the DeepSeek Chat Completions API (OpenAI-compatible) with one job:
given an audio analysis payload + the preset catalog, return a JSON object
saying which preset to use as a base and which parameters to override.

This is intentionally narrow. The LLM is NOT used to render audio — that is
still done by `audio_engine.master()` with the merged parameter dict. The LLM
only picks the parameters, which is a small, well-bounded task that a chat
model handles reliably when given a structured prompt and a constrained
output schema.

Failure mode policy: if the LLM call fails for any reason (network, auth,
malformed JSON, schema violation), `recommend_preset()` raises. The route
layer catches the exception and falls back to the "streaming" preset with
no overrides so the user still gets a result.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .audio_engine import PRESETS
from .config import get_settings

logger = logging.getLogger(__name__)


# Allow-list of parameter names the LLM is permitted to override. Anything
# outside this set is silently dropped — defence-in-depth against prompt
# injection that tries to widen the LLM's authority.
_OVERRIDABLE_KEYS: frozenset[str] = frozenset(
    {
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
        "time_stretch_rate",
        "reverb_amount",
        "pitch_shift_semitones",
    }
)

# Hard numeric ranges per parameter — applied AFTER the LLM's response so a
# hallucinated value can't push the chain into an unsafe / pointless range.
_OVERRIDE_RANGES: Dict[str, Tuple[float, float]] = {
    "hpf_hz": (10.0, 200.0),
    "eq_low_gain_db": (-6.0, 8.0),
    "eq_mid_freq_hz": (100.0, 8000.0),
    "eq_mid_gain_db": (-9.0, 6.0),
    "eq_high_freq_hz": (2000.0, 16000.0),
    "eq_high_gain_db": (-9.0, 9.0),
    "comp_threshold_db": (-40.0, -6.0),
    "comp_ratio": (1.0, 12.0),
    "comp_attack_ms": (0.5, 200.0),
    "comp_release_ms": (10.0, 1000.0),
    "true_peak_ceiling_dbtp": (-3.0, -0.1),
    "target_lufs": (-23.0, -8.0),
    "time_stretch_rate": (0.5, 2.0),
    "reverb_amount": (0.0, 1.0),
    "pitch_shift_semitones": (-12.0, 12.0),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Raised when the LLM is unavailable, returns bad JSON, or violates schema."""


def is_llm_available() -> bool:
    """True when LLM_ENABLED=True AND a DeepSeek key is configured."""
    s = get_settings()
    return bool(s.llm_enabled and s.deepseek_api_key)


def recommend_preset(features: Dict[str, Any]) -> Dict[str, Any]:
    """Call DeepSeek and return {preset_id, overrides, reasoning}.

    Returns the merged parameter dict ready to feed into `audio_engine.master`.
    Raises `LLMError` on any failure — the route layer falls back to a sane
    default so the user is never blocked by LLM outages.
    """
    if not is_llm_available():
        raise LLMError("LLM is not enabled (set LLM_ENABLED=true and DEEPSEEK_API_KEY).")

    system_prompt = _build_system_prompt()
    user_payload = _format_features(features)
    raw = _call_deepseek(system_prompt, user_payload)
    parsed = _parse_and_validate(raw)
    merged = _merge_into_preset(parsed)
    return merged


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_preset_catalog() -> str:
    """Compact preset catalog for the prompt — id + label + character only.

    The full parameter dict is NOT included in the prompt to keep it small.
    We only need the LLM to pick a base preset_id and override knobs that
    are common across all of them.
    """
    lines = []
    for pid, params in PRESETS.items():
        lines.append(
            f"- `{pid}`: {params['label']} — {params['description']}"
        )
    return "\n".join(lines)


def _build_system_prompt() -> str:
    catalog = _build_preset_catalog()
    keys = sorted(_OVERRIDABLE_KEYS)
    keys_list = ", ".join(f"`{k}`" for k in keys)

    return (
        "You are an audio mastering engineer selecting parameters from a fixed catalog. "
        "You do NOT render audio — you only pick parameters. The DSP chain runs server-side.\n\n"
        "Given the analysis below, choose ONE base preset_id from the catalog, then list "
        "any parameter overrides you want to apply. Keep overrides minimal — only change a "
        "parameter when the analysis clearly calls for it. When in doubt, leave it at the "
        "preset's default.\n\n"
        f"AVAILABLE PRESETS:\n{catalog}\n\n"
        f"OVERRIDABLE PARAMETERS (only these keys are accepted): {keys_list}\n\n"
        "OUTPUT FORMAT — respond with ONLY this exact JSON object, no prose, no markdown:\n"
        "{\n"
        '  "preset_id": "<one of the catalog ids above>",\n'
        '  "overrides": { <subset of the overridable parameter keys, with numeric values> },\n'
        '  "reasoning": "<1-2 short sentences explaining the choice>"\n'
        "}\n\n"
        "RULES:\n"
        "- preset_id MUST be one of the catalog ids. No invented names.\n"
        "- overrides keys MUST be from the allowed list. Unknown keys are silently dropped.\n"
        "- target_lufs range: -23 to -8 (streaming is -14, podcast is -16, EDM is -8).\n"
        "- comp_ratio range: 1.0 (no compression) to 12.0 (brick-wall).\n"
        "- eq_low_gain_db / eq_high_gain_db range: -9 to +9 dB.\n"
        "- hpf_hz: keep ≥ 20 for music, 80+ for speech.\n"
        "- Do NOT apply time_stretch_rate, reverb_amount, or pitch_shift_semitones unless "
        "  the analysis explicitly suggests them (e.g. very long track, dry recording).\n"
        "- If the input is already loud (in_lufs > -12), prefer lower target_lufs and gentler "
        "  compression so you don't pile on more limiting.\n"
        "- If mud_flag is true, do NOT increase eq_low_gain_db; consider reducing it.\n"
        "- If clipping_flag is true, leave true_peak_ceiling_dbtp at -1.0 or tighter.\n"
        "- If `crest_factor_db > 14`, the input is already mastered — pick `acoustic` "
        "and don't apply additional compression (no eq_low_gain_db boost, gentle comp).\n"
        "- If `stereo_width < 0.1`, the input is mono or near-mono — do not apply any "
        "stereo widening. If `stereo_width > 0.7`, it's already wide — don't add stereo "
        "enhancement. The engine doesn't have a stereo widener knob, but consider keeping "
        "eq_mid_freq_hz lower and avoiding high-frequency boosts that would expose mono "
        "mismatch artifacts.\n"
        "- If `genre` is set (e.g. 'rock', 'hip-hop', 'electronic', 'classical', 'speech', "
        "'podcast'), weight the preset choice accordingly: hip-hop/electronic→`loud` or "
        "`edm`; classical/jazz→`acoustic` or `warm`; speech→`podcast`; otherwise prefer "
        "`streaming` as the safe default.\n"
        "- `band_energy_low_mid_high` is a 3-element list [low_frac, mid_frac, high_frac]. "
        "If low_frac > 0.5 (muddy), reduce `eq_low_gain_db` to ≤ 0. If high_frac > 0.5 "
        "(too bright), reduce `eq_high_gain_db` to ≤ 0.\n"
    )


def _format_features(features: Dict[str, Any]) -> str:
    """Render the analysis dict as a compact JSON payload for the prompt."""
    # Strip fields the LLM doesn't need (spectrum_peaks can be verbose).
    keep = {
        "bpm",
        "rms_dbfs",
        "peak_dbfs",
        "lufs_integrated",
        "true_peak_dbtp",
        "mud_flag",
        "clipping_flag",
        "duration_s",
        "sample_rate",
        # Phase 3 — extended analysis + genre
        "crest_factor_db",
        "stereo_width",
        "spectral_centroid_hz",
        "spectral_flatness",
        "band_energy_low_mid_high",
        "perceived_loudness_db",
        "genre",
    }
    slim = {k: v for k, v in features.items() if k in keep}
    return json.dumps(slim, indent=2, default=str)


# ---------------------------------------------------------------------------
# DeepSeek call
# ---------------------------------------------------------------------------


def _call_deepseek(system_prompt: str, user_payload: str) -> str:
    s = get_settings()
    url = f"{s.deepseek_base_url.rstrip('/')}/chat/completions"
    body = {
        "model": s.deepseek_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "max_tokens": 600,
    }
    headers = {
        "Authorization": f"Bearer {s.deepseek_api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=s.deepseek_timeout_s) as client:
            r = client.post(url, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise LLMError(f"DeepSeek HTTP error: {e}") from e
    except Exception as e:  # noqa: BLE001 — surface anything to the caller
        raise LLMError(f"DeepSeek call failed: {e}") from e

    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"DeepSeek response missing content: {data!r}") from e


# ---------------------------------------------------------------------------
# Response parsing & validation
# ---------------------------------------------------------------------------


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_and_validate(raw: str) -> Dict[str, Any]:
    """Parse the LLM string, validate the schema, clamp numeric overrides."""
    # Strip markdown fences if present (the prompt says no prose, but defence).
    fenced = _JSON_FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1)
    raw = raw.strip()

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"LLM returned non-JSON: {raw[:200]!r}") from e

    if not isinstance(obj, dict):
        raise LLMError(f"LLM returned non-object JSON: {type(obj).__name__}")

    preset_id = obj.get("preset_id")
    if not isinstance(preset_id, str) or preset_id not in PRESETS:
        raise LLMError(f"LLM picked invalid preset_id: {preset_id!r}")

    raw_overrides = obj.get("overrides", {})
    if not isinstance(raw_overrides, dict):
        raise LLMError(f"LLM returned non-dict overrides: {type(raw_overrides).__name__}")

    clamped: Dict[str, float] = {}
    for key, value in raw_overrides.items():
        if key not in _OVERRIDABLE_KEYS:
            logger.info("LLM returned unknown override key %r — dropping", key)
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            logger.info("LLM returned non-numeric %s=%r — dropping", key, value)
            continue
        lo, hi = _OVERRIDE_RANGES[key]
        clamped[key] = max(lo, min(hi, v))

    reasoning = obj.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""

    return {
        "preset_id": preset_id,
        "overrides": clamped,
        "reasoning": reasoning.strip()[:500],
    }


def _merge_into_preset(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Build the final parameter dict: PRESETS[base] + clamped overrides."""
    base = dict(PRESETS[parsed["preset_id"]])
    base_label = base.pop("label")
    base_desc = base.pop("description")
    base.update(parsed["overrides"])
    return {
        "preset_id": parsed["preset_id"],
        "preset_label": base_label,
        "preset_description": base_desc,
        "params": base,
        "overrides": parsed["overrides"],
        "reasoning": parsed["reasoning"],
    }


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def fallback_recommendation(features: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic recommendation used when the LLM is unavailable.

    Simple rules: muddy → acoustic; loud → streaming; quiet → loud/club; etc.
    Mirrors the spirit of the LLM prompt so the UX is similar even without it.
    """
    lufs = features.get("lufs_integrated", -16.0)
    mud = features.get("mud_flag", False)
    peak = features.get("true_peak_dbtp", -3.0)
    bpm = features.get("bpm", 120.0)

    if mud:
        pid = "acoustic"
    elif lufs > -10:
        pid = "streaming"
    elif bpm >= 130:
        pid = "edm"
    elif lufs < -22:
        pid = "loud"
    else:
        pid = "streaming"

    merged = _merge_into_preset(
        {"preset_id": pid, "overrides": {}, "reasoning": "Heuristic fallback (LLM unavailable)."}
    )
    return merged