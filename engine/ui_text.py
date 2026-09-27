"""CSV-backed localization for UI-owned BrainEngine text."""
from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import Final

LOCALES_DIR: Final = Path(__file__).with_name("locales")
LANGUAGES_FILE: Final = LOCALES_DIR / "languages.csv"
TRANSLATIONS_FILE: Final = LOCALES_DIR / "translations.csv"

# Existing component keys remain valid while the CSV uses stable namespaced keys.
KEY_ALIASES: Final = {
    "language": "common.language", "dashboard": "nav.dashboard", "providers": "nav.providers",
    "prompt_studio": "nav.prompt_studio", "lorebooks": "nav.lorebooks",
    "prompt_assistant": "nav.prompt_assistant", "save": "common.save", "saved": "common.saved",
    "cancel": "common.cancel", "add": "common.add", "delete": "common.delete",
    "import": "common.import", "export": "common.export", "refresh": "common.refresh",
    "clear": "common.clear", "enabled": "common.enabled", "name": "common.name",
    "title_label": "common.title", "prompt_label": "common.prompt", "content": "common.content",
    "error": "common.error", "no_selection": "common.no_selection",
    "unsaved_changes": "common.unsaved_changes", "not_configured": "common.not_configured",
    "server_status": "dashboard.server_status", "running": "dashboard.running",
    "stopped": "dashboard.stopped", "dashboard_note": "dashboard.web_ui_note",
    "multibrain_api": "dashboard.multibrain_api", "main_provider_api": "dashboard.main_provider_api",
    "background_provider_api": "dashboard.background_provider_api",
    "main_provider": "provider.main_provider", "background_provider": "provider.background_provider",
    "base_url": "provider.base_url", "api_key": "provider.api_key", "model": "provider.model",
    "test_connection": "provider.test_connection", "connection_ok": "provider.connection_ok",
    "connection_failed": "provider.connection_failed", "saved_presets": "prompt_studio.saved_presets",
    "preset_name": "prompt_studio.preset_name", "save_preset": "prompt_studio.save_preset",
    "export_csv": "prompt_studio.export_csv", "import_csv": "prompt_studio.import_csv",
    "debug_traces": "prompt_studio.debug_traces", "save_settings": "prompt_studio.save_settings",
    "exported_preset": "prompt_studio.exported_preset", "reasoning_steps": "prompt_studio.reasoning_steps",
    "summarization": "prompt_studio.summarization", "group_chat": "prompt_studio.group_chat",
    "group_prompt": "prompt_studio.group_prompt", "temperature": "prompt_studio.temperature",
    "frequency_penalty": "prompt_studio.frequency_penalty", "presence_penalty": "prompt_studio.presence_penalty",
    "repetition_penalty": "prompt_studio.repetition_penalty", "repetition_range": "prompt_studio.repetition_range",
    "add_step": "prompt_studio.add_step", "delete_step": "prompt_studio.delete_step",
    "prompt_studio_help": "prompt_studio.help", "global_settings": "lorebook.global_settings",
    "scan_depth": "lorebook.scan_depth", "token_budget": "lorebook.token_budget",
    "case_sensitive": "lorebook.case_sensitive", "whole_words": "lorebook.whole_words",
    "recursive": "lorebook.recursive", "lorebook": "lorebook.book", "add_book": "lorebook.add_book",
    "delete_book": "lorebook.delete_book", "import_st_json": "lorebook.import_st_json",
    "lorebook_title": "lorebook.book_title", "entry": "lorebook.entry", "add_entry": "lorebook.add_entry",
    "delete_entry": "lorebook.delete_entry", "entry_title": "lorebook.entry_title",
    "primary_keys": "lorebook.primary_keys", "secondary_keys": "lorebook.secondary_keys",
    "comma_separated": "lorebook.comma_separated", "always_active": "lorebook.always_active",
    "use_secondary_keys": "lorebook.use_secondary_keys", "use_probability": "lorebook.use_probability",
    "order": "lorebook.order", "probability": "lorebook.probability",
    "entry_scan_depth": "lorebook.entry_scan_depth", "update_draft": "lorebook.update_draft",
    "agent_assignments": "lorebook.agent_assignments", "assigned_lorebooks": "lorebook.assigned_books",
    "update_assignment": "lorebook.update_assignment", "apply_lorebooks": "lorebook.apply_settings",
    "temporary_orders": "prompt_assistant.temporary_orders", "agent": "prompt_assistant.agent",
    "order_label": "prompt_assistant.order", "apply_order": "prompt_assistant.apply_order",
    "clear_order": "prompt_assistant.clear_order", "message": "prompt_assistant.message",
    "message_placeholder": "prompt_assistant.message_placeholder", "send": "prompt_assistant.send",
    "max_tokens": "prompt_assistant.max_tokens", "clear_chat": "prompt_assistant.clear_chat",
}


def _load_catalog():
    with LANGUAGES_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        language_rows = list(csv.DictReader(handle))
    languages = {
        row["code"].strip(): row for row in language_rows
        if row.get("code", "").strip() and row.get("enabled", "").strip().lower() in {"1", "true", "yes"}
    }
    if not languages:
        raise ValueError("languages.csv does not contain an enabled language")
    with TRANSLATIONS_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    catalog = {code: {} for code in languages}
    for row in rows:
        key = (row.get("key") or "").strip()
        if not key:
            continue
        for code, metadata in languages.items():
            value = row.get(code) or ""
            fallback = (metadata.get("fallback") or "").strip()
            if not value and fallback:
                value = row.get(fallback) or ""
            catalog[code][key] = value or row.get("en") or key
    for code in catalog:
        catalog[code].update(app_title="Step-driven MultiBrainEngine", writer="Writer", step="Step")
        for alias, canonical in KEY_ALIASES.items():
            catalog[code][alias] = catalog[code].get(canonical, canonical)
    return languages, catalog


LANGUAGE_METADATA, UI_TEXT = _load_catalog()
DEFAULT_LANGUAGE: Final = "en" if "en" in UI_TEXT else next(iter(UI_TEXT))
SUPPORTED_LANGUAGES: Final = tuple(UI_TEXT)
LANGUAGE_CHOICES: Final = tuple(
    (row.get("display_name") or code, code) for code, row in LANGUAGE_METADATA.items()
)


def normalize_language(language: object = None) -> str:
    if isinstance(language, str):
        candidates: Iterable[object] = language.split(",")
    elif isinstance(language, Iterable):
        candidates = language
    else:
        return DEFAULT_LANGUAGE
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        primary = candidate.split(";", 1)[0].strip().lower().replace("_", "-").split("-", 1)[0]
        if primary in SUPPORTED_LANGUAGES:
            return primary
    return DEFAULT_LANGUAGE


def t(key: str, language: object = None, **values: object) -> str:
    lang = normalize_language(language)
    canonical = KEY_ALIASES.get(key, key)
    template = UI_TEXT.get(lang, {}).get(canonical)
    if template is None:
        template = UI_TEXT[DEFAULT_LANGUAGE].get(canonical, key)
    return template.format_map(values) if values else template
