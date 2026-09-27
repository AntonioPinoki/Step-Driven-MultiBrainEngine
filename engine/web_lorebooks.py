"""Gradio Lorebook editor with lossless draft updates."""

from __future__ import annotations

import copy
import html
import json
import os
import uuid
from typing import Any


MAX_ASSIGNMENT_AGENTS = 24


def _copy(value):
    return copy.deepcopy(value)


def book_choices(draft):
    return [(book.get("name") or book["id"], book["id"]) for book in (draft or {}).get("books", [])]


def assignment_book_choices(draft):
    choices = book_choices(draft)
    known = {value for _, value in choices}
    choices.extend(
        (f"⚠ {filename} (Missing)", filename)
        for filename in (draft or {}).get("missing_files", [])
        if filename not in known
    )
    return choices


def entry_choices(draft, book_id):
    book = find_book(draft, book_id)
    return [
        (f"{entry.get('name') or entry['id']} · {str(entry.get('entry_id') or entry['id'])[-8:]}",
         entry["id"])
        for entry in (book or {}).get("entries", [])
    ]


def find_book(draft, book_id):
    return next((book for book in (draft or {}).get("books", []) if book.get("id") == book_id), None)


def find_entry(draft, book_id, entry_id):
    book = find_book(draft, book_id)
    return next((entry for entry in (book or {}).get("entries", []) if entry.get("id") == entry_id), None)


def update_entry(draft, book_id, entry_id, **changes):
    """Return a new draft while preserving fields not exposed by the web UI."""
    updated = _copy(draft or {})
    entry = find_entry(updated, book_id, entry_id)
    if entry is None:
        raise ValueError("Select a lorebook entry first")
    for key, value in changes.items():
        if key in ("keys", "secondary_keys"):
            value = [part.strip() for part in str(value or "").split(",") if part.strip()]
        entry[key] = value
    return updated


def update_book_name(draft, book_id, name):
    """Return a new draft with the selected lorebook renamed."""
    updated = _copy(draft or {})
    book = find_book(updated, book_id)
    if book is None:
        raise ValueError("Select a lorebook first")
    book["name"] = str(name or "").strip() or "Lorebook"
    return updated


def add_book(draft, name="New Lorebook"):
    updated = _copy(draft or {})
    updated.setdefault("books", [])
    book = {"id": f"book_{uuid.uuid4().hex[:12]}", "name": name, "entries": []}
    updated["books"].append(book)
    return updated, book["id"]


def add_entry(draft, book_id):
    updated = _copy(draft or {})
    book = find_book(updated, book_id)
    if book is None:
        raise ValueError("Select a lorebook first")
    entry = {
        "id": f"entry_{uuid.uuid4().hex[:12]}", "name": "New Entry", "content": "",
        "keys": [], "secondary_keys": [], "constant": False, "selective": False,
        "selective_logic": 0, "enabled": True, "order": 100, "scan_depth": None,
        "case_sensitive": None, "match_whole_words": None, "use_probability": False,
        "probability": 100, "exclude_recursion": False, "prevent_recursion": False,
        "sticky": None, "cooldown": None, "delay": None, "group": "",
        "group_override": False, "group_weight": 100, "extensions": {},
        "entry_id": f"entry_{uuid.uuid4().hex[:12]}",
        "observation_mode": "standard",
        "observation_sources": [
            "chat", "character.description", "character.personality", "scenario",
            "character.depth_prompt", "character.creator_notes", "user.persona",
        ],
        "condition_mode": "normal", "condition_data": {},
        "content_mode": "fixed", "stages": [""],
    }
    book.setdefault("entries", []).append(entry)
    return updated, entry["id"]


def delete_entry(draft, book_id, entry_id):
    updated = _copy(draft or {})
    book = find_book(updated, book_id)
    if book:
        book["entries"] = [item for item in book.get("entries", []) if item.get("id") != entry_id]
    return updated


def delete_book(draft, book_id):
    updated = _copy(draft or {})
    updated["books"] = [book for book in updated.get("books", []) if book.get("id") != book_id]
    for agent_id, assigned in list(updated.get("assignments", {}).items()):
        remaining = [item for item in assigned if item != book_id]
        if remaining:
            updated["assignments"][agent_id] = remaining
        else:
            updated["assignments"].pop(agent_id, None)
    return updated


def update_assignments(draft, agent_ids, selections):
    """Return a draft containing the complete set of visible agent assignments."""
    updated = _copy(draft or {})
    known_books = (
        {book["id"] for book in updated.get("books", [])}
        | {str(item) for item in updated.get("missing_files", [])}
    )
    assignments = {}
    for agent_id, selected in zip(agent_ids, selections):
        agent_id = str(agent_id or "")
        if not agent_id or agent_id == "summary":
            continue
        ordered = []
        for book_id in selected or []:
            book_id = str(book_id)
            if book_id in known_books and book_id not in ordered:
                ordered.append(book_id)
        if ordered:
            assignments[agent_id] = ordered
    updated["assignments"] = assignments
    return updated


