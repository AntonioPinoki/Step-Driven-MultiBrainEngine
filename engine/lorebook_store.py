import copy
import json
import os
import random
import re
import threading
import uuid


HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
LOREBOOK_DIR = os.path.join(PROJECT_ROOT, "Lorebooks")
BOOKS_DIR = os.path.join(LOREBOOK_DIR, "books")
SETTINGS_FILE = os.path.join(LOREBOOK_DIR, "settings.json")
MAX_BOOK_BYTES = 8 * 1024 * 1024
_lock = threading.Lock()

DEFAULT_SETTINGS = {
    "scan_depth": 2,
    "case_sensitive": False,
    "match_whole_words": False,
    "recursive": False,
    "max_recursion_steps": 0,
    "token_budget": 2048,
}

DEFAULT_OBSERVATION_SOURCES = [
    "chat", "character.description", "character.personality", "scenario",
    "character.depth_prompt", "character.creator_notes", "user.persona",
]
CONDITION_MODES = {"normal", "hierarchical", "ordered"}
OBSERVATION_MODES = {"standard", "past_same_step_only"}
CONTENT_MODES = {"fixed", "staged"}


class SettingsDocumentError(ValueError):
    """Raised when an existing settings.json cannot be safely read."""


def _clone(value):
    return copy.deepcopy(value)


def _identifier(value, prefix):
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip()).strip("_")
    return text[:80] or f"{prefix}_{uuid.uuid4().hex[:12]}"


def safe_tag_name(value):
    """Return a stable XML-ish tag name while preserving Japanese names."""
    text = re.sub(r"[\x00-\x20<>/&\\\"']+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")[:100]
    if not text:
        return "entry"
    if text[0].isdigit() or text.lower() == "lorebook":
        text = "entry_" + text
    return text


def _nullable_bool(value):
    return value if isinstance(value, bool) else None


def _nullable_int(value, minimum, maximum):
    if value in (None, ""):
        return None
    value = int(value)
    if value < minimum or value > maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    return value


def _strings(value):
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:500] for item in value if str(item).strip()]


def _brainengine_fields(raw):
    extensions = raw.get("extensions") if isinstance(raw.get("extensions"), dict) else {}
    fields = extensions.get("brainengine")
    return fields if isinstance(fields, dict) else {}


def _stages(value, fallback=""):
    if not isinstance(value, list):
        value = [fallback]
    result = []
    for item in value:
        text = str(item.get("content", "") if isinstance(item, dict) else item)
        if len(text) > 100000:
            raise ValueError("stage content must be 100000 characters or fewer")
        result.append(text)
    return result or [str(fallback or "")]


def _ordered_words(value):
    if isinstance(value, dict):
        value = value.get("words")
    return _strings(value)


def _tree_nodes(value, entry_name, seen_ids=None, seen_names=None, depth=0):
    seen_ids = seen_ids if seen_ids is not None else set()
    seen_names = seen_names if seen_names is not None else set()
    result = []
    for index, raw in enumerate(value if isinstance(value, list) else []):
        raw = raw if isinstance(raw, dict) else {}
        name = str(raw.get("name") or f"Node {index + 1}").strip()[:100]
        node_id = _identifier(raw.get("node_id") or raw.get("id"), "node")
        if node_id in seen_ids:
            raise ValueError(f"{entry_name} hierarchical node ids must be unique")
        if name in seen_names:
            raise ValueError(f"{entry_name} hierarchical node names must be unique")
        seen_ids.add(node_id)
        seen_names.add(name)
        content = str(raw.get("content") or "")
        words = _strings(raw.get("words", raw.get("keys", [])))
        minimum = 2 if depth == 0 else 1
        if len(words) < minimum:
            level = "root" if depth == 0 else "child"
            raise ValueError(
                f"{entry_name} hierarchical {level} nodes require at least {minimum} word(s)")
        result.append({
            "node_id": node_id,
            "name": name,
            "words": words,
            "content": content,
            "stages": _stages(raw.get("stages"), content),
            "children": _tree_nodes(
                raw.get("children"), entry_name, seen_ids, seen_names, depth + 1),
        })
    return result


