"""Persisted Step input policy; no shared request state or UI dependencies."""

import copy
import json
import re


STEP_FIELDS = (
    "prior_output_reference", "inject_prior_outputs", "inject_past_same_step_outputs",
    "exclude_chat_log", "max_output_tokens", "variable_update_prompt",
)


def step_settings(raw):
    result = {}
    for key, default in (("inject_prior_outputs", True),
                         ("inject_past_same_step_outputs", True), ("exclude_chat_log", False)):
        value = raw.get(key, default)
        if type(value) is not bool:
            raise ValueError(f"{raw.get('name', 'Step')}: {key} must be boolean")
        result[key] = value
    limit = raw.get("max_output_tokens")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 131072):
        raise ValueError(f"{raw.get('name', 'Step')}: max_output_tokens must be 1..131072 or null")
    result["max_output_tokens"] = limit
    prompt = raw.get("variable_update_prompt", "")
    if not isinstance(prompt, str) or len(prompt) > 30000:
        raise ValueError("variable_update_prompt must be text of at most 30000 characters")
    result["variable_update_prompt"] = prompt
    ref = raw.get("prior_output_reference", {"mode": "all", "step_ids": []})
    if not isinstance(ref, dict) or set(ref) - {"mode", "step_ids"}:
        raise ValueError("invalid prior_output_reference")
    mode, ids = ref.get("mode"), ref.get("step_ids", [])
    if mode not in {"all", "none", "selected"} or not isinstance(ids, list):
        raise ValueError("invalid prior_output_reference mode or IDs")
    if any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("reference IDs must be nonempty strings")
    ids = list(dict.fromkeys(ids)) if mode == "selected" else []
    if mode == "selected" and not ids:
        raise ValueError("Select at least one earlier Step, or choose none")
    result["prior_output_reference"] = {"mode": mode, "step_ids": ids}
    return result


def normalize_references(steps, structural_edit=False):
    """External invalid references fail; structural edits remove invalid IDs only."""
    result = copy.deepcopy(sorted(steps, key=lambda item: item["step"]))
    earlier = []
    for slot in result:
        ref = slot.get("prior_output_reference", {"mode": "all", "step_ids": []})
        if ref["mode"] == "selected":
            invalid = set(ref["step_ids"]) - set(earlier)
            if invalid and not structural_edit:
                raise ValueError(f"{slot['name']}: references must be earlier Steps: {sorted(invalid)}")
            ids = [identity for identity in earlier if identity in ref["step_ids"]]
            slot["prior_output_reference"] = (
                {"mode": "selected", "step_ids": ids} if ids else {"mode": "all", "step_ids": []})
        earlier.append(slot["id"])
    return result


def resolve_prior_outputs(slot, completed):
    ref = slot.get("prior_output_reference", {"mode": "all"})
    if ref["mode"] == "none":
        return [], []
    wanted = set(ref.get("step_ids", []))
    selected = [item for item in completed if ref["mode"] == "all" or item["id"] in wanted]
    missing = sorted(wanted - {item["id"] for item in selected}) if ref["mode"] == "selected" else []
    return sorted(selected, key=lambda item: item["step"]), missing


def csv_settings(row, number):
    result = {}
    for field in STEP_FIELDS:
        value = row.get(f"step{number}_{field}")
        if value in (None, ""):
            continue
        if field == "variable_update_prompt":
            result[field] = value
        elif field == "max_output_tokens":
            if not re.fullmatch(r"[0-9]+", value):
                raise ValueError("max_output_tokens must be a decimal integer")
            result[field] = int(value)
        else:
            result[field] = json.loads(value)
    return result


def connector_context(snapshot):
    """Only the claimed snapshot's person/settings allowlist enters model input."""
    if not isinstance(snapshot, dict):
        return ""
    user = snapshot.get("user") or {}
    character = snapshot.get("character") or {}
    card = character.get("card") or {}
    fields = {"user.name": user.get("name"), "user.persona": user.get("persona"),
              "character.name": character.get("name")}
    for key in ("description", "personality", "scenario", "depth_prompt", "creator_notes"):
        fields[f"character.{key}"] = card.get(key)
    group = snapshot.get("group")
    if isinstance(group, dict):
        for key in ("active_characters", "all_characters"):
            value = group.get(key)
            if isinstance(value, list):
                fields[f"group.{key}"] = ", ".join(name for item in value
                    if isinstance(name := item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None, str))
        metadata = (snapshot.get("chat") or {}).get("metadata") or {}
        fields["group.scenario"] = metadata.get("scenario")
    lines = [f"{key}: {value}" for key, value in fields.items() if isinstance(value, str) and value.strip()]
    return "[CONNECTOR CONTEXT]\n" + "\n".join(lines) + "\n[/CONNECTOR CONTEXT]" if lines else ""
