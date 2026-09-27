"""Compact, human-editable staged-lore state stored in the visible think block."""

from __future__ import annotations

import re


INFORMATION_RE = re.compile(
    r"<information>\s*\n?lorebook-state-v1\s*\n(.*?)\n?</information>",
    flags=re.DOTALL | re.IGNORECASE,
)
RESERVED = "\\@{},=/|"


def _escape(value):
    text = str(value or "")
    for char in RESERVED:
        text = text.replace(char, "\\" + char)
    return text


def _split_unescaped(text, delimiter):
    parts, buffer, escaped = [], [], False
    for char in str(text):
        if escaped:
            if char == delimiter or char == "\\":
                buffer.append(char)
            else:
                buffer.extend(("\\", char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == delimiter:
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    if escaped:
        buffer.append("\\")
    parts.append("".join(buffer))
    return parts


def _unescape(value):
    output, escaped = [], False
    for char in str(value):
        if escaped:
            output.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        else:
            output.append(char)
    if escaped:
        output.append("\\")
    return "".join(output)


def strip_information(text):
    if not isinstance(text, str):
        return text
    return INFORMATION_RE.sub("", text).strip()


def parse_latest(messages):
    """Read only the first lore information block in the newest assistant response."""
    for message in reversed(messages if isinstance(messages, list) else []):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            return {}, {}
        think = re.match(
            r"\s*<think\b[^>]*>(.*?)</think>", content,
            flags=re.DOTALL | re.IGNORECASE)
        if not think:
            return {}, {}
        match = INFORMATION_RE.match(think.group(1).lstrip())
        if not match:
            return {}, {}
        state, labels = {}, {}
        for raw_line in match.group(1).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = _split_unescaped(line, "=")
            if len(parts) != 2:
                continue
            identity, count_text = parts
            try:
                count = max(0, int(count_text))
            except ValueError:
                continue
            identity_parts = _split_unescaped(identity, "|")
            if len(identity_parts) not in (2, 4):
                continue
            entry_name, entry_id = map(_unescape, identity_parts[:2])
            if len(identity_parts) == 4:
                node_name, node_id = map(_unescape, identity_parts[2:])
                key = f"{entry_id}/{node_id}"
                labels[key] = (entry_name, node_name)
            else:
                key = entry_id
                labels[key] = (entry_name, None)
            state[key] = count
        return state, labels
    return {}, {}


def serialize(state, labels):
    lines = []
    for key in sorted(state):
        count = max(0, int(state[key]))
        if count <= 0:
            continue
        entry_name, node_name = labels.get(key, (key.split("/", 1)[0], None))
        if "/" in key:
            entry_id, node_id = key.split("/", 1)
            identity = "|".join(map(_escape, (entry_name, entry_id, node_name or node_id, node_id)))
        else:
            identity = "|".join(map(_escape, (entry_name, key)))
        lines.append(f"{identity}={count}")
    if not lines:
        return ""
    return "<information>\nlorebook-state-v1\n" + "\n".join(lines) + "\n</information>"


def advance(state, labels, injected_keys, injected_labels):
    updated = dict(state or {})
    merged_labels = dict(labels or {})
    merged_labels.update(injected_labels or {})
    for key in dict.fromkeys(injected_keys or []):
        updated[key] = updated.get(key, 0) + 1
    return updated, merged_labels