def validate_entry(raw, index=0):
    if not isinstance(raw, dict):
        raise ValueError("each lorebook entry must be an object")
    name = str(raw.get("name") or raw.get("comment") or f"Entry {index + 1}").strip()[:100]
    content = str(raw.get("content") or "").strip()
    if len(content) > 100000:
        raise ValueError(f"{name} content must be 100000 characters or fewer")
    probability = raw.get("probability", 100)
    probability = max(0, min(100, int(probability if probability is not None else 100)))
    brain = _brainengine_fields(raw)
    condition_mode = str(raw.get("condition_mode", brain.get("condition_mode", "normal")))
    observation_mode = str(raw.get("observation_mode", brain.get("observation_mode", "standard")))
    content_mode = str(raw.get("content_mode", brain.get("content_mode", "fixed")))
    if condition_mode not in CONDITION_MODES:
        raise ValueError(f"{name} has an invalid condition mode")
    if observation_mode not in OBSERVATION_MODES:
        raise ValueError(f"{name} has an invalid observation mode")
    if content_mode not in CONTENT_MODES:
        raise ValueError(f"{name} has an invalid content mode")
    constant = bool(raw.get("constant", False))
    condition_data = raw.get("condition_data", brain.get("condition_data", {}))
    if condition_mode == "ordered":
        words = _ordered_words(condition_data)
        if len(words) < 2:
            raise ValueError(f"{name} ordered condition requires at least two words")
        if constant:
            raise ValueError(f"{name} cannot combine ordered condition with always active")
        condition_data = {"words": words}
    elif condition_mode == "hierarchical":
        nodes = _tree_nodes(
            condition_data.get("nodes") if isinstance(condition_data, dict) else condition_data,
            name,
        )
        if not nodes:
            raise ValueError(f"{name} hierarchical condition requires a root node")
        if constant:
            raise ValueError(f"{name} cannot combine hierarchical condition with always active")
        condition_data = {"nodes": nodes}
    else:
        condition_data = _clone(condition_data if isinstance(condition_data, dict) else {})
    if observation_mode == "past_same_step_only" and constant:
        raise ValueError(f"{name} cannot combine past same-step mode with always active")
    sources = _strings(raw.get(
        "observation_sources", brain.get("observation_sources", DEFAULT_OBSERVATION_SOURCES)))
    allowed_sources = set(DEFAULT_OBSERVATION_SOURCES) | {"previous_steps"}
    sources = [item for item in sources if item in allowed_sources]
    if observation_mode == "standard" and not sources:
        raise ValueError(f"{name} requires at least one observation source")
    normalized_stages = _stages(raw.get("stages", brain.get("stages")), content)
    if content_mode == "staged" and not any(stage.strip() for stage in normalized_stages):
        raise ValueError(f"{name} staged content requires at least one non-empty stage")
    extension_copy = _clone(raw.get("extensions") or {})
    extension_copy["brainengine"] = {
        **_clone(brain),
        "entry_id": _identifier(
            raw.get("entry_id") or brain.get("entry_id") or raw.get("id")
            or raw.get("uid") or f"entry_{index + 1}", "entry"),
        "observation_mode": observation_mode,
        "observation_sources": sources,
        "condition_mode": condition_mode,
        "condition_data": _clone(condition_data),
        "content_mode": content_mode,
        "stages": normalized_stages,
    }
    return {
        "id": _identifier(raw.get("id") or raw.get("uid") or f"entry_{index + 1}", "entry"),
        "entry_id": extension_copy["brainengine"]["entry_id"],
        "name": name or f"Entry {index + 1}",
        "content": content,
        "keys": _strings(raw.get("keys", raw.get("key", []))),
        "secondary_keys": _strings(raw.get("secondary_keys", raw.get("keysecondary", []))),
        "constant": constant,
        "selective": bool(raw.get("selective", False)),
        "selective_logic": int(raw.get("selective_logic", raw.get("selectiveLogic", 0)) or 0),
        "enabled": bool(raw.get("enabled", not raw.get("disable", False))),
        "order": int(raw.get("order", raw.get("insertion_order", 0)) or 0),
        "scan_depth": _nullable_int(raw.get("scan_depth", raw.get("scanDepth")), 1, 1000),
        "case_sensitive": _nullable_bool(raw.get("case_sensitive", raw.get("caseSensitive"))),
        "match_whole_words": _nullable_bool(raw.get("match_whole_words", raw.get("matchWholeWords"))),
        "use_probability": bool(raw.get("use_probability", raw.get("useProbability", False))),
        "probability": probability,
        "exclude_recursion": bool(raw.get("exclude_recursion", raw.get("excludeRecursion", False))),
        "prevent_recursion": bool(raw.get("prevent_recursion", raw.get("preventRecursion", False))),
        "sticky": _nullable_int(raw.get("sticky"), 0, 1000),
        "cooldown": _nullable_int(raw.get("cooldown"), 0, 1000),
        "delay": _nullable_int(raw.get("delay"), 0, 1000),
        "group": str(raw.get("group") or "")[:100],
        "group_override": bool(raw.get("group_override", raw.get("groupOverride", False))),
        "group_weight": int(raw.get("group_weight", raw.get("groupWeight", 100)) or 100),
        "observation_mode": observation_mode,
        "observation_sources": sources,
        "condition_mode": condition_mode,
        "condition_data": condition_data,
        "content_mode": content_mode,
        "stages": extension_copy["brainengine"]["stages"],
        "extensions": extension_copy,
    }


def _source_entry_location(data):
    candidates = [(data, "entries")]
    character_book = data.get("character_book")
    if isinstance(character_book, dict):
        candidates.append((character_book, "entries"))
    nested = data.get("data")
    if isinstance(nested, dict):
        candidates.append((nested, "entries"))
        character_book = nested.get("character_book")
        if isinstance(character_book, dict):
            candidates.append((character_book, "entries"))
    for owner, key in candidates:
        entries = owner.get(key)
        if isinstance(entries, dict):
            return owner, key, entries
        if isinstance(entries, list):
            return owner, key, entries
    raise ValueError("World Info JSON has no entries")


def _source_entries(data):
    _, _, entries = _source_entry_location(data)
    return list(entries.values()) if isinstance(entries, dict) else entries


