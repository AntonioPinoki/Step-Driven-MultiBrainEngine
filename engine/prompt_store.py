import csv
import io
import json
import os
import re
import threading
import time
import uuid
from step_history import step_output_tag


HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS_FILE = os.path.join(HERE, "prompts.json")
PRESET_DIR = os.path.join(os.path.dirname(HERE), "Preset")
_lock = threading.Lock()


def _clone(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


DEFAULT_STEP_TEMPERATURE = 0.3
DEFAULT_WRITER_TEMPERATURE = 0.85
DEFAULT_SUMMARY_TEMPERATURE = 0.25
DEFAULT_FREQUENCY_PENALTY = 0.0
DEFAULT_PRESENCE_PENALTY = 0.0
DEFAULT_REPETITION_PENALTY = 1.0
DEFAULT_REPETITION_PENALTY_RANGE = 0
DEFAULT_PRESET_ID = "preset_builtin_default"
DEFAULT_PRESET_NAME = "Default"


def _new_preset_id():
    return f"preset_{uuid.uuid4().hex}"


def _new_step_id():
    return f"step_{uuid.uuid4().hex[:12]}"


def _clean_preset_id(value, fallback=None):
    value = str(value or "").strip()
    if value and len(value) <= 80 and re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return fallback or _new_preset_id()


def _defaults(default_steps, default_writer, default_summary):
    clean = validate_config(
        default_steps, default_writer, default_summary, "",
        DEFAULT_PRESET_ID, DEFAULT_PRESET_NAME)
    return _apply_legacy_sampling_defaults(clean)


def _apply_legacy_sampling_defaults(clean):
    """Preserve the hard-coded sampler values used before config version 6."""
    if clean["steps"]:
        clean["steps"][-1]["presence_penalty"] = 0.4
    clean["writer"]["frequency_penalty"] = 0.3
    clean["writer"]["presence_penalty"] = 0.3
    clean["summary"]["frequency_penalty"] = 0.1
    return clean


def _migrate_v1(data, default_writer):
    old_slots = data.get("slots") if isinstance(data, dict) else None
    if not isinstance(old_slots, list):
        return None
    steps = []
    writer = _clone(default_writer)
    for raw in old_slots:
        if not isinstance(raw, dict):
            continue
        if raw.get("kind") == "writer":
            writer = {
                "id": "writer", "name": str(raw.get("name") or "Writer"),
                "prompt": str(raw.get("prompt") or default_writer["prompt"])
            }
            continue
        steps.append({
            "id": str(raw.get("id") or f"step_{len(steps) + 1}"),
            "name": str(raw.get("name") or f"Step {len(steps) + 1}"),
            "step": len(steps) + 1,
            "prompt": str(raw.get("prompt") or "Analyze the current scene."),
        })
    return {"steps": steps, "writer": writer}


def load_config(default_steps, default_writer, default_summary):
    with _lock:
        if not os.path.exists(PROMPTS_FILE):
            return _defaults(default_steps, default_writer, default_summary)
        try:
            with open(PROMPTS_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and data.get("version") in (2, 3, 4, 5, 6, 7, 8):
                clean = validate_config(
                    data.get("steps"), data.get("writer"), data.get("summary") or default_summary,
                    data.get("group_prompt") or "",
                    data.get("active_preset_id") or DEFAULT_PRESET_ID,
                    data.get("active_preset_name") or DEFAULT_PRESET_NAME)
                return clean if data.get("version") in (6, 7, 8) else _apply_legacy_sampling_defaults(clean)
            migrated = _migrate_v1(data, default_writer)
            return _apply_legacy_sampling_defaults(validate_config(
                migrated["steps"], migrated["writer"], default_summary)) if migrated else _defaults(
                    default_steps, default_writer, default_summary)
        except Exception as exc:
            print(f"⚠️ Could not load prompts.json ({exc}); using built-in prompts.")
            return _defaults(default_steps, default_writer, default_summary)


def load_available_config(
    default_steps, default_writer, default_summary, default_group_prompt="",
):
    """Load the active preset, replacing a deleted preset with an available one."""
    current = load_config(default_steps, default_writer, default_summary)
    presets = list_presets()
    if any(item.get("preset_id") == current["preset_id"] for item in presets):
        return current

    if presets:
        fallback = next(
            (item for item in presets
             if item.get("preset_id") == DEFAULT_PRESET_ID),
            None,
        )
        fallback = fallback or next(
            (item for item in presets if item.get("name", "").casefold() == "default"),
            presets[0],
        )
        return load_preset_file(
            fallback["filename"], default_writer, default_summary)

    built_in = validate_config(
        default_steps, default_writer, default_summary, default_group_prompt,
        DEFAULT_PRESET_ID, DEFAULT_PRESET_NAME)
    return save_config(**built_in)


def _temperature(raw, fallback, label):
    try:
        value = float(raw if raw is not None else fallback)
    except (TypeError, ValueError):
        raise ValueError(f"{label} has an invalid temperature")
    if value < 0 or value > 1.5:
        raise ValueError(f"{label} temperature must be between 0.00 and 1.50")
    return round(value, 2)


def _penalty(raw, fallback, label):
    try:
        value = float(raw if raw is not None else fallback)
    except (TypeError, ValueError):
        raise ValueError(f"{label} has an invalid value")
    if value < -2.0 or value > 2.0:
        raise ValueError(f"{label} must be between -2.00 and 2.00")
    return round(value, 2)


def _repetition_penalty(raw, label):
    try:
        value = float(raw if raw is not None else DEFAULT_REPETITION_PENALTY)
    except (TypeError, ValueError):
        raise ValueError(f"{label} has an invalid repetition penalty")
    if value < 0.0 or value > 2.0:
        raise ValueError(f"{label} repetition penalty must be between 0.00 and 2.00")
    return round(value, 2)


def _repetition_range(raw, label):
    try:
        value = int(raw if raw is not None else DEFAULT_REPETITION_PENALTY_RANGE)
    except (TypeError, ValueError):
        raise ValueError(f"{label} has an invalid repetition range")
    if value < 0 or value > 32768:
        raise ValueError(f"{label} repetition range must be between 0 and 32768 tokens")
    return value


def _sampling(raw, temperature_fallback, label):
    return {
        "temperature": _temperature(raw.get("temperature"), temperature_fallback, label),
        "frequency_penalty": _penalty(
            raw.get("frequency_penalty"), DEFAULT_FREQUENCY_PENALTY,
            f"{label} frequency penalty"),
        "presence_penalty": _penalty(
            raw.get("presence_penalty"), DEFAULT_PRESENCE_PENALTY,
            f"{label} presence penalty"),
        "repetition_penalty": _repetition_penalty(raw.get("repetition_penalty"), label),
        "repetition_penalty_range": _repetition_range(
            raw.get("repetition_penalty_range"), label),
    }


def validate_config(
    steps, writer, summary, group_prompt="", preset_id=None, preset_name=None,
):
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    if len(steps) > 23:
        raise ValueError("A maximum of 23 reasoning steps is supported")
    clean_steps = []
    seen_ids, seen_steps, seen_tags = set(), set(), {}
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise ValueError(f"step {index + 1} must be an object")
        slot_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        try:
            step = int(raw.get("step"))
        except (TypeError, ValueError):
            raise ValueError(f"{name or f'step {index + 1}'} has an invalid step number")
        if not slot_id or len(slot_id) > 80 or slot_id in seen_ids:
            raise ValueError(f"step {index + 1} has an invalid or duplicate id")
        if not name or len(name) > 100:
            raise ValueError(f"step {index + 1} has an invalid name")
        if not prompt or len(prompt) > 30000:
            raise ValueError(f"step {index + 1} has an invalid prompt")
        if step < 1 or step > 23 or step in seen_steps:
            raise ValueError(f"step number {step} is invalid or already occupied")
        tag = step_output_tag({"name": name, "step": step})
        if tag in seen_tags:
            raise ValueError(
                f"Step names '{seen_tags[tag]}' and '{name}' produce the same output tag <{tag}>; "
                "use unique step names")
        seen_tags[tag] = name
        seen_ids.add(slot_id)
        seen_steps.add(step)
        clean_steps.append({
            "id": slot_id, "name": name, "step": step, "prompt": prompt,
            **_sampling(raw, DEFAULT_STEP_TEMPERATURE, name),
        })
    clean_steps.sort(key=lambda item: item["step"])

    if not isinstance(writer, dict):
        raise ValueError("writer must be an object")
    writer_name = str(writer.get("name") or "Writer").strip()
    writer_prompt = str(writer.get("prompt") or "").strip()
    if not writer_name or len(writer_name) > 100 or not writer_prompt or len(writer_prompt) > 30000:
        raise ValueError("writer settings are invalid")
    clean_writer = {
        "id": "writer", "name": writer_name, "prompt": writer_prompt,
        **_sampling(writer, DEFAULT_WRITER_TEMPERATURE, "Writer"),
    }

    if not isinstance(summary, dict):
        raise ValueError("summary must be an object")
    summary_prompt = str(summary.get("prompt") or "").strip()
    if not summary_prompt or len(summary_prompt) > 30000:
        raise ValueError("summary prompt is invalid")
    clean_summary = {
        "id": "summary", "name": "Summarize", "prompt": summary_prompt,
        **_sampling(summary, DEFAULT_SUMMARY_TEMPERATURE, "Summarize"),
    }
    clean_group_prompt = str(group_prompt or "").strip()
    if len(clean_group_prompt) > 30000:
        raise ValueError("group prompt is too long")
    clean_preset_id = _clean_preset_id(preset_id, DEFAULT_PRESET_ID)
    clean_preset_name = str(preset_name or DEFAULT_PRESET_NAME).strip()[:100] or DEFAULT_PRESET_NAME
    return {
        "preset_id": clean_preset_id, "preset_name": clean_preset_name,
        "steps": clean_steps, "writer": clean_writer, "summary": clean_summary,
        "group_prompt": clean_group_prompt,
    }


def _saved_identity_unlocked():
    try:
        with open(PROMPTS_FILE, "r", encoding="utf-8") as handle:
            current = json.load(handle)
        return (
            _clean_preset_id(current.get("active_preset_id"), DEFAULT_PRESET_ID),
            str(current.get("active_preset_name") or DEFAULT_PRESET_NAME).strip()[:100],
        )
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_PRESET_ID, DEFAULT_PRESET_NAME


def save_config(
    steps, writer, summary, group_prompt="", preset_id=None, preset_name=None,
):
    temp_file = PROMPTS_FILE + ".tmp"
    with _lock:
        current_id, current_name = _saved_identity_unlocked()
        clean = validate_config(
            steps, writer, summary, group_prompt,
            preset_id or current_id, preset_name or current_name)
        payload = {
            "version": 8,
            "active_preset_id": clean["preset_id"],
            "active_preset_name": clean["preset_name"],
            "steps": clean["steps"], "writer": clean["writer"],
            "summary": clean["summary"], "group_prompt": clean["group_prompt"],
        }
        with open(temp_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(temp_file, PROMPTS_FILE)
    return _clone(clean)


def save_active_preset_config(
    steps, writer, summary, group_prompt="", preset_id=None, preset_name=None,
):
    """Save the active runtime config and overwrite its single preset CSV."""
    json_temp = PROMPTS_FILE + ".tmp"
    csv_temp = None
    with _lock:
        current_id, current_name = _saved_identity_unlocked()
        clean = validate_config(
            steps, writer, summary, group_prompt,
            preset_id or current_id, preset_name or current_name)
        matches = [
            item["filename"] for item in list_presets()
            if item.get("preset_id") == clean["preset_id"]
        ]
        if not matches:
            raise ValueError("The active preset CSV was not found")
        if len(matches) != 1:
            raise ValueError("Multiple preset CSV files use the active preset ID")

        filename = matches[0]
        path = os.path.abspath(os.path.join(PRESET_DIR, filename))
        if os.path.dirname(path) != os.path.abspath(PRESET_DIR):
            raise ValueError("invalid preset filename")
        csv_temp = path + ".tmp"
        csv_text = config_to_csv(**clean)
        payload = {
            "version": 8,
            "active_preset_id": clean["preset_id"],
            "active_preset_name": clean["preset_name"],
            "steps": clean["steps"], "writer": clean["writer"],
            "summary": clean["summary"], "group_prompt": clean["group_prompt"],
        }
        try:
            with open(csv_temp, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write(csv_text)
            with open(json_temp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(csv_temp, path)
            os.replace(json_temp, PROMPTS_FILE)
        finally:
            for temp_file in (csv_temp, json_temp):
                if temp_file and os.path.exists(temp_file):
                    os.remove(temp_file)
    return _clone(clean)


def _safe_preset_name(name):
    name = re.sub(r"[^\w\- ]", "", str(name or "").strip(), flags=re.UNICODE)
    name = re.sub(r"\s+", "_", name).strip("_-")
    return name[:80] or time.strftime("prompt_preset_%Y%m%d_%H%M%S")


def require_unused_preset_name(name):
    display_name = str(name or "").strip()
    if not display_name:
        raise ValueError("Enter a preset name")
    filename = _safe_preset_name(display_name) + ".csv"
    existing = {
        item["filename"].casefold() for item in list_presets()
    }
    if filename.casefold() in existing:
        raise ValueError("A preset with that name already exists")
    return display_name


def unique_preset_name(name):
    base = str(name or "").strip() or "Imported preset"
    existing = {
        item["filename"].casefold() for item in list_presets()
    }
    candidate = base
    number = 2
    while (_safe_preset_name(candidate) + ".csv").casefold() in existing:
        candidate = f"{base} {number}"
        number += 1
    return candidate


def distinct_preset_id(preset_id, reserved_ids=()):
    known_ids = {
        item.get("preset_id") for item in list_presets()
        if item.get("preset_id")
    }
    known_ids.update(
        str(item) for item in reserved_ids if str(item or "").strip()
    )
    clean = _clean_preset_id(preset_id)
    return _new_preset_id() if clean in known_ids else clean


def config_to_csv(
    steps, writer, summary, group_prompt="", preset_id=None, preset_name=None,
):
    clean = validate_config(
        steps, writer, summary, group_prompt,
        preset_id or _new_preset_id(), preset_name or DEFAULT_PRESET_NAME)
    headers = ["preset_id", "preset_name"]
    headers += [f"step{number}" for number in range(1, 24)] + ["writer"]
    headers += [f"step{number}_id" for number in range(1, 24)]
    headers += [f"step{number}_title" for number in range(1, 24)]
    headers += ["writer_title", "summarize_title"]
    headers += [f"step{number}_temperature" for number in range(1, 24)]
    headers += ["writer_temperature", "summarize", "summarize_temperature", "group_prompt"]
    sampling_fields = ["frequency_penalty", "presence_penalty", "repetition_penalty",
                       "repetition_penalty_range"]
    for field in sampling_fields:
        headers += [f"step{number}_{field}" for number in range(1, 24)]
        headers += [f"writer_{field}", f"summarize_{field}"]
    by_step = {item["step"]: item["prompt"] for item in clean["steps"]}
    by_title = {item["step"]: item["name"] for item in clean["steps"]}
    by_temp = {item["step"]: item["temperature"] for item in clean["steps"]}
    by_id = {item["step"]: item["id"] for item in clean["steps"]}
    row = [clean["preset_id"], clean["preset_name"]]
    row += [by_step.get(number, "") for number in range(1, 24)] + [clean["writer"]["prompt"]]
    row += [by_id.get(number, "") for number in range(1, 24)]
    row += [by_title.get(number, "") for number in range(1, 24)]
    row += [clean["writer"]["name"], clean["summary"]["name"]]
    row += [by_temp.get(number, "") for number in range(1, 24)]
    row += [clean["writer"]["temperature"], clean["summary"]["prompt"],
            clean["summary"]["temperature"], clean["group_prompt"]]
    by_number = {item["step"]: item for item in clean["steps"]}
    for field in sampling_fields:
        row += [by_number.get(number, {}).get(field, "") for number in range(1, 24)]
        row += [clean["writer"][field], clean["summary"][field]]
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows([headers, row])
    return buffer.getvalue()


def save_preset(
    name, steps, writer, summary, group_prompt="", preset_id=None, preset_name=None,
):
    display_name = str(preset_name or name or DEFAULT_PRESET_NAME).strip()[:100]
    actual_id = _clean_preset_id(preset_id)
    csv_text = config_to_csv(
        steps, writer, summary, group_prompt, actual_id, display_name)
    filename = _safe_preset_name(name) + ".csv"
    os.makedirs(PRESET_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(PRESET_DIR, filename))
    if os.path.dirname(path) != os.path.abspath(PRESET_DIR):
        raise ValueError("invalid preset filename")
    with _lock:
        temp_file = path + ".tmp"
        with open(temp_file, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(csv_text)
        os.replace(temp_file, path)
    return {
        "filename": filename, "csv": csv_text,
        "preset_id": actual_id, "preset_name": display_name,
    }


def export_preset(
    name, steps, writer, summary, group_prompt="", preset_id=None, preset_name=None,
):
    """Build a downloadable preset without writing it to the Preset folder."""
    display_name = str(preset_name or name or DEFAULT_PRESET_NAME).strip()[:100]
    actual_id = _clean_preset_id(preset_id)
    return {
        "filename": _safe_preset_name(name) + ".csv",
        "csv": config_to_csv(
            steps, writer, summary, group_prompt, actual_id, display_name),
        "preset_id": actual_id, "preset_name": display_name,
    }


def list_presets():
    if not os.path.isdir(PRESET_DIR):
        return []
    presets = []
    for filename in os.listdir(PRESET_DIR):
        if not filename.lower().endswith(".csv"):
            continue
        path = os.path.abspath(os.path.join(PRESET_DIR, filename))
        if os.path.dirname(path) != os.path.abspath(PRESET_DIR) or not os.path.isfile(path):
            continue
        preset_id = ""
        preset_name = os.path.splitext(filename)[0]
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle), {})
            preset_id = str(row.get("preset_id") or "").strip()
            preset_name = str(row.get("preset_name") or preset_name).strip() or preset_name
        except (OSError, csv.Error):
            pass
        presets.append({
            "filename": filename, "name": preset_name, "preset_id": preset_id,
        })
    return sorted(presets, key=lambda item: item["name"].casefold())


def rename_preset(filename, new_name, default_writer, default_summary):
    """Rename a saved preset without changing its stable identity or step IDs."""
    safe_name = os.path.basename(str(filename or ""))
    if safe_name != str(filename or "") or not safe_name.lower().endswith(".csv"):
        raise ValueError("invalid preset filename")
    old_path = os.path.abspath(os.path.join(PRESET_DIR, safe_name))
    if os.path.dirname(old_path) != os.path.abspath(PRESET_DIR) or not os.path.isfile(old_path):
        raise ValueError("preset file was not found")
    display_name = str(new_name or "").strip()[:100]
    if not display_name:
        raise ValueError("Enter a preset name")
    new_filename = _safe_preset_name(display_name) + ".csv"
    new_path = os.path.abspath(os.path.join(PRESET_DIR, new_filename))
    if os.path.normcase(new_path) != os.path.normcase(old_path) and os.path.exists(new_path):
        raise ValueError("A preset with that filename already exists")
    with open(old_path, "r", encoding="utf-8-sig", newline="") as handle:
        config = csv_to_config(
            handle.read(), default_writer, default_summary,
            preset_name=os.path.splitext(safe_name)[0])
    config["preset_name"] = display_name
    csv_text = config_to_csv(**config)
    temp_file = new_path + ".tmp"
    with _lock:
        with open(temp_file, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(csv_text)
        os.replace(temp_file, new_path)
        if os.path.normcase(new_path) != os.path.normcase(old_path):
            os.remove(old_path)
    active = save_config(**config)
    return {
        "filename": new_filename, "csv": csv_text,
        "preset_id": active["preset_id"], "preset_name": active["preset_name"],
        "config": active,
    }


def load_preset_file(filename, default_writer, default_summary):
    safe_name = os.path.basename(str(filename or ""))
    if safe_name != str(filename or "") or not safe_name.lower().endswith(".csv"):
        raise ValueError("invalid preset filename")
    path = os.path.abspath(os.path.join(PRESET_DIR, safe_name))
    if os.path.dirname(path) != os.path.abspath(PRESET_DIR) or not os.path.isfile(path):
        raise ValueError("preset file was not found")
    if os.path.getsize(path) > 1_000_000:
        raise ValueError("CSV file is too large")
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        csv_text = handle.read()
    config = csv_to_config(
        csv_text, default_writer, default_summary,
        preset_name=os.path.splitext(safe_name)[0])
    source_row = next(csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))), {})
    missing_identity = not str(source_row.get("preset_id") or "").strip()
    duplicate_files = sorted(
        item["filename"] for item in list_presets()
        if item.get("preset_id") and item["preset_id"] == config["preset_id"]
    )
    duplicate_identity = (
        len(duplicate_files) > 1 and safe_name != duplicate_files[0]
    )
    if duplicate_identity:
        config["preset_id"] = _new_preset_id()
    missing_step_id = any(
        not str(source_row.get(f"step{item['step']}_id") or "").strip()
        for item in config["steps"]
    )
    if missing_identity or missing_step_id or duplicate_identity:
        upgraded = config_to_csv(**config)
        temp_file = path + ".tmp"
        with _lock:
            with open(temp_file, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write(upgraded)
            os.replace(temp_file, path)
    return save_config(**config)


def csv_to_config(
    csv_text, default_writer, default_summary, *, preset_name="", preset_id=None,
):
    if not isinstance(csv_text, str) or not csv_text.strip() or len(csv_text) > 1_000_000:
        raise ValueError("CSV file is empty or too large")
    try:
        rows = list(csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))))
    except csv.Error as exc:
        raise ValueError(f"invalid CSV: {exc}") from exc
    if not rows:
        raise ValueError("CSV does not contain a preset row")
    row = rows[0]
    expected = {f"step{number}" for number in range(1, 24)} | {"writer"}
    if not row.keys() or not any(key in expected for key in row.keys()):
        raise ValueError("CSV must contain step1, step2, ... or writer columns")
    steps = []
    for number in range(1, 24):
        prompt = str(row.get(f"step{number}") or "").strip()
        if prompt:
            title = str(row.get(f"step{number}_title") or "").strip()
            steps.append({
                "id": str(row.get(f"step{number}_id") or "").strip() or _new_step_id(),
                "name": title or f"Step {number}",
                "step": number, "prompt": prompt,
                "temperature": row.get(f"step{number}_temperature") or DEFAULT_STEP_TEMPERATURE,
                "frequency_penalty": row.get(f"step{number}_frequency_penalty") or DEFAULT_FREQUENCY_PENALTY,
                "presence_penalty": row.get(f"step{number}_presence_penalty") or DEFAULT_PRESENCE_PENALTY,
                "repetition_penalty": row.get(f"step{number}_repetition_penalty") or DEFAULT_REPETITION_PENALTY,
                "repetition_penalty_range": row.get(f"step{number}_repetition_penalty_range") or DEFAULT_REPETITION_PENALTY_RANGE,
            })
    writer_prompt = str(row.get("writer") or "").strip() or default_writer["prompt"]
    writer_title = str(row.get("writer_title") or "").strip()
    writer = {
        "id": "writer", "name": writer_title or default_writer.get("name") or "Writer",
        "prompt": writer_prompt,
        "temperature": row.get("writer_temperature") or DEFAULT_WRITER_TEMPERATURE,
        "frequency_penalty": row.get("writer_frequency_penalty") or DEFAULT_FREQUENCY_PENALTY,
        "presence_penalty": row.get("writer_presence_penalty") or DEFAULT_PRESENCE_PENALTY,
        "repetition_penalty": row.get("writer_repetition_penalty") or DEFAULT_REPETITION_PENALTY,
        "repetition_penalty_range": row.get("writer_repetition_penalty_range") or DEFAULT_REPETITION_PENALTY_RANGE,
    }
    summary = {
        "id": "summary",
        "name": str(row.get("summarize_title") or "").strip() or "Summarize",
        "prompt": str(row.get("summarize") or "").strip() or default_summary["prompt"],
        "temperature": row.get("summarize_temperature") or DEFAULT_SUMMARY_TEMPERATURE,
        "frequency_penalty": row.get("summarize_frequency_penalty") or DEFAULT_FREQUENCY_PENALTY,
        "presence_penalty": row.get("summarize_presence_penalty") or DEFAULT_PRESENCE_PENALTY,
        "repetition_penalty": row.get("summarize_repetition_penalty") or DEFAULT_REPETITION_PENALTY,
        "repetition_penalty_range": row.get("summarize_repetition_penalty_range") or DEFAULT_REPETITION_PENALTY_RANGE,
    }
    actual_preset_id = _clean_preset_id(
        row.get("preset_id") or preset_id or _new_preset_id())
    return validate_config(
        steps, writer, summary, row.get("group_prompt") or "",
        actual_preset_id,
        row.get("preset_name") or preset_name or DEFAULT_PRESET_NAME)


def import_preset(csv_text, default_writer, default_summary, *, preset_name=""):
    config = csv_to_config(
        csv_text, default_writer, default_summary, preset_name=preset_name)
    return save_config(**config)
