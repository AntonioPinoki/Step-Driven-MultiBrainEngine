"""Request-local persistent lore transitions, independent of rendering budgets."""

import copy


def validate(raw, condition_mode):
    raw = raw if raw is not None else {}
    if not isinstance(raw, dict) or set(raw) - {"enabled", "stop_keywords", "deactivates_entry_ids", "control_only"}:
        raise ValueError("invalid persistence settings")
    result = {"enabled": raw.get("enabled", False), "control_only": raw.get("control_only", False),
              "stop_keywords": raw.get("stop_keywords", []), "deactivates_entry_ids": raw.get("deactivates_entry_ids", [])}
    for field in ("enabled", "control_only"):
        if type(result[field]) is not bool:
            raise ValueError(f"persistence.{field} must be boolean")
    for field in ("stop_keywords", "deactivates_entry_ids"):
        values = result[field]
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"persistence.{field} must contain nonempty strings")
        result[field] = list(dict.fromkeys(values))
    if result["enabled"] and condition_mode != "normal":
        raise ValueError("persistent lore supports normal conditions only")
    return result


def evaluate(store, entries, messages, settings, search_sources, counts, active, blocked, rng, attempted=None):
    """End conditions win across every assigned book, before groups and budgets."""
    candidates, stops, starts = [], set(), set()
    for entry in entries:
        if not entry["enabled"]:
            continue
        identity = entry["entry_id"]
        persistence = entry.get("persistence", {})
        depth = entry["scan_depth"] or settings["scan_depth"]
        sources = {**search_sources, "chat": store.chat_blocks(messages, depth)}
        selected = store._entry_sources(entry, sources)
        text = "\n".join(block for blocks in selected.values() for block in blocks)
        case = entry["case_sensitive"] if entry["case_sensitive"] is not None else settings["case_sensitive"]
        whole = entry["match_whole_words"] if entry["match_whole_words"] is not None else settings["match_whole_words"]
        keywords = persistence.get("stop_keywords", [])
        if persistence.get("enabled") and any(store._matches(text, word, case, whole) for word in keywords):
            stops.add(identity)
        original = store._entry_candidate(entry, sources, settings, counts)
        was_active = persistence.get("enabled") and identity in active
        candidate = original
        if was_active:
            forced = copy.deepcopy(entry)
            forced.update(constant=True, observation_mode="standard")
            candidate = store._entry_candidate(forced, sources, settings, counts)
        if candidate is None:
            continue
        if not was_active:
            passes_probability = not entry["use_probability"] or rng.random() * 100 < entry["probability"]
            if attempted is not None:
                attempted[identity] = passes_probability
            if not passes_probability:
                continue
        if original is not None:
            stops.update(persistence.get("deactivates_entry_ids", []))
        if persistence.get("enabled"):
            starts.add(identity)
        if not persistence.get("control_only"):
            candidates.append(candidate)
    blocked.update(stops)
    active.difference_update(stops)
    active.update(starts - blocked)
    return [item for item in candidates if item["entry_id"] not in blocked]