def import_sillytavern(data, fallback_name="Imported Lorebook", book_id=None, strict=True):
    """Convert SillyTavern World Info, Character Book, or our JSON to the internal model."""
    if not isinstance(data, dict):
        raise ValueError("World Info JSON must be an object")
    source_entries = _source_entries(data)
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    direct_character_book = data.get("character_book")
    direct_character_book = (
        direct_character_book
        if isinstance(direct_character_book, dict) else {}
    )
    character_book = nested.get("character_book")
    character_book = character_book if isinstance(character_book, dict) else {}
    book_name = str(
        data.get("name") or data.get("title")
        or direct_character_book.get("name")
        or nested.get("name") or nested.get("title")
        or character_book.get("name")
        or fallback_name
    ).strip()[:100]
    converted = []
    for index, source in enumerate(source_entries):
        source = source if isinstance(source, dict) else {}
        ext = source.get("extensions") if isinstance(source.get("extensions"), dict) else {}
        candidate = {
            "id": source.get("uid", source.get("id", f"entry_{index + 1}")),
            "name": source.get("comment") or source.get("name") or f"Entry {index + 1}",
            "content": source.get("content"),
            "keys": source.get("key", source.get("keys", [])),
            "secondary_keys": source.get("keysecondary", source.get("secondary_keys", [])),
            "constant": source.get("constant", False),
            "selective": source.get("selective", False),
            "selective_logic": source.get("selectiveLogic", ext.get("selectiveLogic", 0)),
            "enabled": source.get("enabled", not source.get("disable", False)),
            "order": source.get("order", source.get("insertion_order", 0)),
            "scan_depth": source.get("scanDepth", ext.get("scan_depth")),
            "case_sensitive": source.get("caseSensitive", ext.get("case_sensitive")),
            "match_whole_words": source.get("matchWholeWords", ext.get("match_whole_words")),
            "use_probability": source.get("useProbability", ext.get("useProbability", False)),
            "probability": source.get("probability", ext.get("probability", 100)),
            "exclude_recursion": source.get("excludeRecursion", ext.get("exclude_recursion", False)),
            "prevent_recursion": source.get("preventRecursion", ext.get("prevent_recursion", False)),
            "sticky": source.get("sticky", ext.get("sticky")),
            "cooldown": source.get("cooldown", ext.get("cooldown")),
            "delay": source.get("delay", ext.get("delay")),
            "group": source.get("group", ext.get("group", "")),
            "group_override": source.get("groupOverride", ext.get("group_override", False)),
            "group_weight": source.get("groupWeight", ext.get("group_weight", 100)),
            "extensions": ext,
        }
        candidate.update({
            key: _clone(source[key])
            for key in (
                "entry_id", "observation_mode", "observation_sources",
                "condition_mode", "condition_data", "content_mode", "stages",
            )
            if key in source
        })
        try:
            converted.append(validate_entry(candidate, index))
        except ValueError as exc:
            if strict:
                raise
            fallback = _clone(candidate)
            fallback_extensions = _clone(ext)
            fallback_brain = _clone(_brainengine_fields(fallback))
            original_mode = str(fallback_brain.get("condition_mode", "normal"))
            fallback_brain["condition_mode"] = "normal"
            fallback_extensions["brainengine"] = fallback_brain
            fallback["extensions"] = fallback_extensions
            normalized = validate_entry(fallback, index)
            normalized["condition_mode"] = original_mode
            normalized["condition_data"] = _clone(
                _brainengine_fields(candidate).get("condition_data", {}))
            normalized["_configuration_error"] = str(exc)
            converted.append(normalized)
    entry_ids = [item["id"] for item in converted]
    if len(entry_ids) != len(set(entry_ids)):
        raise ValueError(f"entry ids in {book_name} must be unique")
    return {
        "id": str(book_id or _identifier(None, "book")),
        "name": book_name or fallback_name,
        "entries": converted,
    }


def _safe_book_filename(value):
    stem = os.path.splitext(os.path.basename(str(value or "")))[0]
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._")[:100] or "Lorebook"
    if stem.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *{f"COM{number}" for number in range(1, 10)},
        *{f"LPT{number}" for number in range(1, 10)},
    }:
        stem = "_" + stem
    return stem + ".json"


def _valid_book_ref(value):
    value = str(value or "").strip()
    if not value or value != os.path.basename(value) or not value.lower().endswith(".json"):
        return None
    if len(value) > 160 or any(char in value for char in '<>:"/\\|?*'):
        return None
    return value


def _book_path(filename):
    filename = _valid_book_ref(filename)
    if not filename:
        raise ValueError("invalid lorebook filename")
    path = os.path.abspath(os.path.join(BOOKS_DIR, filename))
    if os.path.dirname(path) != os.path.abspath(BOOKS_DIR):
        raise ValueError("invalid lorebook filename")
    return path


def _ensure_directories():
    os.makedirs(BOOKS_DIR, exist_ok=True)


def _write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def _unique_book_filename(preferred, existing=None):
    existing = {name.casefold() for name in (existing or [])}
    candidate = _safe_book_filename(preferred)
    if candidate.casefold() not in existing:
        return candidate
    stem, extension = os.path.splitext(candidate)
    number = 2
    while f"{stem} ({number}){extension}".casefold() in existing:
        number += 1
    return f"{stem} ({number}){extension}"


def _load_book_file(filename):
    path = _book_path(filename)
    if os.path.getsize(path) > MAX_BOOK_BYTES:
        raise ValueError("file is larger than 8 MB")
    with open(path, "r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    book = import_sillytavern(
        data, os.path.splitext(filename)[0], book_id=filename, strict=False)
    book["file"] = filename
    return book


def discover_books():
    if not os.path.isdir(BOOKS_DIR):
        return [], []
    books, errors = [], []
    seen = set()
    for filename in sorted(os.listdir(BOOKS_DIR), key=str.casefold):
        if not filename.lower().endswith(".json"):
            continue
        folded = filename.casefold()
        if folded in seen:
            errors.append({"file": filename, "error": "duplicate filename"})
            continue
        seen.add(folded)
        try:
            books.append(_load_book_file(filename))
        except Exception as exc:
            errors.append({"file": filename, "error": str(exc)})
    return books, errors


def _validate_global_settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    settings = {
        "scan_depth": int(raw.get("scan_depth", 2)),
        "case_sensitive": bool(raw.get("case_sensitive", False)),
        "match_whole_words": bool(raw.get("match_whole_words", False)),
        "recursive": bool(raw.get("recursive", False)),
        "max_recursion_steps": max(0, min(100, int(raw.get("max_recursion_steps", 0)))),
        "token_budget": max(0, min(131072, int(raw.get("token_budget", 2048)))),
    }
    if not 1 <= settings["scan_depth"] <= 1000:
        raise ValueError("default scan depth must be between 1 and 1000")
    return settings


def _book_refs(raw):
    if not isinstance(raw, list):
        return []
    result = []
    for value in raw:
        filename = _valid_book_ref(value)
        if filename and filename.casefold() not in {item.casefold() for item in result}:
            result.append(filename)
    return result


def _target_record(agent_id, raw):
    raw = raw if isinstance(raw, dict) else {"books": raw}
    step = raw.get("target_position")
    try:
        step = int(step) if step not in (None, "", "writer") else None
    except (TypeError, ValueError):
        step = None
    return {
        "target_name": str(raw.get("target_name") or agent_id).strip()[:100] or str(agent_id),
        "target_position": step,
        "books": _book_refs(raw.get("books")),
    }


