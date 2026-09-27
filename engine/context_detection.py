"""Safe context-window detection adapters for local and compatible providers."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import provider_config


MAX_RESPONSE_BYTES = 1_048_576
MAX_REDIRECTS = 2
_CANDIDATE_PATHS = (
    ("context_length",),
    ("max_context_length",),
    ("context_window",),
    ("max_model_len",),
    ("inputTokenLimit",),
    ("tokens",),
    ("info", "contextLength"),
    ("limits", "context"),
)
_KIND_RANK = {"loaded": 0, "model_limit": 1, "contract_limit": 2, "unknown": 3}


class DetectionError(ValueError):
    pass


def _origin(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme.lower(), parsed.hostname or "", parsed.port


def _root_url(base_url):
    parsed = urllib.parse.urlsplit(provider_config.normalize_base_url(base_url))
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _models_url(base_url):
    base = provider_config.normalize_base_url(base_url)
    return base if base.endswith("/models") else base + "/models"


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin):
        super().__init__()
        self.origin = origin
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.count += 1
        if self.count > MAX_REDIRECTS:
            raise DetectionError("too_many_redirects")
        target = urllib.parse.urljoin(req.full_url, newurl)
        if _origin(target) != self.origin:
            raise DetectionError("cross_origin_redirect")
        return super().redirect_request(req, fp, code, msg, headers, target)


def _request_json(method, url, headers, body, timeout):
    payload = None
    request_headers = dict(headers or {})
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=payload, headers=request_headers, method=method
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _RedirectHandler(_origin(url)),
    )
    with opener.open(request, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise DetectionError("response_too_large")
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DetectionError("invalid_json") from exc


def _read_path(value, path):
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _valid_window(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if provider_config.CONTEXT_WINDOW_MIN <= parsed <= provider_config.CONTEXT_WINDOW_MAX:
        return parsed
    return None


def _model_rows(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "models"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def _row_model_id(row):
    if not isinstance(row, dict):
        return ""
    return str(
        row.get("id") or row.get("model") or row.get("name")
        or row.get("model_name") or row.get("key") or ""
    ).strip()


def _matching_model(rows, model):
    wanted = str(model or "").strip()
    return next((row for row in rows if _row_model_id(row) == wanted), None)


def _candidate(row):
    values = []
    for path in _CANDIDATE_PATHS:
        value = _valid_window(_read_path(row, path))
        if value is not None:
            values.append(value)
    return min(values) if values else None


def _headers(api_key):
    headers = {
        "Accept": "application/json",
        "HTTP-Referer": "http://localhost:8001",
        "X-Title": "BrainEngine2",
    }
    if str(api_key or "").strip():
        headers["Authorization"] = "Bearer " + str(api_key).strip()
    return headers


def _probe_openai(requester, base_url, api_key, model, timeout):
    payload = requester("GET", _models_url(base_url), _headers(api_key), None, timeout)
    row = _matching_model(_model_rows(payload), model)
    value = _candidate(row) if row else None
    return (value, "model_limit", "openai_models") if value else None


def _probe_props(requester, base_url, api_key, _model, timeout):
    url = _root_url(base_url) + "/props"
    payload = requester("GET", url, _headers(api_key), None, timeout)
    value = _valid_window(_read_path(payload, ("default_generation_settings", "n_ctx")))
    return (value, "loaded", "props") if value else None


def _probe_ollama(requester, base_url, api_key, model, timeout):
    root = _root_url(base_url)
    payload = requester("GET", root + "/api/ps", _headers(api_key), None, timeout)
    row = _matching_model(_model_rows(payload), model)
    value = _valid_window(row.get("context_length")) if isinstance(row, dict) else None
    if value:
        return value, "loaded", "ollama_ps"
    payload = requester(
        "POST", root + "/api/show", _headers(api_key), {"model": str(model)}, timeout
    )
    candidates = []
    model_info = payload.get("model_info") if isinstance(payload, dict) else {}
    if isinstance(model_info, dict):
        for key, raw in model_info.items():
            if str(key).endswith(".context_length"):
                value = _valid_window(raw)
                if value:
                    candidates.append(value)
    return (min(candidates), "model_limit", "ollama_show") if candidates else None


def _probe_tabby(requester, base_url, api_key, model, timeout):
    payload = requester(
        "GET", _root_url(base_url) + "/v1/model", _headers(api_key), None, timeout
    )
    returned_model = _row_model_id(payload)
    if returned_model and returned_model != str(model or "").strip():
        return None
    value = _valid_window(_read_path(payload, ("parameters", "max_seq_len")))
    return (value, "loaded", "tabby_model") if value else None


def _probe_lm_studio(requester, base_url, api_key, model, timeout):
    payload = requester(
        "GET", _root_url(base_url) + "/api/v1/models",
        _headers(api_key), None, timeout,
    )
    row = _matching_model(_model_rows(payload), model)
    if not isinstance(row, dict):
        return None
    instances = row.get("loaded_instances")
    if isinstance(instances, list) and len(instances) == 1:
        value = _valid_window(_read_path(instances[0], ("config", "context_length")))
        if value:
            return value, "loaded", "lm_studio_loaded_instance"
    if isinstance(instances, list) and len(instances) > 1:
        return None
    value = _valid_window(row.get("max_context_length"))
    return (value, "model_limit", "lm_studio_model") if value else None


_PROBES = {
    "openai": _probe_openai,
    "props": _probe_props,
    "ollama": _probe_ollama,
    "tabby": _probe_tabby,
    "lm_studio": _probe_lm_studio,
}


def detect_context_window(base_url, api_key, model, mode="auto", *,
                          timeout_per=3.0, timeout_total=10.0, requester=None):
    """Probe fixed same-origin adapters and return a body-free result."""
    base_url = provider_config.normalize_base_url(base_url)
    model = str(model or "").strip()
    mode = str(mode or "auto").strip().lower()
    if mode == "manual":
        return {"status": "not_attempted", "error": "manual_mode"}
    if mode not in provider_config.CONTEXT_DETECTION_MODES:
        return {"status": "failed", "error": "invalid_mode"}
    if not base_url or not model:
        return {"status": "failed", "error": "missing_connection_identity"}
    try:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"status": "failed", "error": "invalid_base_url"}
    except ValueError:
        return {"status": "failed", "error": "invalid_base_url"}

    requester = requester or _request_json
    adapters = (
        ["openai", "props", "ollama", "tabby", "lm_studio"]
        if mode == "auto" else [mode]
    )
    started = time.monotonic()
    found = []
    errors = []
    for adapter in adapters:
        if time.monotonic() - started >= timeout_total:
            errors.append("total_timeout")
            break
        try:
            result = _PROBES[adapter](
                requester, base_url, api_key, model,
                min(float(timeout_per), max(0.05, timeout_total - (time.monotonic() - started))),
            )
            if result:
                found.append(result)
                if result[1] == "loaded":
                    break
        except Exception as exc:
            errors.append(
                str(exc) if isinstance(exc, DetectionError) else type(exc).__name__
            )
    if not found:
        return {
            "status": "failed",
            "error": errors[-1] if errors else "context_window_not_found",
        }
    found.sort(key=lambda item: (_KIND_RANK.get(item[1], 99), item[0]))
    best_rank = _KIND_RANK.get(found[0][1], 99)
    peers = [item for item in found if _KIND_RANK.get(item[1], 99) == best_rank]
    selected = min(peers, key=lambda item: item[0])
    source = selected[2] if len(peers) == 1 else "multiple_candidates_minimum"
    return {
        "status": "success",
        "context_window": selected[0],
        "kind": selected[1],
        "source": source,
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "error": "",
    }
