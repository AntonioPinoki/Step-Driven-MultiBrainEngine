"""Gradio Prompt Studio built on top of :mod:`prompt_store`.

The module deliberately imports Gradio only inside ``build_prompt_studio`` so
the server and the prompt persistence layer remain usable without the optional
web UI dependency.  Callback helpers are public to keep the fairly large form
easy to test without launching a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import tempfile
import uuid
import math
import json
import copy
import bundle_store
from step_inputs import step_settings, normalize_references
from typing import Any, Callable, Iterable

try:
    from . import prompt_store
except ImportError:  # Support ``engine`` being placed directly on sys.path.
    import prompt_store


MAX_STEPS = 23
SAMPLING_FIELDS = (
    "temperature",
    "frequency_penalty",
    "presence_penalty",
    "repetition_penalty",
    "repetition_penalty_range",
)
STEP_WIDTH = 12 + len(SAMPLING_FIELDS)  # enabled, id, name, number, sampling, prompt
FIXED_WIDTH = 2 + len(SAMPLING_FIELDS)  # name, sampling, prompt


def _sampling(item: dict[str, Any]) -> list[Any]:
    return [item.get(field) for field in SAMPLING_FIELDS]


def input_form_values(item):
    settings = step_settings(item)
    ref = settings["prior_output_reference"]
    return [ref["mode"], ref["step_ids"], settings["inject_prior_outputs"],
            settings["inject_past_same_step_outputs"], settings["exclude_chat_log"],
            settings["max_output_tokens"], settings["variable_update_prompt"]]


def normalize_form_references(values):
    values = list(values)
    steps = [{"id": values[i * STEP_WIDTH + 1], "name": values[i * STEP_WIDTH + 2],
              "step": values[i * STEP_WIDTH + 3],
              "prior_output_reference": {"mode": values[i * STEP_WIDTH + 10],
                                         "step_ids": values[i * STEP_WIDTH + 11] or []}}
             for i in range(MAX_STEPS) if values[i * STEP_WIDTH]]
    clean = {item["id"]: item for item in normalize_references(steps, structural_edit=True)}
    for i in range(MAX_STEPS):
        if values[i * STEP_WIDTH]:
            ref = clean[values[i * STEP_WIDTH + 1]]["prior_output_reference"]
            values[i * STEP_WIDTH + 10:i * STEP_WIDTH + 12] = [ref["mode"], ref["step_ids"]]
    return values


def config_to_form(config: dict[str, Any]) -> tuple[Any, ...]:
    """Convert a prompt config into the fixed 23-slot Gradio form values."""
    by_number = {int(item["step"]): item for item in config.get("steps", [])}
    values: list[Any] = []
    for number in range(1, MAX_STEPS + 1):
        item = by_number.get(number)
        if item:
            values.extend([True, item["id"], item["name"], number, *_sampling(item), item["prompt"], *input_form_values(item)])
        else:
            values.extend([
                False, f"step_{number}", f"Step {number}", number,
                prompt_store.DEFAULT_STEP_TEMPERATURE,
                prompt_store.DEFAULT_FREQUENCY_PENALTY,
                prompt_store.DEFAULT_PRESENCE_PENALTY,
                prompt_store.DEFAULT_REPETITION_PENALTY,
                prompt_store.DEFAULT_REPETITION_PENALTY_RANGE, "",
                *input_form_values({}),
            ])
    writer = config["writer"]
    summary = config["summary"]
    values.extend([writer.get("name", "Writer"), *_sampling(writer), writer["prompt"]])
    values.extend([summary.get("name", "Summarize"), *_sampling(summary), summary["prompt"]])
    values.append(config.get("group_prompt", ""))
    values.append(json.dumps(config.get("variables", []), ensure_ascii=False, indent=2))
    return tuple(values)


def form_to_config(values: Iterable[Any]) -> dict[str, Any]:
    """Convert fixed form values to a config and validate it via prompt_store."""
    values = list(values)
    expected = MAX_STEPS * STEP_WIDTH + FIXED_WIDTH * 2 + 2
    if len(values) != expected:
        raise ValueError(f"Prompt Studio expected {expected} values, received {len(values)}")
    cursor = 0
    steps = []
    for _ in range(MAX_STEPS):
        enabled, slot_id, name, number = values[cursor:cursor + 4]
        cursor += 4
        sampling = dict(zip(SAMPLING_FIELDS, values[cursor:cursor + len(SAMPLING_FIELDS)]))
        cursor += len(SAMPLING_FIELDS)
        prompt = values[cursor]
        cursor += 1
        mode, ids, inject_prior, inject_past, exclude_chat, limit, update_prompt = values[cursor:cursor + 7]
        cursor += 7
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(limit) or int(limit) != limit:
                raise ValueError("Token limit must be a finite integer")
            limit = int(limit)
        if enabled:
            steps.append({
                "id": slot_id, "name": name, "step": number, "prompt": prompt,
                **sampling,
                "prior_output_reference": {"mode": mode, "step_ids": ids or []},
                "inject_prior_outputs": inject_prior,
                "inject_past_same_step_outputs": inject_past,
                "exclude_chat_log": exclude_chat, "max_output_tokens": limit,
                "variable_update_prompt": update_prompt,
            })

    def fixed(slot_id: str) -> dict[str, Any]:
        nonlocal cursor
        name = values[cursor]
        cursor += 1
        sampling = dict(zip(SAMPLING_FIELDS, values[cursor:cursor + len(SAMPLING_FIELDS)]))
        cursor += len(SAMPLING_FIELDS)
        prompt = values[cursor]
        cursor += 1
        return {"id": slot_id, "name": name, "prompt": prompt, **sampling}

    writer = fixed("writer")
    summary = fixed("summary")
    config = prompt_store.validate_config(steps, writer, summary, values[cursor],
        variables=json.loads(values[cursor + 1] or "[]"))
    config.pop("preset_id", None)
    config.pop("preset_name", None)
    return config


def step_number_choices(enabled_values: Iterable[Any], number_values: Iterable[Any]):
    """Return the existing step numbers as choices for each enabled slot."""
    enabled = [bool(value) for value in enabled_values]
    numbers = [int(value) if value not in (None, "") else index + 1
               for index, value in enumerate(number_values)]
    used = sorted({number for active, number in zip(enabled, numbers) if active})
    return [
        used if active else [current]
        for active, current in zip(enabled, numbers)
    ]


def step_ui_state(enabled_values: Iterable[Any], number_values: Iterable[Any]):
    """Return the visibility and number choices for every fixed step slot."""
    enabled = [bool(value) for value in enabled_values]
    numbers = [int(value) if value not in (None, "") else index + 1
               for index, value in enumerate(number_values)]
    return enabled, numbers, step_number_choices(enabled, numbers)


def swap_step_values(
    form_values: Iterable[Any], previous_numbers: Iterable[Any], source_slot: int,
):
    """Swap one step's settings with an existing numbered step, preserving positions."""
    values = list(form_values)
    numbers = [int(value) for value in previous_numbers]
    enabled = [bool(values[index * STEP_WIDTH]) for index in range(MAX_STEPS)]
    source_number_index = source_slot * STEP_WIDTH + 3
    source_number = numbers[source_slot]
    target_number = int(values[source_number_index])

    if target_number != source_number:
        target_slot = next((
            index for index, (active, number) in enumerate(zip(enabled, numbers))
            if active and number == target_number
        ), None)
        if target_slot is not None:
            # ID moves with the prompt/settings so lorebook assignments follow it.
            swappable_offsets = [1, 2, *range(4, STEP_WIDTH)]
            for offset in swappable_offsets:
                source_index = source_slot * STEP_WIDTH + offset
                target_index = target_slot * STEP_WIDTH + offset
                values[source_index], values[target_index] = (
                    values[target_index], values[source_index])

    choices = step_number_choices(enabled, numbers)
    for index in range(MAX_STEPS):
        number_index = index * STEP_WIDTH + 3
        values[number_index] = numbers[index]
    return normalize_form_references(values), numbers, choices