def assigned_book_names(draft, book_ids):
    names = {
        str(book["id"]): str(book.get("name") or book["id"])
        for book in (draft or {}).get("books", [])
    }
    return [
        names.get(str(book_id), f"⚠ {book_id} (Missing)")
        for book_id in book_ids or []
    ]


def remap_assignments(assignments, book_id_map):
    """Replace temporary draft book IDs after their first file save."""
    return {
        str(agent_id): [book_id_map.get(str(book_id), str(book_id)) for book_id in book_ids]
        for agent_id, book_ids in (assignments or {}).items()
    }


def edit_ordered_words(text, index, action, new_word=""):
    words = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    position = max(0, min(int(index or 1) - 1, max(0, len(words) - 1)))
    if action == "add":
        insertion = position + 1 if words else 0
        words.insert(insertion, str(new_word or "New word").strip() or "New word")
        position = insertion
    elif action == "delete" and words:
        words.pop(position)
        position = min(position, max(0, len(words) - 1))
    elif action == "up" and position > 0:
        words[position - 1], words[position] = words[position], words[position - 1]
        position -= 1
    elif action == "down" and position + 1 < len(words):
        words[position + 1], words[position] = words[position], words[position + 1]
        position += 1
    return "\n".join(words), position + 1


def edit_hierarchy_json(text, path, action):
    roots = json.loads(text) if str(text or "").strip() else []
    if isinstance(roots, dict):
        roots = roots.get("nodes", [])
    if not isinstance(roots, list):
        raise ValueError("hierarchy must be a JSON array")
    indices = [int(part) for part in str(path or "").split("/") if part.strip()]

    def container_and_index():
        container = roots
        for part in indices[:-1]:
            node = container[part]
            container = node.setdefault("children", [])
        return container, (indices[-1] if indices else None)

    new_node = lambda: {
        "node_id": f"node_{uuid.uuid4().hex[:12]}", "name": "New Node",
        "words": ["word"], "content": "", "stages": [""], "children": [],
    }
    if action == "add_root":
        roots.append(new_node())
        indices = [len(roots) - 1]
    else:
        container, selected = container_and_index()
        if selected is None or selected < 0 or selected >= len(container):
            raise ValueError("select a valid hierarchy path such as 0 or 0/1")
        if action == "add_child":
            children = container[selected].setdefault("children", [])
            children.append(new_node())
            indices.append(len(children) - 1)
        elif action == "duplicate":
            clone = _copy(container[selected])

            def renew(node):
                node["node_id"] = f"node_{uuid.uuid4().hex[:12]}"
                node["name"] = str(node.get("name") or "Node") + " Copy"
                for child in node.get("children", []):
                    renew(child)

            renew(clone)
            container.insert(selected + 1, clone)
            indices[-1] = selected + 1
        elif action == "delete":
            container.pop(selected)
            indices[-1] = min(selected, max(0, len(container) - 1))
        elif action == "up" and selected > 0:
            container[selected - 1], container[selected] = container[selected], container[selected - 1]
            indices[-1] = selected - 1
        elif action == "down" and selected + 1 < len(container):
            container[selected + 1], container[selected] = container[selected], container[selected + 1]
            indices[-1] = selected + 1
    return json.dumps(roots, ensure_ascii=False, indent=2), "/".join(map(str, indices))


