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
STEP_WIDTH = 5 + len(SAMPLING_FIELDS)  # enabled, id, name, number, sampling, prompt
FIXED_WIDTH = 2 + len(SAMPLING_FIELDS)  # name, sampling, prompt


def _sampling(item: dict[str, Any]) -> list[Any]:
    return [item.get(field) for field in SAMPLING_FIELDS]


def config_to_form(config: dict[str, Any]) -> tuple[Any, ...]:
    """Convert a prompt config into the fixed 23-slot Gradio form values."""
    by_number = {int(item["step"]): item for item in config.get("steps", [])}
    values: list[Any] = []
    for number in range(1, MAX_STEPS + 1):
        item = by_number.get(number)
        if item:
            values.extend([True, item["id"], item["name"], number, *_sampling(item), item["prompt"]])
        else:
            values.extend([
                False, f"step_{number}", f"Step {number}", number,
                prompt_store.DEFAULT_STEP_TEMPERATURE,
                prompt_store.DEFAULT_FREQUENCY_PENALTY,
                prompt_store.DEFAULT_PRESENCE_PENALTY,
                prompt_store.DEFAULT_REPETITION_PENALTY,
                prompt_store.DEFAULT_REPETITION_PENALTY_RANGE, "",
            ])
    writer = config["writer"]
    summary = config["summary"]
    values.extend([writer.get("name", "Writer"), *_sampling(writer), writer["prompt"]])
    values.extend([summary.get("name", "Summarize"), *_sampling(summary), summary["prompt"]])
    values.append(config.get("group_prompt", ""))
    return tuple(values)