def validate_settings_document(raw):
    raw = raw if isinstance(raw, dict) else {}
    profiles = {}
    for preset_id, source in (raw.get("profiles") or {}).items():
        preset_id = str(preset_id or "").strip()
        if not preset_id or len(preset_id) > 80 or not isinstance(source, dict):
            continue
        assignments = {}
        for agent_id, target in (source.get("assignments") or {}).items():
            agent_id = str(agent_id or "").strip()
            if not agent_id or agent_id == "summary":
                continue
            record = _target_record(agent_id, target)
            if record["books"]:
                assignments[agent_id] = record
        profiles[preset_id] = {
            "last_known_name": str(
                source.get("last_known_name") or preset_id).strip()[:100] or preset_id,
            "character_context_search_enabled": bool(
                source.get("character_context_search_enabled", True)),
            "previous_step_output_search_enabled": bool(
                source.get("previous_step_output_search_enabled", True)),
            "assignments": assignments,
        }
    return {
        "version": 2,
        "settings": _validate_global_settings(raw.get("settings")),
        "profiles": profiles,
    }


def _load_settings_document():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
            return validate_settings_document(json.load(handle))
    except FileNotFoundError:
        return validate_settings_document({})
    except Exception as exc:
        raise SettingsDocumentError(
            f"Could not read Lorebooks/settings.json: {exc}") from exc


def _prompt_context(prompt_config):
    if isinstance(prompt_config, dict):
        preset_id = str(prompt_config.get("preset_id") or "preset_builtin_default")
        preset_name = str(prompt_config.get("preset_name") or "Default")
        agents = [
            {"id": str(item["id"]), "name": str(item.get("name") or item["id"]),
             "step": item.get("step")}
            for item in prompt_config.get("steps", [])
        ]
        writer = prompt_config.get("writer") or {}
        if writer:
            agents.append({
                "id": "writer", "name": str(writer.get("name") or "Writer"),
                "step": "writer",
            })
        return preset_id, preset_name, agents
    agent_ids = [str(value) for value in (prompt_config or []) if str(value) != "summary"]
    agents = [
        {"id": agent_id, "name": agent_id, "step": "writer" if agent_id == "writer" else None}
        for agent_id in agent_ids
    ]
    return "preset_builtin_default", "Default", agents


def load_config(prompt_config=None):
    books, book_errors = discover_books()
    settings_error = None
    try:
        document = _load_settings_document()
    except SettingsDocumentError as exc:
        settings_error = str(exc)
        document = validate_settings_document({})
    preset_id, preset_name, agents = _prompt_context(prompt_config)
    profile = document["profiles"].get(preset_id, {
        "last_known_name": preset_name,
        "character_context_search_enabled": True,
        "previous_step_output_search_enabled": True,
        "assignments": {},
    })
    current_ids = {agent["id"] for agent in agents}
    assignments = {}
    missing_targets = []
    all_references = []
    for agent_id, target in profile.get("assignments", {}).items():
        refs = list(target.get("books", []))
        all_references.extend(refs)
        if agent_id in current_ids:
            if refs:
                assignments[agent_id] = refs
        else:
            missing_targets.append({
                "target_id": agent_id,
                "target_name": target.get("target_name") or agent_id,
                "target_position": target.get("target_position"),
                "books": refs,
            })
    known = {book["id"].casefold() for book in books}
    missing_files = [
        filename for filename in dict.fromkeys(all_references)
        if filename.casefold() not in known
    ]
    return {
        "version": 2,
        "preset_id": preset_id,
        "preset_name": preset_name,
        "character_context_search_enabled": profile.get(
            "character_context_search_enabled", True),
        "previous_step_output_search_enabled": profile.get(
            "previous_step_output_search_enabled", True),
        "settings": _clone(document["settings"]),
        "books": books,
        "assignments": assignments,
        "missing_files": missing_files,
        "missing_targets": missing_targets,
        "book_errors": book_errors,
        "settings_error": settings_error,
    }


def profile_exists(preset_id):
    document = _load_settings_document()
    return str(preset_id or "") in document["profiles"]


def ensure_profile(preset_id, preset_name, copy_from=None):
    preset_id = str(preset_id or "").strip()
    if not preset_id:
        raise ValueError("preset id is required")
    with _lock:
        document = _load_settings_document()
        if preset_id not in document["profiles"]:
            source = document["profiles"].get(str(copy_from or ""))
            document["profiles"][preset_id] = (
                _clone(source) if source else {
                    "last_known_name": str(preset_name or preset_id),
                    "character_context_search_enabled": True,
                    "previous_step_output_search_enabled": True,
                    "assignments": {},
                }
            )
        document["profiles"][preset_id]["last_known_name"] = str(
            preset_name or preset_id).strip()[:100] or preset_id
        _write_json(SETTINGS_FILE, validate_settings_document(document))


def rename_profile(preset_id, preset_name):
    ensure_profile(preset_id, preset_name)


def move_assignment_target(preset_id, source_id, destination, prompt_config):
    preset_id = str(preset_id or "").strip()
    source_id = str(source_id or "").strip()
    destination = str(destination or "").strip()
    _, preset_name, agents = _prompt_context(prompt_config)
    agent = next((item for item in agents if item["id"] == destination), None)
    if not preset_id or not source_id or agent is None:
        raise ValueError("select a valid missing target and destination")
    with _lock:
        document = _load_settings_document()
        profile = document["profiles"].get(preset_id)
        if not profile or source_id not in profile["assignments"]:
            raise ValueError("missing assignment target was not found")
        source = profile["assignments"].pop(source_id)
        target = profile["assignments"].setdefault(destination, {
            "target_name": agent["name"],
            "target_position": agent["step"] if isinstance(agent.get("step"), int) else None,
            "books": [],
        })
        for filename in source["books"]:
            if filename.casefold() not in {item.casefold() for item in target["books"]}:
                target["books"].append(filename)
        target["target_name"] = agent["name"]
        target["target_position"] = (
            agent["step"] if isinstance(agent.get("step"), int) else None)
        profile["last_known_name"] = preset_name
        _write_json(SETTINGS_FILE, validate_settings_document(document))


def remove_assignment_target(preset_id, target_id):
    with _lock:
        document = _load_settings_document()
        profile = document["profiles"].get(str(preset_id or ""))
        if profile:
            profile["assignments"].pop(str(target_id or ""), None)
            _write_json(SETTINGS_FILE, validate_settings_document(document))


