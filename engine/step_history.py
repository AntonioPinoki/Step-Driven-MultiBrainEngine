"""Request-local RP step history, independent of provider and UI state."""

import copy
import re
import threading


def step_output_tag(slot):
    """Use the same title normalization for output, lookup, and validation."""
    title = str(slot.get("name") or "").strip().lower()
    tag = re.sub(r"[^\w-]+", "", title, flags=re.UNICODE).strip("_-")
    if not tag or tag[0].isdigit():
        tag = f"step{slot.get('step', '')}"
    return tag


def strip_thoughts(content):
    if not isinstance(content, str):
        return content
    return re.sub(r"<think\b[^>]*>.*?</think>\s*", "", content,
                  flags=re.DOTALL | re.IGNORECASE)


def visible_messages(messages):
    """Preserve message order, roles, and metadata while removing thoughts."""
    result = copy.deepcopy(messages)
    for message in result:
        if isinstance(message.get("content"), str):
            message["content"] = strip_thoughts(message["content"])
    return result


class PresetHistoryState:
    """One atomic switch decision per normal chain, shared by the server."""

    def __init__(self):
        self._last_preset_id = None
        self._lock = threading.Lock()

    def begin_chain(self, preset_id):
        with self._lock:
            switched = self._last_preset_id is not None and self._last_preset_id != preset_id
            self._last_preset_id = preset_id
            return switched


def select_history_turns(messages, binding, limit=3):
    """Select before parsing: missing output never backfills an older RP turn."""
    character = binding.get("history_char_name", binding.get("char_name", ""))
    is_group = binding.get("is_group", False)
    selected = []
    excluded = []
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        name = str(message.get("name") or "").strip()
        content = message.get("content")
        if name:
            own = bool(character and name == character)
        elif not is_group:
            own = True
        else:
            # An exact speaker label is evidence; a name prefix in prose is not.
            visible = strip_thoughts(content)
            own = bool(character and isinstance(visible, str) and re.match(
                rf"^\s*{re.escape(character)}\s*[:：]", visible))
        if not own:
            excluded.append({"message_index": index, "reason": "other_or_unknown_character"})
            continue
        selected.append((index, content))
        if len(selected) >= limit:
            break
    selected.reverse()
    return selected, excluded


def parse_history_turns(turns):
    """Malformed/ambiguous DEEP DIVE blocks are optional memory, never errors."""
    parsed, excluded = [], []
    start, end = "[DEEP DIVE]", "[/DEEP DIVE]"
    for index, content in turns:
        reason = None
        if not isinstance(content, str):
            reason = "non_text_content"
        elif start not in content and end not in content:
            reason = "missing_deep_dive"
        elif content.count(start) != 1 or content.count(end) != 1:
            reason = "malformed_deep_dive"
        elif content.index(end) < content.index(start):
            reason = "malformed_deep_dive"
        if reason:
            excluded.append({"message_index": index, "reason": reason})
            continue
        parsed.append((index, content.split(start, 1)[1].split(end, 1)[0]))
    return parsed, excluded


def extract_step_history(turns, slot, max_chars=None):
    """Return literal tag bodies in time order; optional budget drops oldest first."""
    tag = step_output_tag(slot)
    opening, closing = f"<{tag}>", f"</{tag}>"
    matches, excluded = [], []
    for index, dive in turns:
        if opening not in dive and closing not in dive:
            reason = "missing_step_tag"
        elif dive.count(opening) != 1 or dive.count(closing) != 1:
            reason = "malformed_or_duplicate_step_tag"
        elif dive.index(closing) < dive.index(opening):
            reason = "malformed_step_tag"
        else:
            matches.append((index, dive.split(opening, 1)[1].split(closing, 1)[0]))
            continue
        excluded.append({"message_index": index, "reason": reason})
    matched_count = len(matches)
    total_chars = sum(len(body) for _, body in matches)
    while matches and max_chars is not None and total_chars > max_chars:
        index, body = matches.pop(0)
        total_chars -= len(body)
        excluded.append({"message_index": index, "reason": "history_size_limit"})
    return [body for _, body in matches], {
        "tag": tag, "matched_turns": matched_count, "inserted_turns": len(matches),
        "excluded": excluded,
    }