def build_lorebooks(
    gr: Any, *, load_config, save_books, save_assignments, import_book, delete_book_file,
    move_assignment_target, remove_assignment_target,
    remove_missing_file_reference, i18n=None,
):
    draft = gr.State({})
    tr = i18n or (lambda key: key)
    gr.Markdown("### Lorebooks")
    with gr.Row():
        active_preset = gr.Markdown("")
        refresh_button = gr.Button(tr("lorebook.refresh_books"), scale=1)
    status = gr.Markdown("")
    with gr.Accordion(tr("lorebook.preset_search_extensions"), open=False):
        character_context_search_enabled = gr.Checkbox(
            value=True, label=tr("lorebook.character_context_search_enabled"))
        previous_step_output_search_enabled = gr.Checkbox(
            value=True, label=tr("lorebook.previous_step_output_search_enabled"))
        gr.Markdown(tr("lorebook.preset_search_extensions_help"))
    with gr.Accordion(tr("global_settings"), open=False):
        with gr.Row():
            scan_depth = gr.Number(value=2, precision=0, minimum=1, maximum=1000, label=tr("scan_depth"))
            token_budget = gr.Number(value=2048, precision=0, minimum=0, maximum=131072, label=tr("token_budget"))
        with gr.Row():
            case_sensitive = gr.Checkbox(label=tr("case_sensitive"))
            whole_words = gr.Checkbox(label=tr("whole_words"))
            recursive = gr.Checkbox(label=tr("recursive"))

    with gr.Accordion(tr("agent_assignments"), open=False):
        gr.Markdown(tr("lorebook.assignment_help"))
        assignment_agent_ids = []
        assignment_rows = []
        assignment_labels = []
        assignment_dropdowns = []
        assignment_summaries = []
        with gr.Column(elem_classes="brain-lorebook-assignment-scroll"):
            for _ in range(MAX_ASSIGNMENT_AGENTS):
                assignment_agent_ids.append(gr.State(None))
                with gr.Group(
                    visible=False, elem_classes="brain-lorebook-agent-card",
                ) as assignment_row:
                    with gr.Row():
                        with gr.Column(
                            scale=2, min_width=240,
                            elem_classes="brain-lorebook-agent-info",
                        ):
                            agent_label = gr.Markdown(
                                "", elem_classes="brain-lorebook-agent-label",
                            )
                            summary = gr.Markdown(
                                "", elem_classes="brain-lorebook-active-books",
                            )
                        assigned = gr.Dropdown(
                            choices=[], multiselect=True, filterable=True,
                            label=tr("lorebook.available_books"), scale=3,
                            elem_classes="brain-lorebook-assignment-dropdown",
                        )
                assignment_rows.append(assignment_row)
                assignment_labels.append(agent_label)
                assignment_dropdowns.append(assigned)
                assignment_summaries.append(summary)

    apply_button = gr.Button(tr("lorebook.save_assignments"), variant="primary")

    with gr.Accordion(tr("lorebook.missing_and_errors"), open=False):
        missing_files_text = gr.Markdown("")
        with gr.Row():
            missing_file = gr.Dropdown(label=tr("lorebook.missing_files"))
            remove_missing_file_button = gr.Button(tr("lorebook.remove_missing_reference"))
        missing_targets_text = gr.Markdown("")
        with gr.Row():
            missing_target = gr.Dropdown(label=tr("lorebook.missing_targets"))
            destination = gr.Dropdown(label=tr("lorebook.destination"))
            move_target_button = gr.Button(tr("lorebook.move_assignment"))
            remove_target_button = gr.Button(tr("lorebook.remove_assignment"))
        book_errors_text = gr.Markdown("")

    with gr.Row():
        book = gr.Dropdown(label=tr("lorebook"), choices=[], scale=2)
        add_book_button = gr.Button(tr("add_book"))
        delete_book_button = gr.Button(tr("delete_book"))
        import_file = gr.File(label=tr("import_st_json"), file_types=[".json"], type="filepath")
        import_mode = gr.Radio(
            choices=[
                (tr("lorebook.import_as_new"), "new"),
                (tr("lorebook.import_as_restore"), "restore"),
            ], value="new", label=tr("lorebook.import_mode"))
    book_name = gr.Textbox(label=tr("lorebook_title"))
    with gr.Row():
        entry = gr.Dropdown(label=tr("entry"), choices=[], scale=2)
        add_entry_button = gr.Button(tr("add_entry"))
        delete_entry_button = gr.Button(tr("delete_entry"))

    with gr.Accordion(tr("lorebook.basic_information"), open=True):
        entry_name = gr.Textbox(label=tr("entry_title"))
        with gr.Row():
            enabled = gr.Checkbox(value=True, label=tr("enabled"))
            constant = gr.Checkbox(label=tr("always_active"))
            use_probability = gr.Checkbox(label=tr("use_probability"))
        with gr.Row():
            order = gr.Number(value=100, precision=0, label=tr("order"))
            probability = gr.Slider(0, 100, value=100, step=1, label=tr("probability"))
    with gr.Accordion(tr("lorebook.observation"), open=True):
        observation_mode = gr.Radio(
            choices=[
                (tr("lorebook.observation_standard"), "standard"),
                (tr("lorebook.observation_past_same_step"), "past_same_step_only"),
            ], value="standard", label=tr("lorebook.observation_mode"))
        observation_sources = gr.CheckboxGroup(
            choices=[
                (tr("lorebook.source_chat"), "chat"),
                (tr("lorebook.source_description"), "character.description"),
                (tr("lorebook.source_personality"), "character.personality"),
                (tr("lorebook.source_scenario"), "scenario"),
                (tr("lorebook.source_depth_prompt"), "character.depth_prompt"),
                (tr("lorebook.source_creator_notes"), "character.creator_notes"),
                (tr("lorebook.source_persona"), "user.persona"),
                (tr("lorebook.source_previous_steps"), "previous_steps"),
            ], label=tr("lorebook.observation_sources"))
    with gr.Accordion(tr("lorebook.scope"), open=False):
        entry_scan_depth = gr.Number(
            value=None, precision=0, minimum=1, maximum=1000,
            label=tr("entry_scan_depth"))
        gr.Markdown(tr("lorebook.scope_help"))
    with gr.Accordion(tr("lorebook.condition"), open=True):
        condition_mode = gr.Radio(
            choices=[
                (tr("lorebook.condition_normal"), "normal"),
                (tr("lorebook.condition_hierarchical"), "hierarchical"),
                (tr("lorebook.condition_ordered"), "ordered"),
            ], value="normal", label=tr("lorebook.condition_mode"))
        with gr.Row():
            keys = gr.Textbox(label=tr("primary_keys"), info=tr("comma_separated"))
            secondary_keys = gr.Textbox(label=tr("secondary_keys"), info=tr("comma_separated"))
        selective = gr.Checkbox(label=tr("use_secondary_keys"))
        ordered_words = gr.Textbox(
            label=tr("lorebook.ordered_words"), lines=5,
            info=tr("lorebook.ordered_words_help"))
        with gr.Row():
            ordered_index = gr.Number(value=1, precision=0, minimum=1,
                                      label=tr("lorebook.selected_position"))
            ordered_new_word = gr.Textbox(label=tr("lorebook.new_word"))
        with gr.Row():
            ordered_add = gr.Button(tr("lorebook.add_word"))
            ordered_up = gr.Button(tr("lorebook.move_up"))
            ordered_down = gr.Button(tr("lorebook.move_down"))
            ordered_delete = gr.Button(tr("lorebook.delete_word"))
        hierarchy_json = gr.Textbox(
            label=tr("lorebook.hierarchy_json"), lines=10,
            info=tr("lorebook.hierarchy_json_help"))
        hierarchy_path = gr.Textbox(value="0", label=tr("lorebook.hierarchy_path"))
        with gr.Row():
            hierarchy_add_root = gr.Button(tr("lorebook.add_root"))
            hierarchy_add_child = gr.Button(tr("lorebook.add_child"))
            hierarchy_duplicate = gr.Button(tr("lorebook.duplicate_node"))
        with gr.Row():
            hierarchy_up = gr.Button(tr("lorebook.move_up"))
            hierarchy_down = gr.Button(tr("lorebook.move_down"))
            hierarchy_delete = gr.Button(tr("lorebook.delete_node"))
    with gr.Accordion(tr("lorebook.injection_content"), open=True):
        content_mode = gr.Radio(
            choices=[
                (tr("lorebook.content_fixed"), "fixed"),
                (tr("lorebook.content_staged"), "staged"),
            ], value="fixed", label=tr("lorebook.content_mode"))
        content = gr.Textbox(label=tr("content"), lines=10, max_lines=24)
        stages = gr.Textbox(
            label=tr("lorebook.stages"), lines=10,
            info=tr("lorebook.stages_help"))
    with gr.Accordion(tr("lorebook.application_information"), open=False):
        gr.Markdown(tr("lorebook.application_information_help"))
    with gr.Accordion(tr("lorebook.behavior_preview"), open=True):
        behavior_preview = gr.Markdown("")
        validation_message = gr.Markdown("")
    save_entry_button = gr.Button(tr("lorebook.save_books"), variant="primary")
    def assignment_label(agent):
        name = agent.get("name") or agent["id"]
        step = agent.get("step")
        if isinstance(step, int):
            return f"{tr('step')} {step} · {name}"
        return f"{tr('writer')} · {name}"

    def assignment_summary(body, book_ids):
        selected = [html.escape(name) for name in assigned_book_names(body, book_ids)]
        listing = " / ".join(selected) if selected else tr("lorebook.none_assigned")
        return f"**{tr('lorebook.active_books')}:** {listing}"

    def assignment_control_values(body):
        books = assignment_book_choices(body)
        agents = list((body or {}).get("agents", []))[:MAX_ASSIGNMENT_AGENTS]
        agent_ids = []
        rows = []
        labels = []
        dropdowns = []
        summaries = []
        for index in range(MAX_ASSIGNMENT_AGENTS):
            if index < len(agents):
                agent = agents[index]
                agent_id = str(agent["id"])
                selected = body.get("assignments", {}).get(agent_id, [])
                agent_ids.append(agent_id)
                rows.append(gr.Group(visible=True))
                labels.append(f"#### {html.escape(assignment_label(agent))}")
                dropdowns.append(gr.Dropdown(
                    choices=books, value=selected, multiselect=True, filterable=True,
                ))
                summaries.append(assignment_summary(body, selected))
            else:
                agent_ids.append(None)
                rows.append(gr.Group(visible=False))
                labels.append("")
                dropdowns.append(gr.Dropdown(choices=books, value=[], multiselect=True))
                summaries.append("")
        return [*agent_ids, *rows, *labels, *dropdowns, *summaries]

    def issue_values(body):
        preset_name = html.escape(str(body.get("preset_name") or "Default"))
        preset = f"**{tr('lorebook.current_preset')}:** {preset_name}"
        missing = list(body.get("missing_files", []))
        missing_lines = (
            "\n".join(f"- <code>{html.escape(str(item))}</code>" for item in missing)
            if missing else tr("lorebook.no_missing_files")
        )
        targets = list(body.get("missing_targets", []))
        target_choices = []
        for item in targets:
            position = item.get("target_position")
            suffix = f" (Step {position})" if position not in (None, "") else ""
            label = f"{item.get('target_name') or item['target_id']}{suffix}"
            target_choices.append((label, item["target_id"]))
        target_lines = (
            "\n".join(
                f"- **{html.escape(str(item.get('target_name') or item['target_id']))}** "
                f"(<code>{html.escape(str(item['target_id']))}</code>)"
                for item in targets
            )
            if targets else tr("lorebook.no_missing_targets")
        )
        destinations = [
            (assignment_label(agent), str(agent["id"]))
            for agent in body.get("agents", [])
        ]
        errors = list(body.get("book_errors", []))
        settings_error = str(body.get("settings_error") or "").strip()
        error_lines = (
            "\n".join(
                f"- <code>{html.escape(str(item.get('file', '')))}</code>: "
                f"{html.escape(str(item.get('error', '')))}"
                for item in errors
            )
            if errors else tr("lorebook.no_invalid_files")
        )
        if settings_error:
            error_lines = (
                f"- **settings.json:** {html.escape(settings_error)}\n"
                + error_lines
            )
        return (
            preset,
            f"**{tr('lorebook.missing_files')}**\n\n{missing_lines}",
            gr.Dropdown(choices=[(item, item) for item in missing],
                        value=missing[0] if missing else None),
            f"**{tr('lorebook.missing_targets')}**\n\n{target_lines}",
            gr.Dropdown(choices=target_choices,
                        value=target_choices[0][1] if target_choices else None),
            gr.Dropdown(choices=destinations,
                        value=destinations[0][1] if destinations else None),
            f"**{tr('lorebook.invalid_files')}**\n\n{error_lines}",
        )

    def preview_value(body, book_id, observation, sources, condition, ordered, content_kind, name):
        source_text = (
            tr("lorebook.preview_past_same_step")
            if observation == "past_same_step_only"
            else ", ".join(sources or []) or tr("lorebook.preview_no_sources")
        )
        if condition == "ordered":
            condition_text = " → ".join(
                line.strip() for line in str(ordered or "").splitlines() if line.strip())
        elif condition == "hierarchical":
            condition_text = tr("lorebook.condition_hierarchical")
        else:
            condition_text = tr("lorebook.condition_normal")
        targets = []
        agents = {str(item["id"]): item for item in (body or {}).get("agents", [])}
        for agent_id, book_ids in (body or {}).get("assignments", {}).items():
            if book_id not in (book_ids or []):
                continue
            agent = agents.get(str(agent_id), {"name": agent_id})
            targets.append(str(agent.get("name") or agent_id))
        target_text = ", ".join(targets) or tr("lorebook.none_assigned")
        return (
            f"**{html.escape(str(name or tr('lorebook.entry')))}**  \n"
            f"{tr('lorebook.preview_sources')}: {html.escape(source_text)}  \n"
            f"{tr('lorebook.preview_condition')}: {html.escape(condition_text)}  \n"
            f"{tr('lorebook.preview_content')}: {html.escape(str(content_kind))}  \n"
            f"{tr('lorebook.preview_targets')}: {html.escape(target_text)}  \n"
            f"{tr('lorebook.preview_timing')}"
        )

    def validation_value(observation, sources, condition, ordered, hierarchy,
                         content_kind, staged, always_active):
        errors = []
        if observation == "standard" and not sources:
            errors.append(tr("lorebook.error_source_required"))
        if observation == "past_same_step_only" and always_active:
            errors.append(tr("lorebook.error_past_constant"))
        if condition in {"ordered", "hierarchical"} and always_active:
            errors.append(tr("lorebook.error_condition_constant"))
        if condition == "ordered" and len([
            line for line in str(ordered or "").splitlines() if line.strip()
        ]) < 2:
            errors.append(tr("lorebook.error_ordered_words"))
        if condition == "hierarchical":
            try:
                nodes = json.loads(hierarchy) if str(hierarchy).strip() else []
                if not nodes:
                    errors.append(tr("lorebook.error_hierarchy_required"))
            except json.JSONDecodeError:
                errors.append(tr("lorebook.error_hierarchy_json"))
        if content_kind == "staged" and not any(
            part.strip() for part in str(staged or "").split("\n---\n")):
            errors.append(tr("lorebook.error_stage_required"))
        if not errors:
            return f"✅ {tr('lorebook.validation_ok')}"
        return "\n".join(f"- ⚠️ {html.escape(error)}" for error in errors)

    def all_values(body, message, preferred_book=None, preferred_entry=None):
        books = book_choices(body)
        available_books = {value for _, value in books}
        book_id = preferred_book if preferred_book in available_books else (
            books[0][1] if books else None)
        entries = entry_choices(body, book_id)
        available_entries = {value for _, value in entries}
        entry_id = preferred_entry if preferred_entry in available_entries else (
            entries[0][1] if entries else None)
        settings = body.get("settings", {})
        return (
            body, *issue_values(body),
            body.get("character_context_search_enabled", True),
            body.get("previous_step_output_search_enabled", True),
            gr.Dropdown(choices=books, value=book_id),
            gr.Dropdown(choices=entries, value=entry_id),
            *entry_values(body, book_id, entry_id),
            *assignment_control_values(body),
            settings.get("scan_depth", 2), settings.get("token_budget", 2048),
            settings.get("case_sensitive", False), settings.get("match_whole_words", False),
            settings.get("recursive", False), message,
        )

    def load_all():
        return all_values(load_config(), tr("lorebook.loaded_status"))

    def refresh_assignments(body):
        fresh = load_config()
        if body and body.get("preset_id") == fresh.get("preset_id"):
            updated = _copy(body)
        else:
            updated = _copy(fresh)
        updated["preset_id"] = fresh.get("preset_id")
        updated["preset_name"] = fresh.get("preset_name")
        updated["agents"] = fresh.get("agents", [])
        valid_agents = {str(agent["id"]) for agent in updated["agents"]}
        updated["assignments"] = {
            str(agent_id): list(book_ids)
            for agent_id, book_ids in updated.get("assignments", {}).items()
            if str(agent_id) in valid_agents
        }
        return updated, *assignment_control_values(updated)

    def entry_values(body, book_id, entry_id):
        selected_book = find_book(body, book_id) or {}
        selected = find_entry(body, book_id, entry_id) or {}
        condition_data = selected.get("condition_data") or {}
        ordered = "\n".join(condition_data.get("words", []))
        nodes = condition_data.get("nodes", [])
        hierarchy = json.dumps(nodes, ensure_ascii=False, indent=2) if nodes else ""
        staged = "\n---\n".join(selected.get("stages", []))
        preview = preview_value(
            body, book_id,
            selected.get("observation_mode", "standard"),
            selected.get("observation_sources", []),
            selected.get("condition_mode", "normal"), ordered,
            selected.get("content_mode", "fixed"), selected.get("name", ""),
        )
        validation = validation_value(
            selected.get("observation_mode", "standard"),
            selected.get("observation_sources", []),
            selected.get("condition_mode", "normal"), ordered, hierarchy,
            selected.get("content_mode", "fixed"), staged,
            selected.get("constant", False),
        )
        return (
            selected_book.get("name", ""), selected.get("name", ""),
            selected.get("enabled", True), selected.get("constant", False),
            selected.get("use_probability", False), selected.get("order", 100),
            selected.get("probability", 100),
            selected.get("observation_mode", "standard"),
            selected.get("observation_sources", []), selected.get("scan_depth"),
            selected.get("condition_mode", "normal"),
            ", ".join(selected.get("keys", [])),
            ", ".join(selected.get("secondary_keys", [])),
            selected.get("selective", False), ordered, hierarchy,
            selected.get("content_mode", "fixed"), selected.get("content", ""),
            staged, preview, validation,
        )

    editor_outputs = [
        book_name, entry_name, enabled, constant, use_probability, order, probability,
        observation_mode, observation_sources, entry_scan_depth, condition_mode,
        keys, secondary_keys, selective, ordered_words, hierarchy_json,
        content_mode, content, stages, behavior_preview, validation_message,
    ]
    editor_inputs = editor_outputs[:-2]

    def select_book(body, book_id):
        entries = entry_choices(body, book_id)
        entry_id = entries[0][1] if entries else None
        return gr.Dropdown(choices=entries, value=entry_id), *entry_values(body, book_id, entry_id)

    def select_entry(body, book_id, entry_id):
        return entry_values(body, book_id, entry_id)

    def save_book_settings(body, book_id, entry_id, bname, *values):
        editor_values = values[:18]
        depth, budget, case, whole, recurse = values[18:]
        try:
            updated = update_book_name(body, book_id, bname)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc
        if entry_id:
            (name, enabled_value, constant_value, probability_enabled, order_value,
             probability_value, observation_value, sources_value, entry_depth,
             condition_value, primary_value, secondary_value, selective_value,
             ordered_value, hierarchy_value, content_mode_value, content_value,
             stages_value) = editor_values
            try:
                hierarchy_nodes = json.loads(hierarchy_value) if str(hierarchy_value).strip() else []
            except json.JSONDecodeError as exc:
                raise gr.Error(f"Hierarchy JSON: {exc.msg}") from exc
            if isinstance(hierarchy_nodes, dict):
                hierarchy_nodes = hierarchy_nodes.get("nodes", [])
            ordered_list = [
                line.strip() for line in str(ordered_value or "").splitlines()
                if line.strip()
            ]
            stage_list = str(stages_value or "").split("\n---\n")
            names = (
                "name", "enabled", "constant", "use_probability", "order",
                "probability", "observation_mode", "observation_sources",
                "scan_depth", "condition_mode", "keys", "secondary_keys",
                "selective", "condition_data", "content_mode", "content", "stages",
            )
            prepared_values = (
                name, enabled_value, constant_value, probability_enabled, order_value,
                probability_value, observation_value, sources_value, entry_depth,
                condition_value, primary_value, secondary_value, selective_value,
                ({"words": ordered_list} if condition_value == "ordered" else
                 {"nodes": hierarchy_nodes} if condition_value == "hierarchical" else {}),
                content_mode_value, content_value, stage_list,
            )
            try:
                updated = update_entry(
                    updated, book_id, entry_id, **dict(zip(names, prepared_values)))
            except ValueError as exc:
                raise gr.Error(str(exc)) from exc
        updated["settings"] = {
            **updated.get("settings", {}), "scan_depth": int(depth),
            "token_budget": int(budget), "case_sensitive": bool(case),
            "match_whole_words": bool(whole), "recursive": bool(recurse),
        }
        preserved_assignments = _copy(updated.get("assignments", {}))
        saved = save_books(updated)
        book_id_map = saved.pop("book_id_map", {})
        saved["assignments"] = remap_assignments(preserved_assignments, book_id_map)
        return all_values(
            saved, tr("lorebook.books_saved_status"),
            book_id_map.get(str(book_id), str(book_id)), entry_id)

    def create_book(body):
        updated, book_id = add_book(body)
        return updated, gr.Dropdown(choices=book_choices(updated), value=book_id), gr.Dropdown(choices=[], value=None), *entry_values(updated, book_id, None)

    def remove_book_file(book_id):
        if not book_id:
            raise gr.Error("Select a lorebook first")
        delete_book_file(book_id)
        return load_all()

    def create_entry(body, book_id, bname):
        try:
            updated = update_book_name(body, book_id, bname)
            updated, entry_id = add_entry(updated, book_id)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc
        return updated, gr.Dropdown(choices=entry_choices(updated, book_id), value=entry_id), *entry_values(updated, book_id, entry_id)

    def remove_entry(body, book_id, entry_id):
        updated = delete_entry(body, book_id, entry_id)
        choices = entry_choices(updated, book_id)
        selected = choices[0][1] if choices else None
        return updated, gr.Dropdown(choices=choices, value=selected), *entry_values(updated, book_id, selected)

    def commit_assignment(body, agent_id, book_ids):
        updated = _copy(body)
        assignments = updated.setdefault("assignments", {})
        if agent_id and book_ids:
            assignments[str(agent_id)] = list(book_ids)
        elif agent_id:
            assignments.pop(str(agent_id), None)
        return updated, assignment_summary(updated, book_ids)

    def save_assignment_settings(body, character_enabled, previous_enabled, *assignment_values):
        agent_ids = assignment_values[:MAX_ASSIGNMENT_AGENTS]
        selections = assignment_values[MAX_ASSIGNMENT_AGENTS:]
        updated = update_assignments(body, agent_ids, selections)
        updated["character_context_search_enabled"] = bool(character_enabled)
        updated["previous_step_output_search_enabled"] = bool(previous_enabled)
        try:
            saved = save_assignments(updated)
        except ValueError as exc:
            message = str(exc)
            if "Save new lorebooks" in message:
                message = tr("lorebook.save_books_first")
            raise gr.Error(message) from exc
        updated["assignments"] = _copy(saved.get("assignments", {}))
        return updated, tr("lorebook.assignments_saved_status")

    def import_json(path, import_mode):
        if not path:
            return load_all()
        if os.path.getsize(path) > 8 * 1024 * 1024:
            raise gr.Error("Lorebook file must be 8 MB or smaller")
        with open(path, "r", encoding="utf-8-sig") as handle:
            import_book(json.load(handle), os.path.basename(path), import_mode)
        return load_all()

    def move_missing_target(source_id, destination_id):
        if not source_id or not destination_id:
            raise gr.Error("Select both the missing target and its destination")
        move_assignment_target(source_id, destination_id)
        return load_all()

    def remove_missing_target(target_id):
        if not target_id:
            raise gr.Error("Select a missing target first")
        remove_assignment_target(target_id)
        return load_all()

    def remove_missing_reference(filename):
        if not filename:
            raise gr.Error("Select a missing file first")
        remove_missing_file_reference(filename)
        return load_all()

    book.input(select_book, [draft, book], [entry, *editor_outputs], show_progress="hidden")
    entry.input(select_entry, [draft, book, entry], editor_outputs, show_progress="hidden")
    assignment_outputs = [
        *assignment_agent_ids, *assignment_rows, *assignment_labels,
        *assignment_dropdowns, *assignment_summaries,
    ]
    issue_outputs = [
        active_preset, missing_files_text, missing_file,
        missing_targets_text, missing_target, destination, book_errors_text,
    ]
    load_outputs = [
        draft, *issue_outputs,
        character_context_search_enabled, previous_step_output_search_enabled,
        book, entry, *editor_outputs, *assignment_outputs,
        scan_depth, token_budget, case_sensitive, whole_words, recursive, status,
    ]

    preview_inputs = [
        draft, book, observation_mode, observation_sources, condition_mode, ordered_words,
        content_mode, entry_name,
    ]
    for component in preview_inputs[1:]:
        component.input(
            preview_value, preview_inputs, behavior_preview, show_progress="hidden")
    validation_inputs = [
        observation_mode, observation_sources, condition_mode, ordered_words,
        hierarchy_json, content_mode, stages, constant,
    ]
    for component in validation_inputs:
        component.input(
            validation_value, validation_inputs, validation_message,
            show_progress="hidden")

    def ordered_action(text, index, word, action):
        return edit_ordered_words(text, index, action, word)

    for button, action in (
        (ordered_add, "add"), (ordered_up, "up"),
        (ordered_down, "down"), (ordered_delete, "delete"),
    ):
        button.click(
            lambda text, index, word, selected_action=action: ordered_action(
                text, index, word, selected_action),
            [ordered_words, ordered_index, ordered_new_word],
            [ordered_words, ordered_index], show_progress="hidden")

    def hierarchy_action(text, path, action):
        try:
            return edit_hierarchy_json(text, path, action)
        except (ValueError, IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise gr.Error(str(exc)) from exc

    for button, action in (
        (hierarchy_add_root, "add_root"), (hierarchy_add_child, "add_child"),
        (hierarchy_duplicate, "duplicate"), (hierarchy_up, "up"),
        (hierarchy_down, "down"), (hierarchy_delete, "delete"),
    ):
        button.click(
            lambda text, path, selected_action=action: hierarchy_action(
                text, path, selected_action),
            [hierarchy_json, hierarchy_path], [hierarchy_json, hierarchy_path],
            show_progress="hidden")

    save_entry_event = save_entry_button.click(
        save_book_settings,
        [draft, book, entry, *editor_inputs, scan_depth, token_budget,
         case_sensitive, whole_words, recursive],
        load_outputs)
    add_book_event = add_book_button.click(
        create_book, draft, [draft, book, entry, *editor_outputs])
    delete_book_event = delete_book_button.click(
        remove_book_file, book, load_outputs)
    add_entry_button.click(
        create_entry, [draft, book, book_name], [draft, entry, *editor_outputs])
    delete_entry_button.click(remove_entry, [draft, book, entry], [draft, entry, *editor_outputs])
    for agent_id, assigned, summary in zip(
        assignment_agent_ids, assignment_dropdowns, assignment_summaries,
    ):
        assigned.input(
            commit_assignment, [draft, agent_id, assigned], [draft, summary],
            show_progress="hidden",
        )
    apply_button.click(
        save_assignment_settings,
        [draft, character_context_search_enabled, previous_step_output_search_enabled,
         *assignment_agent_ids, *assignment_dropdowns],
        [draft, status],
    )
    import_event = import_file.change(import_json, [import_file, import_mode], load_outputs)
    refresh_event = refresh_button.click(load_all, None, load_outputs)
    move_event = move_target_button.click(
        move_missing_target, [missing_target, destination], load_outputs)
    remove_target_event = remove_target_button.click(
        remove_missing_target, missing_target, load_outputs)
    remove_missing_event = remove_missing_file_button.click(
        remove_missing_reference, missing_file, load_outputs)

    for event in (add_book_event,):
        event.then(
            assignment_control_values, draft, assignment_outputs,
            show_progress="hidden",
        )

    return {
        "load": load_all,
        "load_outputs": load_outputs,
        "full_refresh": load_all,
        "full_refresh_outputs": load_outputs,
        "refresh": refresh_assignments,
        "refresh_inputs": [draft],
        "assignment_outputs": [draft, *assignment_outputs],
        "events": [
            refresh_event, delete_book_event, import_event, move_event,
            remove_target_event, remove_missing_event,
        ],
    }
