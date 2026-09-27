"""Deterministic context budgeting and safe, provenance-aware trimming."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


class ContextInputError(ValueError):
    """Raised when outbound message content cannot be estimated safely."""


class ContextWindowExceeded(ValueError):
    """Raised before an upstream call when protected input cannot fit."""

    def __init__(self, detail):
        super().__init__("The request exceeds the configured context window.")
        self.detail = detail


@dataclass(frozen=True)
class PreflightResult:
    messages: list[dict]
    input_tokens: int
    input_budget: int
    safety_margin: int
    removed_past_outputs: int = 0
    removed_chat_turns: int = 0


def canonical_content(content):
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if not isinstance(content, (dict, list, tuple, int, float, bool)):
        raise ContextInputError(
            f"Unsupported message content type: {type(content).__name__}"
        )
    try:
        return json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContextInputError("Message content could not be normalized.") from exc


def estimate_message_tokens(message):
    content = canonical_content(message.get("content"))
    characters = math.ceil(len(content) / 4)
    utf8_bytes = math.ceil(len(content.encode("utf-8")) / 3)
    return max(characters, utf8_bytes) + 8


def estimate_messages_tokens(messages):
    return sum(estimate_message_tokens(message) for message in messages)


def safety_margin(effective_context_window):
    return max(512, math.ceil(int(effective_context_window) * 0.02))


def input_budget(effective_context_window, max_output_tokens):
    margin = safety_margin(effective_context_window)
    return int(effective_context_window) - int(max_output_tokens) - margin, margin


def _chat_turn_groups(messages, chat_indices):
    indices = sorted({
        int(index) for index in chat_indices
        if 0 <= int(index) < len(messages)
        and messages[int(index)].get("role") != "system"
    })
    if not indices:
        return []
    groups = []
    current = []
    saw_user = False
    for index in indices:
        role = messages[index].get("role")
        if role == "user":
            if current:
                groups.append(current)
            current = [index]
            saw_user = True
        else:
            if not saw_user and current:
                current.append(index)
            else:
                current.append(index)
    if current:
        groups.append(current)
    return groups


def _latest_protected_group(messages, groups):
    latest_user_group = None
    for group_index, group in enumerate(groups):
        if any(messages[index].get("role") == "user" for index in group):
            latest_user_group = group_index
    if latest_user_group is not None:
        return latest_user_group
    return len(groups) - 1 if groups else None


def _detail(provider_context, *, input_tokens, output_tokens, margin,
            removed_past_outputs, removed_chat_turns):
    return {
        "error": "context_window_exceeded",
        "provider": str(provider_context.get("provider") or "unknown"),
        "effective_context_window": int(
            provider_context.get("effective_context_window") or 0
        ),
        "estimated_input_tokens": int(input_tokens),
        "reserved_output_tokens": int(output_tokens),
        "safety_margin": int(margin),
        "removed_past_outputs": int(removed_past_outputs),
        "removed_chat_turns": int(removed_chat_turns),
    }


def preflight_messages(messages: Sequence[Mapping], *, provider_context,
                       max_output_tokens, chat_indices: Iterable[int] = (),
                       protected_indices: Iterable[int] = (),
                       removed_past_outputs=0):
    """Estimate and trim removable chat turns, never protected message bodies."""
    effective = int(provider_context.get("effective_context_window") or 0)
    output_tokens = int(max_output_tokens)
    budget, margin = input_budget(effective, output_tokens)
    copied = [copy.deepcopy(dict(message)) for message in messages]
    estimated = estimate_messages_tokens(copied)
    if budget <= 0:
        raise ContextWindowExceeded(_detail(
            provider_context,
            input_tokens=estimated,
            output_tokens=output_tokens,
            margin=margin,
            removed_past_outputs=removed_past_outputs,
            removed_chat_turns=0,
        ))
    if budget > 0 and estimated <= budget:
        return PreflightResult(
            copied, estimated, budget, margin,
            removed_past_outputs=int(removed_past_outputs),
        )

    protected = {
        int(index) for index in protected_indices
        if 0 <= int(index) < len(copied)
    }
    protected.update(
        index for index, message in enumerate(copied)
        if message.get("role") == "system"
    )
    groups = _chat_turn_groups(copied, chat_indices)
    latest_group = _latest_protected_group(copied, groups)
    removed_indices = set()
    removed_turns = 0
    for group_index, group in enumerate(groups):
        if group_index == latest_group or protected.intersection(group):
            continue
        removed_indices.update(group)
        removed_turns += 1
        candidate = [
            message for index, message in enumerate(copied)
            if index not in removed_indices
        ]
        estimated = estimate_messages_tokens(candidate)
        if budget > 0 and estimated <= budget:
            return PreflightResult(
                candidate, estimated, budget, margin,
                removed_past_outputs=int(removed_past_outputs),
                removed_chat_turns=removed_turns,
            )

    final_messages = [
        message for index, message in enumerate(copied)
        if index not in removed_indices
    ]
    estimated = estimate_messages_tokens(final_messages)
    raise ContextWindowExceeded(_detail(
        provider_context,
        input_tokens=estimated,
        output_tokens=output_tokens,
        margin=margin,
        removed_past_outputs=removed_past_outputs,
        removed_chat_turns=removed_turns,
    ))
