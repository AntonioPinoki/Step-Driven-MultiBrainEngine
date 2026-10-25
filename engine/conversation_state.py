"""Versioned, authenticated completion snapshots carried by retained think history."""

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading

KEY_FILE = Path(__file__).resolve().parents[1] / "Lorebooks" / ".conversation-state-key"
_lock = threading.Lock()
PATTERN = re.compile(r"<be-conversation-state-v1>\s*([^<]+)\s*</be-conversation-state-v1>")


def _key(create=False):
    with _lock:
        if not KEY_FILE.exists() and create:
            KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(os.urandom(32))
            except FileExistsError:
                pass
        return KEY_FILE.read_bytes() if KEY_FILE.exists() else None


def _encoded(payload):
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e")


def serialize(payload):
    text = _encoded({**payload, "version": 1, "committed": True})
    envelope = {"payload": json.loads(text), "signature": hmac.new(_key(True), text.encode(), hashlib.sha256).hexdigest()}
    return "<be-conversation-state-v1>\n" + _encoded(envelope) + "\n</be-conversation-state-v1>"


def parse_latest(messages, preset_id, chat_id="", branch_root=""):
    """The newest assistant is the boundary; never search older states past it."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            return None
        think = re.match(r"\s*<think\b[^>]*>(.*?)</think>", content, re.S | re.I)
        match = PATTERN.search(think.group(1)) if think else None
        if not match:
            return None
        try:
            envelope = json.loads(match.group(1))
            payload = envelope["payload"]
            key = _key()
            if not key or not hmac.compare_digest(envelope["signature"], hmac.new(key, _encoded(payload).encode(), hashlib.sha256).hexdigest()):
                return None
            if payload.get("version") != 1 or payload.get("committed") is not True or payload.get("preset_id") != preset_id:
                return None
            if chat_id and payload.get("chat_id") and payload["chat_id"] != chat_id:
                ancestors = [payload.get("chat_id"), *payload.get("lineage", [])]
                if not branch_root or branch_root not in ancestors:
                    return None
            return payload
        except (ValueError, KeyError, TypeError):
            return None
    return None


def strip_state(text):
    return PATTERN.sub("", text)