@dataclass(frozen=True)
class PresetDropdownState:
    """Gradio-neutral description of a saved-preset dropdown update."""

    choices: list[tuple[str, str]]
    value: str


class PromptStudioCallbacks:
    """Persistence callbacks shared by Gradio and unit tests."""

    def __init__(
        self,
        default_steps: list[dict[str, Any]],
        default_writer: dict[str, Any],
        default_summary: dict[str, Any],
        get_debug: Callable[[], bool] | None = None,
        set_debug: Callable[[bool], Any] | None = None,
        ensure_profile: Callable[..., Any] | None = None,
        rename_profile: Callable[[str, str], Any] | None = None,
        active_preset_text: str = "Active preset",
        default_group_prompt: str = "",
    ) -> None:
        self.default_steps = default_steps
        self.default_writer = default_writer
        self.default_summary = default_summary
        self.get_debug = get_debug or (lambda: False)
        self.set_debug = set_debug or (lambda enabled: enabled)
        self.ensure_profile = ensure_profile or (lambda *args, **kwargs: None)
        self.rename_profile = rename_profile or (lambda *args, **kwargs: None)
        self.active_preset_text = active_preset_text
        self.default_group_prompt = default_group_prompt

    def load(self) -> tuple[Any, ...]:
        return config_to_form(prompt_store.load_available_config(
            self.default_steps, self.default_writer, self.default_summary,
            self.default_group_prompt))

    def save(self, *values: Any) -> tuple[Any, ...]:
        config = form_to_config(values)
        saved = prompt_store.save_active_preset_config(**config)
        return ("Saved Prompt Studio settings and active preset CSV.", *config_to_form(saved))

    def presets(self) -> list[tuple[str, str]]:
        return [(item["name"], item["filename"]) for item in prompt_store.list_presets()]

    def active_filename(self, config: dict[str, Any]) -> str | None:
        return next((
            item["filename"] for item in prompt_store.list_presets()
            if item.get("preset_id") == config.get("preset_id")
        ), None)

    def require_unused_name(self, name: str) -> str:
        return prompt_store.require_unused_preset_name(name)

    def unique_import_name(self, name: str) -> str:
        return prompt_store.unique_preset_name(name)

    def save_preset(self, name: str, *values: Any) -> tuple[str, PresetDropdownState, str]:
        """Save As: create a new preset identity and clone the active lore profile."""
        name = self.require_unused_name(name)
        source = prompt_store.load_config(
            self.default_steps, self.default_writer, self.default_summary)
        config = form_to_config(values)
        result = prompt_store.save_preset(name, **config)
        config["preset_id"] = result["preset_id"]
        config["preset_name"] = result["preset_name"]
        prompt_store.save_config(**config)
        self.ensure_profile(
            result["preset_id"], result["preset_name"],
            copy_from=source["preset_id"])
        return (
            f"Saved preset: {result['filename']}",
            PresetDropdownState(self.presets(), result["filename"]),
            self.active_label(config),
        )

    def load_preset(self, filename: str, profile_mode: str = "copy") -> tuple[Any, ...]:
        source = prompt_store.load_config(
            self.default_steps, self.default_writer, self.default_summary)
        config = prompt_store.load_preset_file(
            filename, self.default_writer, self.default_summary)
        self.ensure_profile(
            config["preset_id"], config["preset_name"],
            copy_from=source["preset_id"] if profile_mode == "copy" else None)
        return (
            f"Loaded preset: {filename}", self.active_label(config),
            *config_to_form(config))

    def new_preset(self, name: str) -> tuple[Any, ...]:
        display_name = (
            self.require_unused_name(name)
            if str(name or "").strip()
            else self.unique_import_name("New preset")
        )
        config = prompt_store.validate_config(
            self.default_steps, self.default_writer, self.default_summary,
            self.default_group_prompt,
            preset_name=display_name)
        config.pop("preset_id", None)
        config.pop("preset_name", None)
        result = prompt_store.save_preset(display_name, **config)
        config["preset_id"] = result["preset_id"]
        config["preset_name"] = result["preset_name"]
        active = prompt_store.save_config(**config)
        self.ensure_profile(active["preset_id"], active["preset_name"])
        return (
            f"Created preset: {result['filename']}",
            PresetDropdownState(self.presets(), result["filename"]),
            self.active_label(active), *config_to_form(active))

    def rename_preset(self, filename: str, name: str) -> tuple[Any, ...]:
        result = prompt_store.rename_preset(
            filename, name, self.default_writer, self.default_summary)
        self.rename_profile(result["preset_id"], result["preset_name"])
        return (
            f"Renamed preset: {result['filename']}",
            PresetDropdownState(self.presets(), result["filename"]),
            self.active_label(result["config"]), *config_to_form(result["config"]))

    def active_label(self, config: dict[str, Any]) -> str:
        return f"**{self.active_preset_text}:** {config.get('preset_name', 'Default')}"

    def import_preset(self, file_value: Any, profile_mode: str = "copy") -> tuple[Any, ...]:
        source = prompt_store.load_config(
            self.default_steps, self.default_writer, self.default_summary)
        path = getattr(file_value, "name", file_value)
        if not path:
            raise ValueError("Choose a CSV preset to import")
        if os.path.getsize(path) > 1_000_000:
            raise ValueError("CSV file is too large")
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            csv_text = handle.read()
        name = os.path.splitext(os.path.basename(path))[0]
        name = self.unique_import_name(name)
        config = prompt_store.csv_to_config(
            csv_text, self.default_writer, self.default_summary, preset_name=name)
        config["preset_id"] = prompt_store.distinct_preset_id(
            config["preset_id"], [source["preset_id"]])
        result = prompt_store.save_preset(name, **config)
        config["preset_name"] = result["preset_name"]
        prompt_store.save_config(**config)
        self.ensure_profile(
            config["preset_id"], config["preset_name"],
            copy_from=source["preset_id"] if profile_mode == "copy" else None)
        return (
            f"Imported preset: {result['filename']}",
            PresetDropdownState(self.presets(), result["filename"]),
            self.active_label(config),
            *config_to_form(config),
        )

    def export_preset(self, name: str, *values: Any) -> tuple[str, str]:
        config = form_to_config(values)
        result = prompt_store.export_preset(name, **config)
        export_dir = os.path.join(tempfile.gettempdir(), "brainengine_exports")
        os.makedirs(export_dir, exist_ok=True)
        path = os.path.join(export_dir, result["filename"])
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write(result["csv"])
        return f"Export ready: {result['filename']}", path

    def debug_state(self) -> bool:
        return bool(self.get_debug())

    def toggle_debug(self, enabled: bool) -> tuple[bool, str]:
        result = self.set_debug(bool(enabled))
        if isinstance(result, dict):
            enabled = bool(result.get("enabled", enabled))
        elif isinstance(result, bool):
            enabled = result
        return enabled, f"Debug mode {'enabled' if enabled else 'disabled'}."