def remove_missing_file_reference(preset_id, filename):
    filename = _valid_book_ref(filename)
    if not filename:
        raise ValueError("select a valid missing lorebook file")
    with _lock:
        document = _load_settings_document()
        profile = document["profiles"].get(str(preset_id or ""))
        if profile:
            for target_id, target in list(profile["assignments"].items()):
                target["books"] = [
                    item for item in target["books"]
                    if item.casefold() != filename.casefold()
                ]
                if not target["books"]:
                    profile["assignments"].pop(target_id, None)
            _write_json(SETTINGS_FILE, validate_settings_document(document))


def _serialize_book(book, filename):
    normalized = import_sillytavern(
        book, os.path.splitext(filename)[0], book_id=filename)
    return {
        "name": normalized["name"],
        "entries": normalized["entries"],
    }


def _set_native(entry, internal_key, st_key, value, st_style):
    if internal_key in entry:
        entry[internal_key] = _clone(value)
    elif st_key in entry:
        entry[st_key] = _clone(value)
    else:
        entry[st_key if st_style else internal_key] = _clone(value)


def _merge_native_entry(source, desired, st_style):
    merged = _clone(source) if isinstance(source, dict) else {}
    _set_native(merged, "name", "comment", desired["name"], st_style)
    _set_native(merged, "content", "content", desired["content"], st_style)
    _set_native(merged, "keys", "key", desired["keys"], st_style)
    _set_native(
        merged, "secondary_keys", "keysecondary",
        desired["secondary_keys"], st_style)
    for internal_key, st_key in (
        ("constant", "constant"),
        ("selective", "selective"),
        ("selective_logic", "selectiveLogic"),
        ("order", "order"),
        ("scan_depth", "scanDepth"),
        ("case_sensitive", "caseSensitive"),
        ("match_whole_words", "matchWholeWords"),
        ("use_probability", "useProbability"),
        ("probability", "probability"),
        ("exclude_recursion", "excludeRecursion"),
        ("prevent_recursion", "preventRecursion"),
        ("sticky", "sticky"),
        ("cooldown", "cooldown"),
        ("delay", "delay"),
        ("group", "group"),
        ("group_override", "groupOverride"),
        ("group_weight", "groupWeight"),
    ):
        _set_native(
            merged, internal_key, st_key, desired[internal_key], st_style)
    if "disable" in merged:
        merged["disable"] = not desired["enabled"]
    else:
        _set_native(merged, "enabled", "enabled", desired["enabled"], st_style)
    if "uid" not in merged and "id" not in merged:
        merged["uid" if st_style else "id"] = desired["id"]
    extensions = merged.get("extensions")
    extensions = _clone(extensions) if isinstance(extensions, dict) else {}
    desired_extensions = desired.get("extensions") or {}
    if isinstance(desired_extensions.get("brainengine"), dict):
        extensions["brainengine"] = _clone(desired_extensions["brainengine"])
    merged["extensions"] = extensions
    return merged


def _set_book_name(source, name):
    if "name" in source:
        source["name"] = name
        return
    if "title" in source:
        source["title"] = name
        return
    direct = source.get("character_book")
    if isinstance(direct, dict):
        direct["name"] = name
        return
    nested = source.get("data")
    nested = nested if isinstance(nested, dict) else None
    character_book = nested.get("character_book") if nested else None
    if isinstance(character_book, dict):
        character_book["name"] = name
        return
    source["name"] = name


def _merge_book_source(source, book, filename):
    """Apply editable fields while preserving unknown source JSON fields."""
    merged = _clone(source)
    owner, key, container = _source_entry_location(merged)
    raw_entries = (
        list(container.values()) if isinstance(container, dict) else list(container)
    )
    source_normalized = import_sillytavern(
        source, os.path.splitext(filename)[0], book_id=filename, strict=False)
    desired = import_sillytavern(
        book, os.path.splitext(filename)[0], book_id=filename)
    raw_by_id = {
        normalized["id"]: raw
        for normalized, raw in zip(source_normalized["entries"], raw_entries)
    }
    key_by_id = {}
    if isinstance(container, dict):
        key_by_id = {
            normalized["id"]: raw_key
            for normalized, raw_key in zip(
                source_normalized["entries"], container.keys())
        }
    st_style = any(
        isinstance(raw, dict)
        and any(field in raw for field in (
            "uid", "key", "keysecondary", "selectiveLogic", "disable"))
        for raw in raw_entries
    )
    rebuilt = []
    rebuilt_keys = []
    used_keys = set()
    for entry in desired["entries"]:
        source_entry = raw_by_id.get(entry["id"])
        rebuilt.append(_merge_native_entry(source_entry, entry, st_style))
        raw_key = str(key_by_id.get(entry["id"], entry["id"]))
        candidate = raw_key
        suffix = 2
        while candidate in used_keys:
            candidate = f"{raw_key}_{suffix}"
            suffix += 1
        used_keys.add(candidate)
        rebuilt_keys.append(candidate)
    if isinstance(container, dict):
        owner[key] = dict(zip(rebuilt_keys, rebuilt))
    else:
        owner[key] = rebuilt
    _set_book_name(merged, desired["name"])
    return merged


def _book_semantics(data, filename):
    normalized = import_sillytavern(
        data, os.path.splitext(filename)[0], book_id=filename)
    return {"name": normalized["name"], "entries": normalized["entries"]}


def _validate_draft_preset(raw, preset_id):
    draft_preset_id = str(raw.get("preset_id") or "").strip()
    if draft_preset_id and draft_preset_id != preset_id:
        raise ValueError(
            "The active prompt preset changed. Reload Lorebooks before saving.")