def form_to_config(values: Iterable[Any]) -> dict[str, Any]:
    """Convert fixed form values to a config and validate it via prompt_store."""
    values = list(values)
    expected = MAX_STEPS * STEP_WIDTH + FIXED_WIDTH * 2 + 1
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
        if enabled:
            steps.append({
                "id": slot_id, "name": name, "step": number, "prompt": prompt,
                **sampling,
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
    config = prompt_store.validate_config(steps, writer, summary, values[cursor])
    config.pop("preset_id", None)
    config.pop("preset_name", None)
    return config


def step_number_choices(enabled_values: Iterable[Any], number_values: Iterable[Any]):
    """Return per-slot choices containing only unused numbers plus its current one."""
    enabled = [bool(value) for value in enabled_values]
    numbers = [int(value) if value not in (None, "") else index + 1
               for index, value in enumerate(number_values)]
    used = {number for active, number in zip(enabled, numbers) if active}
    return [
        [number for number in range(1, MAX_STEPS + 1)
         if number not in used or (active and number == current)]
        for active, current in zip(enabled, numbers)
    ]


def step_ui_state(enabled_values: Iterable[Any], number_values: Iterable[Any]):
    """Return the visibility and number choices for every fixed step slot."""
    enabled = [bool(value) for value in enabled_values]
    numbers = [int(value) if value not in (None, "") else index + 1
               for index, value in enumerate(number_values)]
    return enabled, numbers, step_number_choices(enabled, numbers)


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
            label=tr("saved_presets"))
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
    export_file = gr.File(
        label=tr("exported_preset"), interactive=False,
        elem_classes="brain-export-file",
    )

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
            for _ in range(MAX_STEPS):
                values = [next(initial_values) for _ in range(STEP_WIDTH)]
                initial_step_values.append(values)
                initial_enabled.append(bool(values[0]))
                initial_numbers.append(int(values[3]))
            initial_choices = step_number_choices(initial_enabled, initial_numbers)
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
                            value=values[-1], label=tr("prompt_label"), lines=10,
                            elem_classes="brain-editor",
                        )
                    form.extend([enabled, slot_id, name, step_number, *sampling, prompt])
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

    form_outputs = list(form)

    def preset_dropdown_update(state: PresetDropdownState):
        return gr.update(choices=state.choices, value=state.value)

    def save_preset(*values):
        message, state, active = callbacks.save_preset(*values)
        return message, preset_dropdown_update(state), active

    def new_preset(name):
        message, state, active, *form_values = callbacks.new_preset(name)
        return message, preset_dropdown_update(state), active, *form_values

    def rename_preset(filename, name):
        message, state, active, *form_values = callbacks.rename_preset(filename, name)
        return message, preset_dropdown_update(state), active, *form_values

    def import_preset(file_value, mode):
        message, state, active, *form_values = callbacks.import_preset(file_value, mode)
        return message, preset_dropdown_update(state), active, *form_values

    def refresh_step_ui(*step_values):
        enabled_values, number_values, choices = step_ui_state(
            step_values[:MAX_STEPS], step_values[MAX_STEPS:])
        containers = [gr.update(visible=active) for active in enabled_values]
        dropdowns = [
            gr.update(choices=slot_choices, value=current)
            for slot_choices, current in zip(choices, number_values)
        ]
        return [*containers, *dropdowns]

    def add_step(*step_values):
        enabled_values = [bool(value) for value in step_values[:MAX_STEPS]]
        number_values = [int(value) for value in step_values[MAX_STEPS:MAX_STEPS * 2]]
        ids = list(step_values[MAX_STEPS * 2:])
        try:
            slot = enabled_values.index(False)
        except ValueError as exc:
            raise gr.Error("A maximum of 23 reasoning steps is supported") from exc
        used = {number for active, number in zip(enabled_values, number_values) if active}
        new_number = next(number for number in range(1, MAX_STEPS + 1) if number not in used)
        enabled_values[slot] = True
        number_values[slot] = new_number
        ids[slot] = f"step_{uuid.uuid4().hex[:12]}"
        choices = step_number_choices(enabled_values, number_values)
        return [
            *enabled_values, *ids,
            *[gr.update(visible=active) for active in enabled_values],
            *[gr.update(choices=choices[index], value=number_values[index])
              for index in range(MAX_STEPS)],
        ]

    def remove_step(slot):
        def remove(*values):
            enabled_values = [bool(value) for value in values[:MAX_STEPS]]
            number_values = [int(value) for value in values[MAX_STEPS:]]
            enabled_values[slot] = False
            choices = step_number_choices(enabled_values, number_values)
            return [
                *enabled_values,
                *[gr.update(visible=active) for active in enabled_values],
                *[gr.update(choices=choices[index], value=number_values[index])
                  for index in range(MAX_STEPS)],
            ]
        return remove

    step_state_inputs = [*enabled_controls, *number_controls]
    step_ui_outputs = [*step_containers, *number_controls]
    add_step_button.click(
        add_step, [*enabled_controls, *number_controls, *id_controls],
        [*enabled_controls, *id_controls, *step_containers, *number_controls],
    )
    for index, button in enumerate(remove_buttons):
        button.click(
            remove_step(index), step_state_inputs,
            [*enabled_controls, *step_containers, *number_controls],
        )
    for dropdown in number_controls:
        dropdown.input(refresh_step_ui, step_state_inputs, step_ui_outputs,
                       show_progress="hidden")

    save_event = save_button.click(
        callbacks.save, inputs=form, outputs=[status, *form_outputs])
    save_as_event = save_preset_button.click(
        save_preset, inputs=[preset_name, *form], outputs=[status, preset, active_preset])
    new_event = new_preset_button.click(
        new_preset, inputs=preset_name,
        outputs=[status, preset, active_preset, *form_outputs])
    new_event.then(refresh_step_ui, step_state_inputs, step_ui_outputs,
                   show_progress="hidden")
    rename_event = rename_preset_button.click(
        rename_preset, inputs=[preset, preset_name],
        outputs=[status, preset, active_preset, *form_outputs])
    rename_event.then(refresh_step_ui, step_state_inputs, step_ui_outputs,
                      show_progress="hidden")
    preset_event = preset.change(
        callbacks.load_preset, inputs=[preset, profile_mode],
        outputs=[status, active_preset, *form_outputs])
    preset_event.then(refresh_step_ui, step_state_inputs, step_ui_outputs,
                      show_progress="hidden")
    import_event = import_file.change(
        import_preset, inputs=[import_file, profile_mode],
        outputs=[status, preset, active_preset, *form_outputs],
    )
    import_event.then(refresh_step_ui, step_state_inputs, step_ui_outputs,
                      show_progress="hidden")
    export_button.click(
        callbacks.export_preset, inputs=[preset_name, *form], outputs=[status, export_file])
    debug.change(callbacks.toggle_debug, inputs=debug, outputs=[debug, status])
    return PromptStudioUI(
        callbacks, form, form_outputs, status, preset, debug, save_event,
        refresh_step_ui, step_state_inputs, step_ui_outputs,
        [save_as_event, new_event, rename_event, preset_event, import_event],
    )