@dataclass
class PromptStudioUI:
    callbacks: PromptStudioCallbacks
    form_inputs: list[Any]
    form_outputs: list[Any]
    status: Any
    preset: Any
    debug: Any
    save_event: Any
    refresh_step_ui: Callable[..., list[Any]]
    step_state_inputs: list[Any]
    step_ui_outputs: list[Any]
    identity_events: list[Any]
    load_form: Callable[..., list[Any]]
    load_outputs: list[Any]
    refresh_references: Callable[..., list[Any]]
    reference_inputs: list[Any]
    reference_outputs: list[Any]


def _sampling_controls(gr: Any, prefix: str, values: Iterable[Any], tr=lambda key: key) -> list[Any]:
    temperature, frequency, presence, repetition, repetition_range = values
    with gr.Row():
        return [
            gr.Slider(0, 1.5, value=temperature, step=.01, label=tr("temperature")),
            gr.Slider(-2, 2, value=frequency, step=.01, label=tr("frequency_penalty")),
            gr.Slider(-2, 2, value=presence, step=.01, label=tr("presence_penalty")),
            gr.Slider(0, 2, value=repetition, step=.01, label=tr("repetition_penalty")),
            gr.Number(value=repetition_range, precision=0, minimum=0, maximum=32768,
                      label=tr("repetition_range")),
        ]