def _save_books_locked(raw, document):
    document["settings"] = _validate_global_settings(raw.get("settings"))
    existing = [name for name in os.listdir(BOOKS_DIR) if name.lower().endswith(".json")]
    remapped = {}
    for raw_book in raw.get("books") or []:
        if not isinstance(raw_book, dict):
            raise ValueError("each lorebook must be an object")
        original_id = str(raw_book.get("id") or "")
        supplied = _valid_book_ref(raw_book.get("file") or original_id)
        filename = supplied or _unique_book_filename(raw_book.get("name"), existing)
        path = _book_path(filename)
        if supplied and os.path.isfile(path):
            if os.path.getsize(path) > MAX_BOOK_BYTES:
                raise ValueError(f"{filename} is larger than 8 MB")
            with open(path, "r", encoding="utf-8-sig") as handle:
                source = json.load(handle)
            desired = _book_semantics(raw_book, filename)
            if _book_semantics(source, filename) != desired:
                _write_json(path, _merge_book_source(source, raw_book, filename))
        else:
            _write_json(path, _serialize_book(raw_book, filename))
        existing.append(filename)
        if original_id:
            remapped[original_id] = filename
    return remapped


def _save_assignments_locked(raw, document, preset_id, preset_name, agents, remapped=None):
    remapped = remapped or {}
    profile = document["profiles"].setdefault(preset_id, {
        "last_known_name": preset_name,
        "character_context_search_enabled": True,
        "previous_step_output_search_enabled": True,
        "assignments": {},
    })
    profile["last_known_name"] = preset_name
    profile["character_context_search_enabled"] = bool(
        raw.get("character_context_search_enabled", True))
    profile["previous_step_output_search_enabled"] = bool(
        raw.get("previous_step_output_search_enabled", True))
    targets = profile.setdefault("assignments", {})
    selections = raw.get("assignments") if isinstance(raw.get("assignments"), dict) else {}
    for agent in agents:
        agent_id = agent["id"]
        refs = []
        for value in selections.get(agent_id, []) or []:
            raw_value = str(value)
            filename = remapped.get(raw_value) or _valid_book_ref(raw_value)
            if not filename:
                raise ValueError(
                    "Save new lorebooks before saving their assignments.")
            if filename.casefold() not in {item.casefold() for item in refs}:
                refs.append(filename)
        if refs:
            targets[agent_id] = {
                "target_name": agent["name"],
                "target_position": (
                    agent["step"] if isinstance(agent.get("step"), int) else None),
                "books": refs,
            }
        else:
            targets.pop(agent_id, None)
    assigned_books = {
        filename for target in targets.values() for filename in target.get("books", [])
    }
    identities = {}
    for book in raw.get("books", []) or []:
        book_id = remapped.get(str(book.get("id"))) or str(book.get("id") or "")
        if book_id not in assigned_books:
            continue
        for entry in book.get("entries", []) or []:
            identity = (str(entry.get("name") or ""), str(entry.get("entry_id") or ""))
            if not all(identity):
                continue
            identities.setdefault(identity, set()).add(book_id)
    collisions = [identity for identity, owners in identities.items() if len(owners) > 1]
    if collisions:
        name, entry_id = collisions[0]
        raise ValueError(
            f"Duplicate lore entry identity across assigned books: {name}@{entry_id}")


def save_books(raw, prompt_config=None):
    """Persist lorebook files and global matching settings, not assignments."""
    raw = raw if isinstance(raw, dict) else {}
    preset_id, _, _ = _prompt_context(prompt_config)
    _validate_draft_preset(raw, preset_id)
    with _lock:
        _ensure_directories()
        document = _load_settings_document()
        remapped = _save_books_locked(raw, document)
        _write_json(SETTINGS_FILE, validate_settings_document(document))
    result = load_config(prompt_config)
    result["book_id_map"] = remapped
    return result


def save_assignments(raw, prompt_config=None):
    """Persist only the active preset's agent-to-lorebook assignments."""
    raw = raw if isinstance(raw, dict) else {}
    preset_id, preset_name, agents = _prompt_context(prompt_config)
    _validate_draft_preset(raw, preset_id)
    with _lock:
        _ensure_directories()
        document = _load_settings_document()
        _save_assignments_locked(raw, document, preset_id, preset_name, agents)
        _write_json(SETTINGS_FILE, validate_settings_document(document))
    return load_config(prompt_config)


def save_config(raw, prompt_config=None):
    """Persist books, global settings, and assignments for API compatibility."""
    raw = raw if isinstance(raw, dict) else {}
    preset_id, preset_name, agents = _prompt_context(prompt_config)
    _validate_draft_preset(raw, preset_id)
    with _lock:
        _ensure_directories()
        document = _load_settings_document()
        remapped = _save_books_locked(raw, document)
        _save_assignments_locked(
            raw, document, preset_id, preset_name, agents, remapped=remapped)
        _write_json(SETTINGS_FILE, validate_settings_document(document))
    return load_config(prompt_config)


def _prepare_import_ids(data, preserve_ids=False):
    prepared = _clone(data)
    _owner, _key, entries = _source_entry_location(prepared)
    raw_entries = list(entries.values()) if isinstance(entries, dict) else entries

    def refresh_nodes(nodes):
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            if not preserve_ids or not node.get("node_id"):
                node["node_id"] = f"node_{uuid.uuid4().hex[:12]}"
            refresh_nodes(node.get("children"))

    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        extensions = raw.get("extensions")
        extensions = _clone(extensions) if isinstance(extensions, dict) else {}
        brain = extensions.get("brainengine")
        brain = _clone(brain) if isinstance(brain, dict) else {}
        existing_entry_id = raw.get("entry_id") or brain.get("entry_id")
        if not preserve_ids or not existing_entry_id:
            existing_entry_id = f"entry_{uuid.uuid4().hex[:12]}"
        raw["entry_id"] = existing_entry_id
        brain["entry_id"] = existing_entry_id
        condition_data = raw.get("condition_data")
        if not isinstance(condition_data, dict):
            condition_data = brain.get("condition_data")
        if isinstance(condition_data, dict):
            refresh_nodes(condition_data.get("nodes"))
            raw["condition_data"] = condition_data
            brain["condition_data"] = _clone(condition_data)
        extensions["brainengine"] = brain
        raw["extensions"] = extensions
    return prepared


