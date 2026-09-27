"""Provider configuration, context-window state, and connection checks."""

from __future__ import annotations

import json
import os
import ssl
import tempfile
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Mapping


CONFIG_FILE = Path(__file__).with_name("config.json")
CONTEXT_WINDOW_DEFAULT = 32_768
CONTEXT_WINDOW_MIN = 1_024
CONTEXT_WINDOW_MAX = 2_000_000
CONTEXT_DETECTION_MODES = {
    "auto", "openai", "props", "ollama", "tabby", "lm_studio", "manual",
}
DETECTION_STATUSES = {"not_attempted", "success", "failed", "stale"}
DETECTION_KINDS = {"loaded", "model_limit", "contract_limit", "unknown"}
_CONNECTION_FIELDS = ("api_key", "model", "base_url")
_DETECTION_TEXT_FIELDS = (
    "detected_model", "detected_base_url", "detected_source", "detected_at",
    "detection_error",
)


class ProviderConfigError(ValueError):
    """Raised when provider settings are incomplete or malformed."""


def normalize_base_url(value):
    return str(value or "").strip().rstrip("/")


def _context_window(value, label, *, default_if_missing=True):
    if value in (None, "") and default_if_missing:
        return CONTEXT_WINDOW_DEFAULT
    if isinstance(value, bool):
        raise ProviderConfigError(f"{label} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderConfigError(f"{label} must be an integer.") from exc
    if parsed < CONTEXT_WINDOW_MIN or parsed > CONTEXT_WINDOW_MAX:
        raise ProviderConfigError(
            f"{label} must be between {CONTEXT_WINDOW_MIN:,} and {CONTEXT_WINDOW_MAX:,}."
        )
    return parsed


def _optional_detected_window(value, label):
    if value in (None, ""):
        return None
    return _context_window(value, label, default_if_missing=False)


def _clean_provider(value, label, *, required):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ProviderConfigError(f"{label} provider must be an object.")

    clean = {field: str(value.get(field) or "").strip() for field in _CONNECTION_FIELDS}
    populated = [field for field in _CONNECTION_FIELDS if clean[field]]
    if required and len(populated) != len(_CONNECTION_FIELDS):
        missing = ", ".join(field for field in _CONNECTION_FIELDS if not clean[field])
        raise ProviderConfigError(f"{label} provider is missing: {missing}.")
    clean["base_url"] = normalize_base_url(clean["base_url"])
    if clean["base_url"] and not clean["base_url"].lower().startswith(("http://", "https://")):
        raise ProviderConfigError(f"{label} provider base_url must use http:// or https://.")

    clean["context_window"] = _context_window(
        value.get("context_window"), f"{label} context_window"
    )
    mode = str(value.get("context_detection_mode") or "auto").strip().lower()
    if mode not in CONTEXT_DETECTION_MODES:
        raise ProviderConfigError(f"{label} context_detection_mode is invalid.")
    clean["context_detection_mode"] = mode
    clean["detected_context_window"] = _optional_detected_window(
        value.get("detected_context_window"), f"{label} detected_context_window"
    )
    kind = str(value.get("detected_context_kind") or "unknown").strip().lower()
    clean["detected_context_kind"] = kind if kind in DETECTION_KINDS else "unknown"
    status = str(value.get("detection_status") or "not_attempted").strip().lower()
    clean["detection_status"] = status if status in DETECTION_STATUSES else "not_attempted"
    for field in _DETECTION_TEXT_FIELDS:
        clean[field] = str(value.get(field) or "").strip()
    return clean


def validate_config(raw, *, require_main=True):
    """Return normalized settings, migrating the legacy optional-logic shape."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ProviderConfigError("Provider configuration must be an object.")
    legacy_logic = raw.get("logic") if isinstance(raw.get("logic"), dict) else {}
    if "logic_enabled" in raw:
        logic_enabled = bool(raw.get("logic_enabled"))
    else:
        logic_enabled = bool(legacy_logic and any(
            legacy_logic.get(key) for key in _CONNECTION_FIELDS
        ))
    return {
        "main": _clean_provider(raw.get("main"), "Main", required=require_main),
        "logic_enabled": logic_enabled,
        "logic": _clean_provider(legacy_logic, "Background", required=logic_enabled),
    }


def load_config(path=CONFIG_FILE, *, validate=False):
    """Load provider settings. Missing files return an empty legacy config."""
    path = Path(path)
    if not path.exists():
        return validate_config({}, require_main=True) if validate else {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle) or {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderConfigError(f"Could not read {path.name}: {exc}") from exc
    return validate_config(raw, require_main=validate) if validate else raw


def save_config(raw, path=CONFIG_FILE):
    """Validate and atomically save provider settings."""
    clean = validate_config(raw)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return clean


def _same_detection_identity(provider, *, base_url=None, model=None):
    if (
        provider.get("detected_context_window") is None
        or provider.get("context_detection_mode") == "manual"
    ):
        return False
    current_base = normalize_base_url(
        base_url if base_url is not None else provider.get("base_url")
    )
    current_model = str(
        model if model is not None else provider.get("model") or ""
    ).strip()
    return (
        normalize_base_url(provider.get("detected_base_url")) == current_base
        and str(provider.get("detected_model") or "").strip() == current_model
        and provider.get("detection_status") in {"success", "failed"}
    )


def provider_context_runtime(provider, *, provider_name, base_url=None, model=None,
                             environment_value=None):
    """Resolve persisted, environment, detected, and effective context values."""
    saved = _context_window(
        provider.get("context_window"), f"{provider_name} context_window"
    )
    runtime_value = saved
    runtime_source = "saved"
    if environment_value not in (None, ""):
        runtime_value = _context_window(
            environment_value, f"{provider_name} context window environment variable",
            default_if_missing=False,
        )
        runtime_source = "environment"
    detected = provider.get("detected_context_window") if _same_detection_identity(
        provider, base_url=base_url, model=model
    ) else None
    effective = min(runtime_value, detected) if detected is not None else runtime_value
    return {
        "provider": provider_name,
        "saved_context_window": saved,
        "runtime_context_window": runtime_value,
        "runtime_source": runtime_source,
        "detected_context_window": detected,
        "detected_context_kind": provider.get("detected_context_kind") or "unknown",
        "detected_source": provider.get("detected_source") or "",
        "detected_at": provider.get("detected_at") or "",
        "detection_status": provider.get("detection_status") or "not_attempted",
        "detection_error": provider.get("detection_error") or "",
        "model": str(model if model is not None else provider.get("model") or "").strip(),
        "base_url": normalize_base_url(
            base_url if base_url is not None else provider.get("base_url")
        ),
        "effective_context_window": effective,
    }


def runtime_settings(raw=None, environ: Mapping[str, str] | None = None):
    """Resolve saved settings and runtime-only environment overrides."""
    raw = load_config() if raw is None else raw
    clean = validate_config(raw, require_main=False)
    environ = os.environ if environ is None else environ
    main = clean["main"]
    logic = clean["logic"]
    main_model = str(environ.get("BRAIN_MODEL") or main.get("model") or "").strip()
    main_url = normalize_base_url(environ.get("BRAIN_BASE_URL") or main.get("base_url"))
    logic_enabled = bool(clean["logic_enabled"])
    logic_model = str(
        environ.get("BRAIN_LOGIC_MODEL") or logic.get("model") or ""
    ).strip()
    logic_url = normalize_base_url(
        environ.get("BRAIN_LOGIC_BASE_URL") or logic.get("base_url")
    )
    main_context = provider_context_runtime(
        main, provider_name="main", base_url=main_url, model=main_model,
        environment_value=environ.get("BRAIN_CONTEXT_WINDOW"),
    )
    if logic_enabled:
        logic_context = provider_context_runtime(
            logic, provider_name="background", base_url=logic_url, model=logic_model,
            environment_value=environ.get("BRAIN_LOGIC_CONTEXT_WINDOW"),
        )
    else:
        logic_context = deepcopy(main_context)
        logic_context["requested_provider"] = "background"
        logic_context["provider"] = "main"
    return {
        "API_KEY": str(environ.get("BRAIN_API_KEY") or main.get("api_key") or "").strip(),
        "MODEL_NAME": main_model,
        "BASE_URL": main_url,
        "LOGIC_ENABLED": logic_enabled,
        "LOGIC_API_KEY": str(
            environ.get("BRAIN_LOGIC_API_KEY") or logic.get("api_key") or ""
        ).strip(),
        "LOGIC_MODEL": logic_model,
        "LOGIC_BASE_URL": logic_url,
        "MAIN_CONTEXT": main_context,
        "LOGIC_CONTEXT": logic_context,
    }


def detection_payload(result, *, base_url, model):
    """Return fields safe to merge after a successful or failed probe."""
    payload = {
        "detection_status": str(result.get("status") or "failed"),
        "detection_error": str(result.get("error") or ""),
    }
    if result.get("context_window") is not None:
        payload.update({
            "detected_context_window": _optional_detected_window(
                result.get("context_window"), "Detected context window"
            ),
            "detected_context_kind": str(result.get("kind") or "unknown"),
            "detected_source": str(result.get("source") or ""),
            "detected_at": str(result.get("detected_at") or ""),
            "detected_model": str(model or "").strip(),
            "detected_base_url": normalize_base_url(base_url),
        })
    return payload


def merge_pending_detection(provider, pending, *, base_url, model, mode):
    """Merge a UI probe only when it still matches the form being saved."""
    merged = dict(provider or {})
    pending = pending if isinstance(pending, dict) else {}
    if (
        normalize_base_url(pending.get("base_url")) == normalize_base_url(base_url)
        and str(pending.get("model") or "").strip() == str(model or "").strip()
        and str(pending.get("mode") or "auto") == str(mode or "auto")
    ):
        merged.update(pending.get("detection") or {})
    return merged


def test_provider(base_url, api_key, *, timeout=15):
    """Call the OpenAI-compatible models endpoint and return a useful result."""
    base_url = normalize_base_url(base_url)
    api_key = str(api_key or "").strip()
    if not base_url or not api_key:
        return {"ok": False, "status": None, "error": "Base URL and API key are required."}
    if not base_url.lower().startswith(("http://", "https://")):
        return {"ok": False, "status": None, "error": "Base URL must use http:// or https://."}
    url = base_url if base_url.endswith("/models") else base_url + "/models"
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "http://localhost:8001",
        "X-Title": "BrainEngine2",
    })
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl.create_default_context()
        ) as response:
            status = response.status
            return {"ok": status == 200, "status": status, "error": None}
    except Exception as exc:
        return {"ok": False, "status": getattr(exc, "code", None), "error": str(exc)}