def build_prompt_studio(
    default_steps: list[dict[str, Any]],
    default_writer: dict[str, Any],
    default_summary: dict[str, Any],
    *,
    default_group_prompt: str = "",
    get_debug: Callable[[], bool] | None = None,
    set_debug: Callable[[bool], Any] | None = None,
    ensure_profile: Callable[..., Any] | None = None,
    rename_profile: Callable[[str, str], Any] | None = None,
    i18n: Any | None = None,
) -> PromptStudioUI:
    """Build Prompt Studio inside the caller's active ``gr.Blocks``/``gr.Tab``.

    The returned handle can be retained by ``web_ui`` for future integration.
    """
    import gradio as gr
    tr = i18n or (lambda key: key)

    callbacks = PromptStudioCallbacks(
        default_steps, default_writer, default_summary, get_debug, set_debug,
        ensure_profile, rename_profile, tr("lorebook.current_preset"),
        default_group_prompt)
    initial = prompt_store.load_available_config(
        default_steps, default_writer, default_summary, default_group_prompt)
    initial_values = iter(config_to_form(initial))
    form: list[Any] = []

    gr.Markdown(tr("prompt_studio_help"))
    active_preset = gr.Markdown(callbacks.active_label(initial))
    with gr.Row():
        debug = gr.Checkbox(value=callbacks.debug_state(), label=tr("debug_traces"))
        save_button = gr.Button(tr("save_settings"), variant="primary")
    with gr.Row():
        preset = gr.Dropdown(
            callbacks.presets(), value=callbacks.active_filename(initial),
            label=tr("saved_presets"), elem_id="brain-preset-select")
        preset_name = gr.Textbox(label=tr("preset_name"), placeholder="My preset")
        new_preset_button = gr.Button(tr("prompt_studio.new_preset"))
        save_preset_button = gr.Button(tr("prompt_studio.save_as"))
        rename_preset_button = gr.Button(tr("prompt_studio.rename_preset"))
        export_button = gr.Button(tr("export_csv"))
        import_file = gr.File(label=tr("import_csv"), file_types=[".csv"], type="filepath")
    profile_mode = gr.Radio(
        choices=[
            (tr("prompt_studio.copy_current_lore"), "copy"),
            (tr("prompt_studio.empty_lore"), "empty"),
        ],
        value="copy", label=tr("prompt_studio.unseen_lore_profile"),
    )
    status = gr.Markdown()
    reference_status = gr.Markdown()
    export_file = gr.File(
        label=tr("exported_preset"), interactive=False,
        elem_classes="brain-export-file",
    )

    with gr.Accordion("プリセット＋ロアブック パッケージ", open=False):
        bundle_export = gr.Button("保存済みプリセットを書き出す")
        bundle_download = gr.File(label="パッケージ", interactive=False)
        bundle_upload = gr.File(label="パッケージ読込", file_types=[".json"], type="filepath")
        bundle_mode = gr.Radio(choices=[("新規追加（ID再生成）", "new"), ("復元（同IDは完全一致のみ）", "restore")], value="new", label="読込方式")
        bundle_globals = gr.CheckboxGroup(choices=list(bundle_store.lore.DEFAULT_SETTINGS), value=[], label="推奨グローバル設定の適用（選択項目のみ）")
        bundle_preview = gr.Button("検証・差分表示")
        bundle_commit = gr.Button("表示した内容で読み込む")
        bundle_status = gr.Markdown()
        bundle_plan = gr.State(None)

    def export_package():
        saved = prompt_store.load_config(default_steps, default_writer, default_summary)
        package = bundle_store.export_bundle(saved)
        fd, path = tempfile.mkstemp(suffix=".brainengine.json")
        with os.fdopen(fd, "wb") as handle:
            handle.write(bundle_store.encoded(package))
        return path

    def preview_package(path, mode, selected):
        if not path:
            raise gr.Error("パッケージを選択してください")
        try:
            plan = bundle_store.plan_import(bundle_store.read_bundle(path), mode, selected)
            text = "追加・再利用: " + plan["preset"]["preset_name"] + "\n\n" + json.dumps(plan["global_diff"], ensure_ascii=False, indent=2)
            return plan, text
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise gr.Error(str(exc)) from exc

    def commit_package(plan):
        if not plan:
            raise gr.Error("先に検証・差分表示を実行してください")
        try:
            result = bundle_store.commit(plan)
            return None, "読込完了。プリセット一覧から明示的に選択してください: " + result["preset_name"], gr.update(choices=callbacks.presets())
        except (ValueError, OSError) as exc:
            raise gr.Error(str(exc)) from exc

    bundle_export.click(export_package, outputs=bundle_download)
    bundle_preview.click(preview_package, [bundle_upload, bundle_mode, bundle_globals], [bundle_plan, bundle_status])
    bundle_commit.click(commit_package, bundle_plan, [bundle_plan, bundle_status, preset])
    for control in (bundle_upload, bundle_mode, bundle_globals):
        control.change(lambda: (None, "入力が変わりました。再検証してください。"), outputs=[bundle_plan, bundle_status])

    with gr.Tabs():
        with gr.Tab(tr("reasoning_steps")):
            add_step_button = gr.Button(tr("add_step"), variant="secondary")
            step_containers: list[Any] = []
            enabled_controls: list[Any] = []
            id_controls: list[Any] = []
            number_controls: list[Any] = []
            remove_buttons: list[Any] = []
            initial_enabled = []
            initial_numbers = []
            initial_step_values = []
            reference_order_controls = []
            for _ in range(MAX_STEPS):
                values = [next(initial_values) for _ in range(STEP_WIDTH)]
                initial_step_values.append(values)
                initial_enabled.append(bool(values[0]))
                initial_numbers.append(int(values[3]))
            initial_choices = step_number_choices(initial_enabled, initial_numbers)
            step_numbers_state = gr.State(initial_numbers)
            for number in range(1, MAX_STEPS + 1):
                values = initial_step_values[number - 1]
                with gr.Column(visible=bool(values[0])) as step_container:
                    with gr.Accordion(f"Step {values[3]}", open=bool(values[0])):
                        with gr.Row():
                            enabled = gr.Checkbox(value=values[0], visible=False)
                            slot_id = gr.Textbox(value=values[1], visible=False)
                            name = gr.Textbox(value=values[2], label=tr("title_label"), scale=3)
                            step_number = gr.Dropdown(
                                choices=initial_choices[number - 1], value=values[3],
                                label=tr("step"), allow_custom_value=False, scale=1,
                            )
                            remove_button = gr.Button(tr("delete_step"), variant="stop", scale=1)
                        sampling_values = values[4:4 + len(SAMPLING_FIELDS)]
                        sampling = _sampling_controls(gr, f"Step {number}", sampling_values, tr)
                        prompt = gr.Textbox(
                            value=values[9], label=tr("prompt_label"), lines=10,
                            elem_classes="brain-editor",
                        )
                        with gr.Accordion("入力コンテキスト・出力上限", open=False):
                            ref_mode = gr.Radio(choices=[("指定なし（全先行Step）", "all"),
                                ("参照しない", "none"), ("指定Stepのみ", "selected")], value=values[10], label="今回の先行出力")
                            candidates = [(f"Step {item['step']} · {item['name']}", item['id'])
                                          for item in initial["steps"] if item['step'] < values[3]]
                            ref_ids = gr.Dropdown(choices=candidates, multiselect=True,
                                value=values[11], interactive=values[10] == "selected", label="参照する先行Step")
                            reference_order = gr.Markdown("実行順で参照します。")
                            reference_order_controls.append(reference_order)
                            prior_check = gr.Checkbox(value=values[12], label="今回の先行Step出力をモデル入力に含める")
                            past_check = gr.Checkbox(value=values[13], label="過去ターンの同名Step出力をモデル入力に含める")
                            chat_check = gr.Checkbox(value=values[14], label="チャットログを参照しない")
                            gr.Markdown("参照しない：今回出力の検索も0件。入力注入オフ：検索は維持。チャット除外は最新発言・固定要約も除外し、人物・設定情報だけを例外にします。")
                            token_limit = gr.Number(value=values[15], precision=0, minimum=1,
                                maximum=131072, label="出力トークン上限（空欄は2500。文字数ではありません）")
                            variable_prompt = gr.Textbox(value=values[16], label="変数更新の指示", lines=3)
                            ref_mode.input(lambda mode: gr.update(interactive=mode == "selected"), ref_mode, ref_ids)
                    form.extend([enabled, slot_id, name, step_number, *sampling, prompt,
                                 ref_mode, ref_ids, prior_check, past_check, chat_check, token_limit, variable_prompt])
                    step_containers.append(step_container)
                    enabled_controls.append(enabled)
                    id_controls.append(slot_id)
                    number_controls.append(step_number)
                    remove_buttons.append(remove_button)
        with gr.Tab(tr("writer")):
            writer_name = gr.Textbox(value=next(initial_values), label=tr("title_label"))
            writer_sampling_values = [next(initial_values) for _ in SAMPLING_FIELDS]
            writer_sampling = _sampling_controls(gr, "Writer", writer_sampling_values, tr)
            writer_prompt = gr.Textbox(value=next(initial_values), label=tr("prompt_label"), lines=16)
            form.extend([writer_name, *writer_sampling, writer_prompt])
        with gr.Tab(tr("summarization")):
            summary_name = gr.Textbox(value=next(initial_values), label=tr("title_label"), interactive=False)
            summary_sampling_values = [next(initial_values) for _ in SAMPLING_FIELDS]
            summary_sampling = _sampling_controls(gr, "Summarization", summary_sampling_values, tr)
            summary_prompt = gr.Textbox(value=next(initial_values), label=tr("prompt_label"), lines=16)
            form.extend([summary_name, *summary_sampling, summary_prompt])
        with gr.Tab(tr("group_chat")):
            group_prompt = gr.Textbox(value=next(initial_values), label=tr("group_prompt"), lines=16)
            form.append(group_prompt)
    with gr.Accordion("変数定義", open=False):
        variable_select = gr.Dropdown(choices=[(v["name"] or v["id"], v["id"]) for v in initial.get("variables", [])], label="編集する変数")
        variable_identity = gr.Textbox(value="var_" + uuid.uuid4().hex[:12], visible=False)
        variable_name = gr.Textbox(label="変数名")
        variable_description = gr.Textbox(label="説明", lines=2)
        variable_type = gr.Dropdown(choices=[("整数", "integer"), ("小数", "number"), ("文字列", "string"), ("真偽値", "boolean"), ("列挙値", "enum")], value="integer", label="型")
        variable_initial = gr.Textbox(value="0", label="初期値（真偽値はtrue／false）")
        with gr.Row():
            variable_min = gr.Number(label="最小値（任意）")
            variable_max = gr.Number(label="最大値（任意）")
            variable_max_length = gr.Number(precision=0, label="文字列の最大長（任意）")
        variable_enum = gr.Textbox(label="列挙候補（1行1値）", lines=3)
        agent_choices = [(f"Step {item['step']} · {item['name']}", item['id']) for item in initial["steps"]]
        variable_read = gr.CheckboxGroup(choices=[*agent_choices, ("Writer", "writer")], value=[], label="読取を許可するStep")
        variable_write = gr.CheckboxGroup(choices=agent_choices, value=[], label="更新を許可するStep")
        with gr.Row():
            variable_new = gr.Button("新規変数")
            variable_apply = gr.Button("定義を反映")
            variable_delete = gr.Button("変数を削除")
        variable_status = gr.Markdown("権限未指定は許可なし。読取と更新は独立して指定できます。Writerは読取のみです。")
        with gr.Accordion("定義データ（JSON）", open=False):
            variables_json = gr.Textbox(value=next(initial_values), label="変数定義", lines=10)
    form.append(variables_json)
    variable_fields = [variable_identity, variable_name, variable_description, variable_type, variable_initial,
                       variable_min, variable_max, variable_max_length, variable_enum, variable_read, variable_write]

    def variable_selection(text, identity):
        item = next((v for v in json.loads(text or "[]") if v["id"] == identity), {})
        constraints = item.get("constraints", {})
        value = item.get("initial", 0)
        rendered = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        return [item.get("id", "var_" + uuid.uuid4().hex[:12]), item.get("name", ""), item.get("description", ""),
                item.get("type", "integer"), rendered, constraints.get("min"), constraints.get("max"),
                constraints.get("max_length"), "\n".join(constraints.get("values", [])), item.get("readable_by", []), item.get("writable_by", [])]

    def apply_variable(text, identity, name, description, kind, initial_value, minimum, maximum, max_length, choices, reads, writes, *current_form):
        try:
            definitions = json.loads(text or "[]")
            parsed = initial_value if kind in {"string", "enum"} else json.loads(initial_value)
            constraints = {}
            if kind in {"integer", "number"}:
                constraints.update({k: v for k, v in (("min", minimum), ("max", maximum)) if v is not None})
            if kind == "string" and max_length is not None:
                constraints["max_length"] = int(max_length)
            if kind == "enum":
                constraints["values"] = [v.strip() for v in choices.splitlines() if v.strip()]
            definition = {"id": identity, "name": name, "description": description, "type": kind,
                "initial": parsed, "constraints": constraints, "readable_by": reads or [], "writable_by": writes or []}
            current = form_to_config(current_form)
            definitions = [v for v in definitions if v["id"] != identity] + [definition]
            definitions = prompt_store.validate_definitions(definitions, current["steps"])
            return json.dumps(definitions, ensure_ascii=False, indent=2), gr.update(choices=[(v["name"] or v["id"], v["id"]) for v in definitions], value=identity), "定義を反映しました。プリセットを保存すると確定します。"
        except (ValueError, TypeError) as exc:
            raise gr.Error(str(exc)) from exc

    def delete_variable(text, identity):
        definitions = [v for v in json.loads(text or "[]") if v["id"] != identity]
        return json.dumps(definitions, ensure_ascii=False, indent=2), gr.update(choices=[(v["name"] or v["id"], v["id"]) for v in definitions], value=None)

    variable_select.input(variable_selection, [variables_json, variable_select], variable_fields)
    variable_new.click(lambda: variable_selection("[]", None), outputs=variable_fields)
    variable_apply.click(apply_variable, [variables_json, *variable_fields, *form], [variables_json, variable_select, variable_status])
    variable_delete.click(delete_variable, [variables_json, variable_select], [variables_json, variable_select])
    variables_json.change(lambda text: gr.update(choices=[(v["name"] or v["id"], v["id"]) for v in json.loads(text or "[]")]), variables_json, variable_select)

    form_snapshot = gr.State(list(config_to_form(initial)))
    form_outputs = [*form, *step_containers, step_numbers_state, form_snapshot]

    def render_form(values):
        """Update dependent choices and selections in the same Gradio response.

        A chained refresh is too late: Gradio validates the new value against
        the old choices before that next callback can run.
        """
        clean = normalize_form_references(values)
        steps = sorted((clean[i * STEP_WIDTH + 3], clean[i * STEP_WIDTH + 1], clean[i * STEP_WIDTH + 2])
                       for i in range(MAX_STEPS) if clean[i * STEP_WIDTH])
        enabled = [bool(clean[i * STEP_WIDTH]) for i in range(MAX_STEPS)]
        numbers = [clean[i * STEP_WIDTH + 3] for i in range(MAX_STEPS)]
        number_choices = step_number_choices(enabled, numbers)
        allowed = {identity for _, identity, _ in steps}
        definitions = json.loads(clean[-1] or "[]")
        for definition in definitions:
            definition["readable_by"] = [v for v in definition.get("readable_by", []) if v in allowed or v == "writer"]
            definition["writable_by"] = [v for v in definition.get("writable_by", []) if v in allowed]
        clean[-1] = json.dumps(definitions, ensure_ascii=False, indent=2)
        snapshot = copy.deepcopy(clean)
        for i in range(MAX_STEPS):
            offset = i * STEP_WIDTH
            choices = [(f"Step {n} · {name}", identity) for n, identity, name in steps
                       if enabled[i] and n < numbers[i]]
            clean[offset + 3] = gr.update(choices=number_choices[i], value=numbers[i])
            clean[offset + 11] = gr.update(choices=choices,
                value=clean[offset + 11] if enabled[i] else [],
                interactive=enabled[i] and clean[offset + 10] == "selected")
        return [*clean, *[gr.update(visible=v) for v in enabled], numbers, snapshot]

    def load_form():
        current = prompt_store.load_available_config(default_steps, default_writer, default_summary, default_group_prompt)
        return [gr.update(choices=callbacks.presets(), value=callbacks.active_filename(current)),
                callbacks.active_label(current), *render_form(config_to_form(current))]

    def save_form(*values):
        message, *saved = callbacks.save(*values)
        return [message, *render_form(saved)]

    def load_preset(filename, mode):
        message, active, *saved = callbacks.load_preset(filename, mode)
        return [message, active, *render_form(saved)]

    def preset_dropdown_update(state: PresetDropdownState):
        return gr.update(choices=state.choices, value=state.value)

    def save_preset(*values):
        message, state, active = callbacks.save_preset(*values)
        return message, preset_dropdown_update(state), active

    def new_preset(name):
        message, state, active, *form_values = callbacks.new_preset(name)
        return message, preset_dropdown_update(state), active, *render_form(form_values)

    def rename_preset(filename, name):
        message, state, active, *form_values = callbacks.rename_preset(filename, name)
        return message, preset_dropdown_update(state), active, *render_form(form_values)

    def import_preset(file_value, mode):
        message, state, active, *form_values = callbacks.import_preset(file_value, mode)
        return message, preset_dropdown_update(state), active, *render_form(form_values)

    def refresh_step_ui(*step_values):
        enabled_values, number_values, choices = step_ui_state(
            step_values[:MAX_STEPS], step_values[MAX_STEPS:])
        containers = [gr.update(visible=active) for active in enabled_values]
        dropdowns = [
            gr.update(choices=slot_choices, value=current)
            for slot_choices, current in zip(choices, number_values)
        ]
        return [*containers, *dropdowns, number_values]

    def structural_result(values):
        return render_form(values)

    def add_step(*values):
        values = list(values)
        enabled = [bool(values[i * STEP_WIDTH]) for i in range(MAX_STEPS)]
        try:
            slot = enabled.index(False)
        except ValueError as exc:
            raise gr.Error("A maximum of 23 reasoning steps is supported") from exc
        used = {values[i * STEP_WIDTH + 3] for i in range(MAX_STEPS) if enabled[i]}
        values[slot * STEP_WIDTH] = True
        values[slot * STEP_WIDTH + 1] = f"step_{uuid.uuid4().hex[:12]}"
        values[slot * STEP_WIDTH + 3] = next(n for n in range(1, MAX_STEPS + 1) if n not in used)
        return structural_result(values)

    def remove_step(slot):
        def remove(*values):
            values = list(values)
            values[slot * STEP_WIDTH] = False
            return structural_result(values)
        return remove

    step_state_inputs = [*enabled_controls, *number_controls]
    step_ui_outputs = [*step_containers, *number_controls, step_numbers_state]

    def swap_step_contents(source_slot, *values):
        """Swap step settings with the selected existing step, keeping positions fixed."""
        form_values, previous_numbers = list(values[:-1]), values[-1]
        target = int(form_values[source_slot * STEP_WIDTH + 3])
        # Restoring the slot number can emit another browser input event while
        # other Dropdown values are still being applied. It must be a no-op.
        if target == previous_numbers[source_slot]:
            return [gr.skip() for _ in form_outputs]
        before = list(form_values)
        for i, number in enumerate(previous_numbers):
            before[i * STEP_WIDTH + 3] = number
        try:
            # Gradio's per-component choices may already describe the new
            # order. Validate the complete pre-edit domain model instead.
            form_to_config(before)
            used = {previous_numbers[i] for i in range(MAX_STEPS) if before[i * STEP_WIDTH]}
            if target not in used:
                raise ValueError("Choose an existing Step number")
            swapped, _, _ = swap_step_values(form_values, previous_numbers, source_slot)
        except (ValueError, TypeError) as exc:
            raise gr.Error(str(exc)) from exc
        return render_form(swapped)

    def refresh_references(values, selected_read, selected_write):
        # Read the completed operation's snapshot, not typed Dropdown inputs
        # whose choices may already belong to the next response.
        selected_read, selected_write = selected_read or [], selected_write or []
        clean = normalize_form_references(values)
        steps = sorted([(clean[i * STEP_WIDTH + 3], clean[i * STEP_WIDTH + 1], clean[i * STEP_WIDTH + 2])
                        for i in range(MAX_STEPS) if clean[i * STEP_WIDTH]])
        updates = []
        allowed = {identity for _, identity, _ in steps}
        definitions = json.loads(clean[-1] or "[]")
        for definition in definitions:
            definition["readable_by"] = [v for v in definition.get("readable_by", []) if v in allowed or v == "writer"]
            definition["writable_by"] = [v for v in definition.get("writable_by", []) if v in allowed]
        choices = [(f"Step {n} · {name}", identity) for n, identity, name in steps]
        updates.extend([json.dumps(definitions, ensure_ascii=False, indent=2),
                        gr.update(choices=[*choices, ("Writer", "writer")], value=[v for v in selected_read if v in allowed or v == "writer"]),
                        gr.update(choices=choices, value=[v for v in selected_write if v in allowed])])
        updates.extend(reference_order_values(*clean))
        changes = []
        for i in range(MAX_STEPS):
            offset = i * STEP_WIDTH
            if values[offset] and (values[offset + 10] != clean[offset + 10] or values[offset + 11] != clean[offset + 11]):
                changes.append(f"Step {clean[offset + 3]} · {clean[offset + 2]}：無効な参照を除外しました。有効指定が0件なら指定なしへ戻します。")
        updates.append("\n\n".join(changes))
        return updates

    def reference_order_values(*values):
        steps = sorted([(values[i * STEP_WIDTH + 3], values[i * STEP_WIDTH + 1], values[i * STEP_WIDTH + 2])
                        for i in range(MAX_STEPS) if values[i * STEP_WIDTH]])
        result = []
        for i in range(MAX_STEPS):
            offset = i * STEP_WIDTH
            mode, ids = values[offset + 10:offset + 12]
            selected = [f"Step {n} · {name}" for n, identity, name in steps if n < values[offset + 3]
                        and (mode == "all" or mode == "selected" and identity in (ids or []))]
            result.append("参照する実行順：" + (" → ".join(selected) if selected else "なし"))
        return result

    reference_inputs = [form_snapshot, variable_read, variable_write]
    reference_controls = [variables_json, variable_read, variable_write, *reference_order_controls, reference_status]
    for i in range(MAX_STEPS):
        for control in form[i * STEP_WIDTH + 10:i * STEP_WIDTH + 12]:
            control.input(reference_order_values, form, reference_order_controls)
    add_step_button.click(
        add_step, form, form_outputs,
    ).then(refresh_references, reference_inputs, reference_controls)
    for index, button in enumerate(remove_buttons):
        button.click(
            remove_step(index), form, form_outputs,
        ).then(refresh_references, reference_inputs, reference_controls)
    for index, dropdown in enumerate(number_controls):
        dropdown.input(
            lambda *values, source_slot=index: swap_step_contents(source_slot, *values),
            [*form, step_numbers_state], form_outputs,
            show_progress="hidden", preprocess=False,
        ).then(refresh_references, reference_inputs, reference_controls)

    save_event = save_button.click(
        save_form, inputs=form, outputs=[status, *form_outputs])
    save_as_event = save_preset_button.click(
        save_preset, inputs=[preset_name, *form], outputs=[status, preset, active_preset])
    new_event = new_preset_button.click(
        new_preset, inputs=preset_name,
        outputs=[status, preset, active_preset, *form_outputs])
    rename_event = rename_preset_button.click(
        rename_preset, inputs=[preset, preset_name],
        outputs=[status, preset, active_preset, *form_outputs])
    preset_event = preset.change(
        load_preset, inputs=[preset, profile_mode],
        outputs=[status, active_preset, *form_outputs])
    import_event = import_file.change(
        import_preset, inputs=[import_file, profile_mode],
        outputs=[status, preset, active_preset, *form_outputs],
    )
    export_button.click(
        callbacks.export_preset, inputs=[preset_name, *form], outputs=[status, export_file])
    for event in (save_event, new_event, rename_event, preset_event, import_event):
        event.then(refresh_references, reference_inputs, reference_controls, show_progress="hidden")
    debug.change(callbacks.toggle_debug, inputs=debug, outputs=[debug, status])
    return PromptStudioUI(
        callbacks, form, form_outputs, status, preset, debug, save_event,
        refresh_step_ui, step_state_inputs, step_ui_outputs,
        [save_as_event, new_event, rename_event, preset_event, import_event], load_form,
        [preset, active_preset, *form_outputs],
        refresh_references, reference_inputs, reference_controls,
    )