def import_book_file(data, filename_hint="Imported Lorebook", mode="new"):
    """Store one imported JSON file immediately and return its normalized view."""
    if mode not in {"new", "restore"}:
        raise ValueError("import mode must be new or restore")
    data = _prepare_import_ids(data, preserve_ids=mode == "restore")
    import_sillytavern(data, filename_hint)
    with _lock:
        _ensure_directories()
        existing = [name for name in os.listdir(BOOKS_DIR) if name.lower().endswith(".json")]
        filename = _unique_book_filename(filename_hint, existing)
        _write_json(_book_path(filename), data)
    return _load_book_file(filename)


def delete_book_file(filename):
    """Delete one book file. Assignment references intentionally remain as Missing."""
    path = _book_path(filename)
    with _lock:
        if os.path.isfile(path):
            os.remove(path)


def _regex_key(key):
    match = re.fullmatch(r"/(.*)/([a-zA-Z]*)", key, flags=re.DOTALL)
    if not match:
        return None
    flags = re.IGNORECASE if "i" in match.group(2) else 0
    try:
        return re.compile(match.group(1), flags)
    except re.error:
        return None


def _matches(text, key, case_sensitive, whole_words):
    regex = _regex_key(key)
    if regex is not None:
        return bool(regex.search(text))
    haystack, needle = (text, key) if case_sensitive else (text.casefold(), key.casefold())
    if not whole_words:
        return needle in haystack
    if any(char.isspace() for char in needle):
        return needle in haystack
    return bool(re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack))


def _match_span(text, key, start, case_sensitive, whole_words):
    """Return the next literal match span; ordered mode intentionally has no regex."""
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.escape(str(key))
    if whole_words and not any(char.isspace() for char in str(key)):
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    match = re.compile(pattern, flags).search(str(text), max(0, int(start)))
    return match.span() if match else None


def ordered_match_blocks(blocks, words, case_sensitive=False, whole_words=False):
    """Forward-scan complete blocks without allowing one word to cross a boundary."""
    block_index, offset, positions = 0, 0, []
    blocks = [str(block) for block in blocks if isinstance(block, str)]
    for word in words:
        found = None
        while block_index < len(blocks):
            span = _match_span(
                blocks[block_index], word, offset, case_sensitive, whole_words)
            if span is not None:
                found = (block_index, span[0], span[1])
                offset = span[1]
                break
            block_index += 1
            offset = 0
        if found is None:
            return None
        positions.append(found)
    return positions


def _entry_matches(entry, text, settings):
    if entry["constant"]:
        return True
    keys = entry["keys"]
    if not keys:
        return False
    case_sensitive = entry["case_sensitive"] if entry["case_sensitive"] is not None else settings["case_sensitive"]
    whole_words = entry["match_whole_words"] if entry["match_whole_words"] is not None else settings["match_whole_words"]
    primary = any(_matches(text, key, case_sensitive, whole_words) for key in keys)
    if not primary:
        return False
    if not entry["selective"] or not entry["secondary_keys"]:
        return True
    secondary = [_matches(text, key, case_sensitive, whole_words) for key in entry["secondary_keys"]]
    logic = entry["selective_logic"]
    return {0: any(secondary), 1: not all(secondary), 2: not any(secondary), 3: all(secondary)}.get(logic, any(secondary))


def _chat_text(messages, depth):
    visible = []
    for message in reversed(messages if isinstance(messages, list) else []):
        if message.get("role") not in ("user", "assistant"):
            continue
        content = message.get("content")
        if isinstance(content, str):
            visible.append(content)
        if len(visible) >= depth:
            break
    return "\n".join(reversed(visible))


def chat_blocks(messages, depth):
    visible = []
    for message in reversed(messages if isinstance(messages, list) else []):
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            continue
        content = message.get("content")
        if isinstance(content, str):
            visible.append(content)
        if len(visible) >= depth:
            break
    return list(reversed(visible))


def _entry_sources(entry, sources):
    if entry.get("observation_mode") == "past_same_step_only":
        return {"past_same_step": list(sources.get("past_same_step", []))}
    selected = entry.get("observation_sources") or DEFAULT_OBSERVATION_SOURCES
    return {name: list(sources.get(name, [])) for name in selected if sources.get(name)}


def _content_for_count(content_mode, content, stages, count):
    if content_mode != "staged":
        return str(content or "")
    choices = stages or [content]
    return str(choices[min(max(0, int(count or 0)), len(choices) - 1)] or "")


def _hierarchical_package(entry, selected_sources, settings, lore_state):
    combined = "\n".join(
        block for blocks in selected_sources.values() for block in blocks)
    case_sensitive = (
        entry["case_sensitive"] if entry["case_sensitive"] is not None
        else settings["case_sensitive"])
    whole_words = (
        entry["match_whole_words"] if entry["match_whole_words"] is not None
        else settings["match_whole_words"])
    included, included_ids = [], set()

    def visit(node, ancestors, ancestors_match):
        matched = ancestors_match and bool(node["words"]) and all(
            _matches(combined, word, case_sensitive, whole_words)
            for word in node["words"])
        if matched:
            for candidate in [*ancestors, node]:
                if candidate["node_id"] in included_ids:
                    continue
                included_ids.add(candidate["node_id"])
                included.append(candidate)
        for child in node.get("children", []):
            visit(child, [*ancestors, node], matched)

    for root in entry.get("condition_data", {}).get("nodes", []):
        visit(root, [], True)
    if not included:
        return None
    entry_id = entry["entry_id"]
    parts, state_keys, state_labels = [], [], {}
    for node in included:
        state_key = f"{entry_id}/{node['node_id']}"
        text = _content_for_count(
            entry["content_mode"], node.get("content", ""), node.get("stages"),
            lore_state.get(state_key, 0))
        if text.strip():
            tag = safe_tag_name(node["name"])
            parts.append(f"<{tag}>\n{text}\n</{tag}>")
            if entry["content_mode"] == "staged":
                state_keys.append(state_key)
                state_labels[state_key] = (entry["name"], node["name"])
    if not parts:
        return None
    result = _clone(entry)
    result["content"] = "\n".join(parts)
    result["_state_keys"] = state_keys
    result["_state_labels"] = state_labels
    result["_atomic"] = True
    result["_matched_nodes"] = [node["node_id"] for node in included]
    return result


