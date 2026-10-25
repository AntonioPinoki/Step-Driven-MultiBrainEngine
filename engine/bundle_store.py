"""Portable preset/lore bundles with graph remapping and journaled rollback."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

import lorebook_store as lore
import prompt_store as prompts
from storage_lock import LOCK

FORMAT = "brainengine_bundle"
FEATURES = {"step-inputs-v1", "variables-v1", "persistent-lore-v1"}
MAX_BYTES = 32 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def validate(bundle):
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT or type(bundle.get("version")) is not int or bundle["version"] != 1:
        raise ValueError("Expected .brainengine.json package version 1; use the CSV importer for CSV files")
    features = bundle.get("features", [])
    if not isinstance(features, list) or any(not isinstance(v, str) or v not in FEATURES for v in features):
        raise ValueError("Unknown required package feature")
    if len(encoded(bundle)) > MAX_BYTES:
        raise ValueError("Package exceeds 32 MiB")
    preset = prompts.validate_config(**bundle["preset"])
    books, entry_ids, node_ids, filename_hints = {}, set(), set(), set()
    for raw in bundle.get("lorebooks", []):
        identity = raw["bundle_book_id"]
        if not isinstance(identity, str) or not identity or identity in books:
            raise ValueError("Invalid or duplicate package book ID")
        filename = raw["filename_hint"]
        if not isinstance(filename, str) or "/" in filename or "\\" in filename or ":" in filename or filename in {".", ".."} or not filename.lower().endswith(".json"):
            raise ValueError("Unsafe book filename")
        if filename.casefold() in filename_hints:
            raise ValueError("Duplicate package book filename")
        filename_hints.add(filename.casefold())
        document = raw["document"]
        if len(encoded(document)) > lore.MAX_BOOK_BYTES:
            raise ValueError("Book exceeds 8 MiB")
        book = lore.import_sillytavern(document, book_id=filename, strict=True)
        for entry in book["entries"]:
            if entry["entry_id"] in entry_ids:
                raise ValueError("Duplicate stable entry ID")
            entry_ids.add(entry["entry_id"])
            for node in walk_nodes(entry.get("condition_data", {}).get("nodes", [])):
                if node["node_id"] in node_ids:
                    raise ValueError("Duplicate node ID")
                node_ids.add(node["node_id"])
        books[identity] = book
    for book in books.values():
        for entry in book["entries"]:
            targets = entry.get("persistence", {}).get("deactivates_entry_ids", [])
            if any(t not in entry_ids or t == entry["entry_id"] for t in targets):
                raise ValueError("Unknown or self-referencing persistent lore stop target")
    lore.validate_persistence_graph(list(books.values()))
    profile = bundle.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("assignments"), dict):
        raise ValueError("Missing package profile")
    agents = {s["id"] for s in preset["steps"]} | {"writer"}
    assigned = set()
    for agent, refs in profile["assignments"].items():
        if agent not in agents or not isinstance(refs, list) or any(not isinstance(v, str) or v not in books for v in refs):
            raise ValueError("Invalid package assignment reference")
        assigned.update(refs)
    if assigned != set(books):
        raise ValueError("Package must contain exactly its assigned books")
    for field in ("character_context_search_enabled", "previous_step_output_search_enabled"):
        if type(profile.get(field)) is not bool:
            raise ValueError(f"{field} must be boolean")
    recommended = bundle.get("recommended_global_settings", {})
    if not isinstance(recommended, dict) or set(recommended) - set(lore.DEFAULT_SETTINGS):
        raise ValueError("Invalid recommended global settings")
    lore._validate_global_settings(recommended)
    return preset, books


def walk_nodes(nodes):
    for node in nodes:
        yield node
        yield from walk_nodes(node.get("children", []))


def export_bundle(preset):
    with LOCK:
        preset = prompts.validate_config(**preset)
        setup = lore.load_config(preset)
        needed = {ref for refs in setup["assignments"].values() for ref in refs}
        known = {b["id"]: b for b in setup["books"]}
        if needed - set(known):
            raise ValueError("Assigned lorebook files are missing")
        books = []
        mapping = {filename: f"book_{i}" for i, filename in enumerate(sorted(needed), 1)}
        for filename in sorted(needed):
            path = Path(lore.BOOKS_DIR) / filename
            if path.stat().st_size > lore.MAX_BOOK_BYTES:
                raise ValueError("Book exceeds 8 MiB")
            books.append({"bundle_book_id": mapping[filename], "filename_hint": filename,
                          "document": json.loads(path.read_text(encoding="utf-8-sig"))})
        result = {"format": FORMAT, "version": 1, "name": preset["preset_name"],
                  "features": sorted(FEATURES), "preset": preset, "lorebooks": books,
                  "profile": {key: setup[key] for key in ("character_context_search_enabled", "previous_step_output_search_enabled")},
                  "recommended_global_settings": setup["settings"]}
        result["profile"]["assignments"] = {agent: [mapping[v] for v in refs] for agent, refs in setup["assignments"].items()}
        validate(result)
        return result


def read_bundle(path):
    if Path(path).stat().st_size > MAX_BYTES:
        raise ValueError("Package exceeds 32 MiB")
    result = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    validate(result)
    return result


def remap_new(bundle):
    result = copy.deepcopy(bundle)
    preset, books = validate(result)
    step_ids = {s["id"]: "step_" + uuid.uuid4().hex[:12] for s in preset["steps"]}
    variable_ids = {v["id"]: "var_" + uuid.uuid4().hex[:12] for v in preset["variables"]}
    entries, nodes = {}, {}
    for book in books.values():
        for entry in book["entries"]:
            entries[entry["entry_id"]] = "entry_" + uuid.uuid4().hex[:12]
            for node in walk_nodes(entry.get("condition_data", {}).get("nodes", [])):
                nodes[node["node_id"]] = "node_" + uuid.uuid4().hex[:12]
    preset["preset_id"] = "preset_" + uuid.uuid4().hex
    for step in preset["steps"]:
        step["id"] = step_ids[step["id"]]
        step["prior_output_reference"]["step_ids"] = [step_ids[v] for v in step["prior_output_reference"]["step_ids"]]
    for variable in preset["variables"]:
        variable["id"] = variable_ids[variable["id"]]
        for field in ("readable_by", "writable_by"):
            variable[field] = [step_ids.get(v, v) for v in variable[field]]
    result["preset"] = preset
    for raw in result["lorebooks"]:
        book = books[raw["bundle_book_id"]]
        for entry in book["entries"]:
            entry["id"] = "entry_" + uuid.uuid4().hex[:12]
            entry["entry_id"] = entries[entry["entry_id"]]
            persistence = entry["persistence"]
            persistence["deactivates_entry_ids"] = [entries[v] for v in persistence["deactivates_entry_ids"]]
            for node in walk_nodes(entry.get("condition_data", {}).get("nodes", [])):
                node["node_id"] = nodes[node["node_id"]]
            entry["extensions"]["brainengine"].update(entry_id=entry["entry_id"], persistence=persistence,
                condition_data=entry["condition_data"])
        document = copy.deepcopy(raw["document"])
        owner, key, container = lore._source_entry_location(document)
        originals = list(container.values()) if isinstance(container, dict) else list(container)
        converted = [lore._merge_native_entry(original, entry, "uid" in original or "key" in original)
                     for original, entry in zip(originals, book["entries"])]
        for native, entry in zip(converted, book["entries"]):
            if "id" in native:
                native["id"] = entry["id"]
            if "uid" in native:
                native["uid"] = entry["id"]
        owner[key] = {entry["id"]: native for entry, native in zip(book["entries"], converted)} if isinstance(container, dict) else converted
        raw["document"] = document
    result["profile"]["assignments"] = {step_ids.get(agent, agent): refs for agent, refs in result["profile"]["assignments"].items()}
    validate(result)
    return result


def _journal_path():
    return Path(lore.LOREBOOK_DIR) / ".bundle-journal.json"


def _write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".bundle-tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def recover():
    with LOCK:
        journal = _journal_path()
        if not journal.exists():
            return False
        payload = json.loads(journal.read_text())
        allowed = {Path(prompts.PRESET_DIR).resolve(), Path(lore.BOOKS_DIR).resolve()}
        for record in payload["files"]:
            path = Path(record["path"])
            if path.parent.resolve() not in allowed and path.resolve() != Path(lore.SETTINGS_FILE).resolve():
                raise ValueError("Unsafe recovery journal path")
            if record["before"] is None:
                path.unlink(missing_ok=True)
            else:
                _write_bytes(path, base64.b64decode(record["before"]))
        journal.unlink()
        return True


def plan_import(bundle, mode="new", selected_global=()):
    validate(bundle)
    if mode not in {"new", "restore"}:
        raise ValueError("Choose new or restore")
    candidate = remap_new(bundle) if mode == "new" else copy.deepcopy(bundle)
    preset, books = validate(candidate)
    settings = lore._load_settings_document()
    current = copy.deepcopy(settings)
    writes, conflicts, mapping, reused = {}, [], {}, []
    existing_presets = prompts.list_presets()
    matches = [p for p in existing_presets if p.get("preset_id") == preset["preset_id"]]
    if len(matches) > 1:
        raise ValueError("Duplicate existing preset ID")
    if matches:
        path = Path(prompts.PRESET_DIR) / matches[0]["filename"]
        existing = prompts.csv_to_config(path.read_text(encoding="utf-8-sig"), preset["writer"], preset["summary"])
        if encoded(existing) != encoded(preset):
            conflicts.append(str(path.name))
        reused.append(str(path.name))
    else:
        name = prompts.unique_preset_name(preset["preset_name"])
        if mode == "new":
            preset["preset_name"] = name
        path = Path(prompts.PRESET_DIR) / (prompts._safe_preset_name(name) + ".csv")
        writes[path] = prompts.config_to_csv(**preset).encode("utf-8-sig")
    preset_path = path
    disk_names = [p.name for p in Path(lore.BOOKS_DIR).glob("*.json")]
    existing_names = list(disk_names)
    for raw in candidate["lorebooks"]:
        identity = raw["bundle_book_id"]
        filename = raw["filename_hint"]
        existing_name = next((n for n in disk_names if n.casefold() == filename.casefold()), None)
        if mode == "restore":
            incoming_ids = {entry["entry_id"] for entry in books[identity]["entries"]}
            for old_name in disk_names:
                old_document = json.loads((Path(lore.BOOKS_DIR) / old_name).read_text(encoding="utf-8-sig"))
                old_book = lore.import_sillytavern(old_document, book_id=old_name, strict=True)
                if incoming_ids & {entry["entry_id"] for entry in old_book["entries"]}:
                    if encoded(old_document) != encoded(raw["document"]):
                        conflicts.append(old_name + " (stable entry ID)")
                    else:
                        existing_name = old_name
                    break
        if mode == "new":
            filename = lore._unique_book_filename(Path(filename).stem, existing_names)
        elif existing_name:
            filename = existing_name
            existing_document = json.loads((Path(lore.BOOKS_DIR) / filename).read_text(encoding="utf-8-sig"))
            if encoded(existing_document) != encoded(raw["document"]):
                conflicts.append(filename)
            else:
                reused.append(filename)
        mapping[identity] = filename
        if mode == "new" or not existing_name:
            writes[Path(lore.BOOKS_DIR) / filename] = encoded(raw["document"])
        existing_names.append(filename)
    profile = candidate["profile"]
    agents = {s["id"]: s for s in preset["steps"]} | {"writer": preset["writer"]}
    incoming = {"last_known_name": preset["preset_name"],
        "character_context_search_enabled": profile["character_context_search_enabled"],
        "previous_step_output_search_enabled": profile["previous_step_output_search_enabled"],
        "assignments": {agent: {"target_name": agents[agent]["name"], "target_position": agents[agent].get("step"),
                                  "books": [mapping[v] for v in refs]} for agent, refs in profile["assignments"].items()}}
    old_profile = settings["profiles"].get(preset["preset_id"])
    if old_profile is not None and encoded(old_profile) != encoded(incoming):
        conflicts.append("preset lore profile")
    settings["profiles"][preset["preset_id"]] = incoming
    recommended = candidate.get("recommended_global_settings", {})
    if any(key not in recommended for key in selected_global):
        raise ValueError("Unknown selected global setting")
    settings["settings"].update({key: recommended[key] for key in selected_global})
    writes[Path(lore.SETTINGS_FILE)] = encoded(lore.validate_settings_document(settings))
    if conflicts:
        raise ValueError("Restore cancelled; content conflicts: " + ", ".join(conflicts) + ". Choose new to add a copy.")
    paths = set(writes)
    paths.update(Path(prompts.PRESET_DIR) / p["filename"] for p in existing_presets)
    paths.update(Path(lore.BOOKS_DIR) / filename for filename in existing_names)
    fingerprints = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None for path in paths}
    catalogs = {str(Path(prompts.PRESET_DIR)): sorted(p.name.casefold() for p in Path(prompts.PRESET_DIR).glob("*.csv")),
                str(Path(lore.BOOKS_DIR)): sorted(p.name.casefold() for p in Path(lore.BOOKS_DIR).glob("*.json"))}
    return {"preset": preset, "writes": writes, "fingerprints": fingerprints, "catalogs": catalogs, "preset_path": preset_path, "expected_profile": incoming, "reused": reused,
            "global_diff": {key: {"current": current["settings"].get(key), "recommended": value,
                                  "apply": key in selected_global} for key, value in recommended.items()}}


def commit(plan):
    with LOCK:
        recover()
        for directory, expected in plan["catalogs"].items():
            suffix = "*.csv" if Path(directory) == Path(prompts.PRESET_DIR) else "*.json"
            if sorted(p.name.casefold() for p in Path(directory).glob(suffix)) != expected:
                raise ValueError("Storage changed since preview; validate package again")
        for filename, expected in plan["fingerprints"].items():
            path = Path(filename)
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if actual != expected:
                raise ValueError("Storage changed since preview; validate package again")
        records = [{"path": str(path.resolve()), "before": base64.b64encode(path.read_bytes()).decode() if path.exists() else None}
                   for path in plan["writes"]]
        journal = _journal_path()
        _write_bytes(journal, encoded({"files": records}))
        try:
            for path, data in plan["writes"].items():
                _write_bytes(path, data)
            # Check every staged definition and assignment before committing journal.
            config = plan["preset"]
            loaded = lore.load_config(config)
            if loaded.get("missing_files"):
                raise ValueError("Imported assignments have missing books")
            restored = prompts.csv_to_config(Path(plan["preset_path"]).read_text(encoding="utf-8-sig"), config["writer"], config["summary"])
            if encoded(restored) != encoded(config):
                raise ValueError("Imported preset did not round-trip")
            expected = {agent: record["books"] for agent, record in plan["expected_profile"]["assignments"].items()}
            if loaded["assignments"] != expected:
                raise ValueError("Imported assignments did not round-trip")
            for key in ("character_context_search_enabled", "previous_step_output_search_enabled"):
                if loaded[key] != plan["expected_profile"][key]:
                    raise ValueError("Imported search settings did not round-trip")
            journal.unlink()
        except BaseException:
            recover()
            raise
        return {"imported": True, "preset_id": plan["preset"]["preset_id"],
                "preset_name": plan["preset"]["preset_name"], "reused": plan["reused"]}