def state_catalog(books):
    """Return all persisted staged-state identities, including inactive entries."""
    labels = {}

    def add_nodes(entry, nodes):
        for node in nodes:
            labels[f"{entry['entry_id']}/{node['node_id']}"] = (
                entry["name"], node["name"])
            add_nodes(entry, node.get("children", []))

    for book in books or []:
        for entry in book.get("entries", []):
            if entry.get("content_mode") != "staged":
                continue
            if entry.get("condition_mode") == "hierarchical":
                add_nodes(entry, entry.get("condition_data", {}).get("nodes", []))
            else:
                labels[entry["entry_id"]] = (entry["name"], None)
    return labels


def _entry_candidate(entry, sources, settings, lore_state, recursion_text=""):
    if entry.get("_configuration_error"):
        return None
    selected_sources = _entry_sources(entry, sources)
    mode = entry.get("condition_mode", "normal")
    if mode == "hierarchical":
        return _hierarchical_package(entry, selected_sources, settings, lore_state)
    case_sensitive = (
        entry["case_sensitive"] if entry["case_sensitive"] is not None
        else settings["case_sensitive"])
    whole_words = (
        entry["match_whole_words"] if entry["match_whole_words"] is not None
        else settings["match_whole_words"])
    diagnostics = {}
    if mode == "ordered":
        words = entry.get("condition_data", {}).get("words", [])
        positions = None
        source_name = None
        for name, blocks in selected_sources.items():
            positions = ordered_match_blocks(blocks, words, case_sensitive, whole_words)
            if positions is not None:
                source_name = name
                break
        if positions is None:
            return None
        diagnostics = {"source": source_name, "positions": positions}
    else:
        text = "\n".join(
            block for blocks in selected_sources.values() for block in blocks)
        if recursion_text:
            text += "\n" + recursion_text
        if not _entry_matches(entry, text, settings):
            return None
    result = _clone(entry)
    state_key = entry["entry_id"]
    result["content"] = _content_for_count(
        entry["content_mode"], entry["content"], entry.get("stages"),
        lore_state.get(state_key, 0))
    result["_state_keys"] = [state_key] if entry["content_mode"] == "staged" else []
    result["_state_labels"] = (
        {state_key: (entry["name"], None)}
        if entry["content_mode"] == "staged" else {})
    result["_atomic"] = mode in {"ordered", "hierarchical"}
    result["_match"] = diagnostics
    return result


def select_entry_groups(entries, rng=None):
    rng = rng or random
    grouped, result = {}, []
    for entry in entries:
        (grouped.setdefault(entry["group"], []).append(entry)
         if entry["group"] else result.append(entry))
    for candidates in grouped.values():
        overrides = [item for item in candidates if item["group_override"]]
        pool = overrides or candidates
        weights = [max(0, item["group_weight"]) for item in pool]
        result.append(rng.choices(
            pool, weights=weights if any(weights) else None, k=1)[0])
    return sorted(result, key=lambda item: item["order"], reverse=True)


def activate_book(book, messages, settings, rng=None, fixed_context="",
                  search_sources=None, lore_state=None, apply_groups=True):
    """Return activated entries in SillyTavern insertion-order precedence."""
    rng = rng or random
    fixed_context = str(fixed_context or "")
    lore_state = lore_state if isinstance(lore_state, dict) else {}
    ordered = sorted(book["entries"], key=lambda item: item["order"], reverse=True)
    active, active_ids, recursion_text = [], set(), ""
    max_steps = settings.get("max_recursion_steps", 0) or len(ordered)
    passes = 1 + (max_steps if settings.get("recursive") else 0)
    for pass_index in range(passes):
        newly_active = []
        for entry in ordered:
            if not entry["enabled"] or entry["id"] in active_ids:
                continue
            if pass_index and entry["exclude_recursion"]:
                continue
            depth = entry["scan_depth"] or settings["scan_depth"]
            if search_sources is None:
                sources = {"chat": chat_blocks(messages, depth)}
                if fixed_context:
                    for source in DEFAULT_OBSERVATION_SOURCES[1:]:
                        sources.setdefault(source, [fixed_context])
            else:
                sources = {
                    name: list(blocks) for name, blocks in search_sources.items()
                    if isinstance(blocks, list)
                }
                sources["chat"] = chat_blocks(messages, depth)
            candidate = _entry_candidate(
                entry, sources, settings, lore_state,
                recursion_text if entry.get("condition_mode") == "normal" else "")
            if candidate is None:
                continue
            if entry["use_probability"] and rng.random() * 100 >= entry["probability"]:
                continue
            newly_active.append(candidate)
            active_ids.add(entry["id"])
        if not newly_active:
            break
        active.extend(newly_active)
        recursion_text += "\n" + "\n".join(
            item["content"] for item in newly_active if not item["prevent_recursion"])
        if any(item["prevent_recursion"] for item in newly_active):
            break
    return select_entry_groups(active, rng) if apply_groups else sorted(
        active, key=lambda item: item["order"], reverse=True)


def render_lore(entries, expand=None, token_budget=0, return_metadata=False):
    parts, used, injected, labels, skipped = [], 0, [], {}, []
    character_budget = token_budget * 4 if token_budget else 0
    for entry in entries:
        content = expand(entry["content"]) if expand else entry["content"]
        if not str(content or "").strip():
            continue
        tag = safe_tag_name(entry["name"])
        block = f"<{tag}>\n{content}\n</{tag}>"
        if character_budget and used + len(block) > character_budget:
            skipped.append({"entry_id": entry.get("entry_id"), "reason": "token_budget"})
            continue
        parts.append(block)
        used += len(block)
        injected.extend(entry.get("_state_keys", []))
        labels.update(entry.get("_state_labels", {}))
    joined = "\n".join(parts)
    rendered = f"<lorebook>\n{joined}\n</lorebook>" if parts else ""
    if return_metadata:
        return {
            "text": rendered,
            "injected_state_keys": list(dict.fromkeys(injected)),
            "state_labels": labels,
            "skipped": skipped,
        }
    return rendered
