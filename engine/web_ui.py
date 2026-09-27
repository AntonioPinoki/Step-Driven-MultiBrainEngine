"""Integrated Gradio control surface for BrainEngine."""

from __future__ import annotations

import os
from typing import Any

import context_detection
import lorebook_store
import provider_config
import ui_text
import ui_theme
from web_lorebooks import build_lorebooks
from web_prompt_assistant import build_prompt_assistant
from web_prompt_studio import build_prompt_studio

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FAVICON_PATH = os.path.join(os.path.dirname(__file__), "icon.png")
FAVICON_HEAD = '<link rel="icon" type="image/png" href="/favicon.ico">'


def create_ui(server: Any, language: str = "ja"):
    import gradio as gr

    language = ui_text.normalize_language(language)
    tr = lambda key: ui_text.t(key, language)

    def normalized_config():
        return provider_config.validate_config(
            provider_config.load_config(), require_main=False
        )

    def detection_text(provider, runtime=None):
        provider = provider or {}
        runtime = runtime or provider_config.provider_context_runtime(
            provider,
            provider_name="main",
            base_url=provider.get("base_url"),
            model=provider.get("model"),
        )
        saved = runtime.get("saved_context_window", provider_config.CONTEXT_WINDOW_DEFAULT)
        effective = runtime.get("effective_context_window", saved)
        lines = [f"**{tr('provider.saved_value')}:** {int(saved):,}"]
        if runtime.get("runtime_source") == "environment":
            lines.append(
                f"**{tr('provider.runtime_override')}:** "
                f"{int(runtime.get('runtime_context_window')):,}"
            )
        detected = runtime.get("detected_context_window")
        status = runtime.get("detection_status") or "not_attempted"
        if detected is not None:
            lines.append(f"**{tr('provider.detected_value')}:** {int(detected):,}")
            if runtime.get("detected_source"):
                lines.append(
                    f"**{tr('provider.detection_source')}:** "
                    f"`{runtime.get('detected_source')}`"
                )
            if runtime.get("detected_at"):
                lines.append(
                    f"**{tr('provider.detection_time')}:** "
                    f"`{runtime.get('detected_at')}`"
                )
        if status == "failed":
            label = (
                tr("provider.using_cached_detection")
                if detected is not None else tr("provider.detection_failed")
            )
            lines.append(f"⚠️ {label}")
        elif status == "stale":
            lines.append(f"⚠️ {tr('provider.detection_stale')}")
        elif status == "not_attempted":
            lines.append(tr("provider.detection_not_attempted"))
        lines.append(f"**{tr('provider.effective_context')}:** {int(effective):,}")
        return "  \n".join(lines)

    def pending_from_provider(provider):
        provider = provider or {}
        fields = (
            "detection_status", "detection_error", "detected_context_window",
            "detected_context_kind", "detected_source", "detected_at",
            "detected_model", "detected_base_url",
        )
        return {
            "base_url": provider_config.normalize_base_url(provider.get("base_url")),
            "model": str(provider.get("model") or "").strip(),
            "mode": str(provider.get("context_detection_mode") or "auto"),
            "detection": {
                field: provider.get(field)
                for field in fields if provider.get(field) not in (None, "")
            },
        }

    def dashboard_values():
        settings = provider_config.runtime_settings()
        not_configured = ui_text.t("not_configured", language)
        main_url = settings.get("BASE_URL") or not_configured
        main_model = settings.get("MODEL_NAME") or not_configured
        logic_url = settings.get("LOGIC_BASE_URL") or ""
        logic_model = settings.get("LOGIC_MODEL") or ""
        return (
            f"● {ui_text.t('running', language)}",
            f"**{ui_text.t('multibrain_api', language)}:** `http://127.0.0.1:8001/v1`",
            f"**{ui_text.t('main_provider_api', language)}:** `{main_url}`  \n"
            f"**{ui_text.t('model', language)}:** `{main_model}`  \n"
            f"**{tr('provider.effective_context')}:** "
            f"{settings['MAIN_CONTEXT']['effective_context_window']:,}",
            gr.Markdown(
                value=(f"**{ui_text.t('background_provider_api', language)}:** "
                       f"`{logic_url}`  \n**{ui_text.t('model', language)}:** "
                       f"`{logic_model or not_configured}`  \n"
                       f"**{tr('provider.effective_context')}:** "
                       f"{settings['LOGIC_CONTEXT']['effective_context_window']:,}"),
                visible=bool(settings.get("LOGIC_ENABLED")),
            ),
        )

    def load_providers():
        config = normalized_config()
        runtime = provider_config.runtime_settings(config)
        main = config["main"]
        logic = config["logic"]
        logic_on = bool(config["logic_enabled"])
        return (
            main.get("base_url", ""), main.get("model", ""), main.get("api_key", ""),
            main.get("context_window", provider_config.CONTEXT_WINDOW_DEFAULT),
            main.get("context_detection_mode", "auto"),
            detection_text(main, runtime["MAIN_CONTEXT"]),
            pending_from_provider(main),
            logic_on,
            gr.update(value=logic.get("base_url", ""), interactive=logic_on),
            gr.update(value=logic.get("model", ""), interactive=logic_on),
            gr.update(value=logic.get("api_key", ""), interactive=logic_on),
            gr.update(
                value=logic.get("context_window", provider_config.CONTEXT_WINDOW_DEFAULT),
                interactive=logic_on,
            ),
            gr.update(
                value=logic.get("context_detection_mode", "auto"),
                interactive=logic_on,
            ),
            detection_text(logic, provider_config.provider_context_runtime(
                logic, provider_name="background",
                base_url=logic.get("base_url"), model=logic.get("model"),
            )),
            pending_from_provider(logic),
            *dashboard_values(),
        )

    def probe_context(base_url, model, api_key, mode, context_window,
                      previous_pending=None):
        result = context_detection.detect_context_window(
            base_url, api_key, model, mode
        )
        detection = provider_config.detection_payload(
            result, base_url=base_url, model=model
        )
        previous_pending = previous_pending if isinstance(previous_pending, dict) else {}
        same_previous = (
            provider_config.normalize_base_url(previous_pending.get("base_url"))
            == provider_config.normalize_base_url(base_url)
            and str(previous_pending.get("model") or "").strip() == str(model or "").strip()
            and str(previous_pending.get("mode") or "auto") == str(mode or "auto")
        )
        if result.get("status") == "failed" and same_previous:
            detection = {**(previous_pending.get("detection") or {}), **detection}
        pending = {
            "base_url": provider_config.normalize_base_url(base_url),
            "model": str(model or "").strip(),
            "mode": str(mode or "auto"),
            "detection": detection,
        }
        preview = {
            "base_url": base_url,
            "model": model,
            "context_window": context_window,
            "context_detection_mode": mode,
            **detection,
        }
        return detection_text(preview), pending

    def test_connection(base_url, model, api_key, mode, context_window,
                        previous_pending):
        result = provider_config.test_provider(base_url, api_key)
        if result["ok"]:
            detected, pending = probe_context(
                base_url, model, api_key, mode, context_window, previous_pending
            )
            return tr("connection_ok"), detected, pending
        return (
            str(tr("connection_failed")).format(
                error=result.get("error") or result.get("status")
            ),
            gr.update(),
            {},
        )

    def save_providers(main_url, main_model, main_key, main_context, main_mode,
                       main_pending, logic_on, logic_url, logic_model, logic_key,
                       logic_context, logic_mode, logic_pending):
        current = normalized_config()

        def form_provider(existing, base_url, model, api_key, context_window,
                          mode, pending):
            provider = dict(existing or {})
            old_identity = (
                provider_config.normalize_base_url(provider.get("base_url")),
                str(provider.get("model") or "").strip(),
                str(provider.get("context_detection_mode") or "auto"),
            )
            new_identity = (
                provider_config.normalize_base_url(base_url),
                str(model or "").strip(),
                str(mode or "auto"),
            )
            provider.update({
                "base_url": base_url,
                "model": model,
                "api_key": api_key,
                "context_window": context_window,
                "context_detection_mode": mode,
            })
            if old_identity != new_identity:
                provider["detection_status"] = "stale"
            return provider_config.merge_pending_detection(
                provider, pending, base_url=base_url, model=model, mode=mode
            )

        payload = {
            "main": form_provider(
                current["main"], main_url, main_model, main_key,
                main_context, main_mode, main_pending,
            ),
            "logic_enabled": bool(logic_on),
            "logic": form_provider(
                current["logic"], logic_url, logic_model, logic_key,
                logic_context, logic_mode, logic_pending,
            ),
        }
        try:
            clean = provider_config.save_config(payload)
            runtime = provider_config.runtime_settings(clean)
            server.reload_provider_runtime(runtime)
        except provider_config.ProviderConfigError as exc:
            raise gr.Error(str(exc)) from exc
        return (
            tr("saved"),
            detection_text(clean["main"], runtime["MAIN_CONTEXT"]),
            detection_text(clean["logic"], provider_config.provider_context_runtime(
                clean["logic"], provider_name="background",
                base_url=clean["logic"].get("base_url"),
                model=clean["logic"].get("model"),
            )),
            pending_from_provider(clean["main"]),
            pending_from_provider(clean["logic"]),
        )

    def toggle_logic(enabled):
        update = gr.update(interactive=bool(enabled))
        return (update, update, update, update, update, update, update)

    detection_choices = (
        [
            ("自動", "auto"),
            ("OpenAI互換モデル情報", "openai"),
            ("/props 系", "props"),
            ("Ollama", "ollama"),
            ("TabbyAPI", "tabby"),
            ("LM Studio", "lm_studio"),
            ("手動のみ", "manual"),
        ]
        if language == "ja" else [
            ("Auto", "auto"),
            ("OpenAI-compatible model info", "openai"),
            ("/props", "props"),
            ("Ollama", "ollama"),
            ("TabbyAPI", "tabby"),
            ("LM Studio", "lm_studio"),
            ("Manual only", "manual"),
        ]
    )

    def lore_config():
        prompts = server.active_prompt_setup()
        body = lorebook_store.load_config(prompts)
        body["agents"] = [
            {"id": item["id"], "name": item["name"], "step": item.get("step")}
            for item in prompts["steps"]
        ] + [{"id": "writer", "name": prompts["writer"]["name"], "step": "writer"}]
        return body

    def with_lore_agents(body, prompts):
        body["agents"] = [
            {"id": item["id"], "name": item["name"], "step": item.get("step")}
            for item in prompts["steps"]
        ] + [{"id": "writer", "name": prompts["writer"]["name"], "step": "writer"}]
        return body

    def save_lore_books(payload):
        prompts = server.active_prompt_setup()
        return with_lore_agents(lorebook_store.save_books(payload, prompts), prompts)

    def save_lore_assignments(payload):
        prompts = server.active_prompt_setup()
        return with_lore_agents(lorebook_store.save_assignments(payload, prompts), prompts)

    def move_lore_target(source_id, destination_id):
        prompts = server.active_prompt_setup()
        return lorebook_store.move_assignment_target(
            prompts["preset_id"], source_id, destination_id, prompts)

    def remove_lore_target(target_id):
        prompts = server.active_prompt_setup()
        return lorebook_store.remove_assignment_target(
            prompts["preset_id"], target_id)

    def remove_missing_lore_file(filename):
        prompts = server.active_prompt_setup()
        return lorebook_store.remove_missing_file_reference(
            prompts["preset_id"], filename)

    def assistant_context():
        config = server.active_prompt_setup()
        agents = list(config["steps"]) + [config["writer"], config["summary"]]
        return {"agents": [{
            "id": item["id"], "name": item["name"], "step": item.get("step"),
            "recent_output_count": len(server.RECENT_AGENT_OUTPUTS.get(item["id"], ())),
            "order": server.PROMPT_ORDERS.get(item["id"], ""),
        } for item in agents]}

    def save_orders(orders):
        valid = {str(item["id"]) for item in assistant_context()["agents"]}
        clean = {}
        for agent_id, value in (orders or {}).items():
            value = str(value or "").strip()
            if str(agent_id) in valid and value:
                if len(value) > 12000:
                    raise ValueError("Each temporary order must be 12000 characters or fewer")
                clean[str(agent_id)] = value
        server.PROMPT_ORDERS.clear()
        server.PROMPT_ORDERS.update(clean)
        return dict(clean)

    async def stream_assistant(messages, temperature, max_tokens):
        reference = server.prompt_assistant_reference()
        api_messages = [{
            "role": "system",
            "content": server.PROMPT_ASSISTANT_SYSTEM +
                       "\n\nCURRENT BRAINENGINE REFERENCE:\n" + reference,
        }, *messages[-20:]]
        context_snapshot = server._context_snapshot(True)
        prepared = server._prepare_outbound_messages(
            api_messages, max_tokens=max_tokens, is_writer=True,
            budget_metadata={"chat_indices": list(range(1, len(api_messages)))},
            context_snapshot=context_snapshot,
        )
        active_client, active_model = server.writer_client, server.MODEL_NAME
        stream = await active_client.chat.completions.create(
            model=active_model, messages=prepared.messages, temperature=temperature,
            max_tokens=max_tokens, stream=True,
            extra_headers={"HTTP-Referer": "http://localhost:8001", "X-Title": "BrainEngine2 PromptAssistant"},
        )
        try:
            async for part in server._stream_with_timeout(stream, server.STREAM_STALL_TIMEOUT):
                if part.choices:
                    delta = part.choices[0].delta.content or ""
                    if delta:
                        yield delta
        finally:
            await stream.close()

    with gr.Blocks(title="Step-driven MultiBrainEngine") as demo:
        gr.Markdown(f"# {tr('app_title')}", elem_classes="brain-header")
        with gr.Tabs():
            with gr.Tab(tr("dashboard")):
                with gr.Row():
                    language_selector = gr.Dropdown(
                        choices=ui_text.LANGUAGE_CHOICES, value=language,
                        label=tr("language"), scale=1, allow_custom_value=False,
                    )
                    language_apply = gr.Button(tr("common.apply"), variant="primary", scale=1)
                server_status = gr.Markdown(elem_classes="brain-status-ok")
                multibrain_api = gr.Markdown(elem_classes="brain-dashboard-card")
                main_provider_api = gr.Markdown(elem_classes="brain-dashboard-card")
                logic_provider_api = gr.Markdown(elem_classes="brain-dashboard-card")
                gr.Markdown(tr("dashboard_note"), elem_classes="brain-dashboard-note")

            with gr.Tab(tr("providers")):
                provider_status = gr.Markdown()
                main_pending_detection = gr.State({})
                logic_pending_detection = gr.State({})
                with gr.Accordion(tr("main_provider"), open=True):
                    main_url = gr.Textbox(label=tr("base_url"))
                    main_model = gr.Textbox(label=tr("model"))
                    main_key = gr.Textbox(label=tr("api_key"), type="password")
                    main_context = gr.Slider(
                        provider_config.CONTEXT_WINDOW_MIN,
                        provider_config.CONTEXT_WINDOW_MAX,
                        value=provider_config.CONTEXT_WINDOW_DEFAULT,
                        step=1024,
                        label=tr("provider.context_window"),
                    )
                    with gr.Accordion(tr("provider.context_detection"), open=False):
                        main_mode = gr.Dropdown(
                            choices=detection_choices, value="auto",
                            label=tr("provider.detection_mode"),
                            allow_custom_value=False,
                        )
                        main_detection = gr.Markdown()
                        main_refresh = gr.Button(tr("provider.detect_again"))
                    main_test = gr.Button(tr("test_connection"))
                    main_test_status = gr.Markdown()
                logic_on = gr.Checkbox(label=tr("background_provider"))
                with gr.Accordion(tr("background_provider"), open=False):
                    logic_url = gr.Textbox(label=tr("base_url"))
                    logic_model = gr.Textbox(label=tr("model"))
                    logic_key = gr.Textbox(label=tr("api_key"), type="password")
                    logic_context = gr.Slider(
                        provider_config.CONTEXT_WINDOW_MIN,
                        provider_config.CONTEXT_WINDOW_MAX,
                        value=provider_config.CONTEXT_WINDOW_DEFAULT,
                        step=1024,
                        label=tr("provider.context_window"),
                    )
                    with gr.Accordion(tr("provider.context_detection"), open=False):
                        logic_mode = gr.Dropdown(
                            choices=detection_choices, value="auto",
                            label=tr("provider.detection_mode"),
                            allow_custom_value=False,
                        )
                        logic_detection = gr.Markdown()
                        logic_refresh = gr.Button(tr("provider.detect_again"))
                    logic_test = gr.Button(tr("test_connection"))
                    logic_test_status = gr.Markdown()
                provider_save = gr.Button(tr("save"), variant="primary")

            with gr.Tab(tr("prompt_studio")) as prompt_tab:
                prompt_ui = build_prompt_studio(
                    server.DEFAULT_REASONING_STEPS, server.DEFAULT_WRITER, server.DEFAULT_SUMMARY,
                    default_group_prompt=server.DEFAULT_GROUP_PROMPT,
                    get_debug=lambda: server.DEBUG_MODE,
                    set_debug=lambda enabled: setattr(server, "DEBUG_MODE", bool(enabled)),
                    ensure_profile=lorebook_store.ensure_profile,
                    rename_profile=lorebook_store.rename_profile,
                    i18n=tr,
                )

            with gr.Tab(tr("lorebooks")) as lore_tab:
                lore_ui = build_lorebooks(
                    gr, load_config=lore_config, save_books=save_lore_books,
                    save_assignments=save_lore_assignments,
                    import_book=lorebook_store.import_book_file,
                    delete_book_file=lorebook_store.delete_book_file,
                    move_assignment_target=move_lore_target,
                    remove_assignment_target=remove_lore_target,
                    remove_missing_file_reference=remove_missing_lore_file,
                    i18n=tr,
                )

            with gr.Tab(tr("prompt_assistant")):
                assistant_ui = build_prompt_assistant(
                    gr, text=ui_text.t, language=language, get_context=assistant_context,
                    save_orders=save_orders, stream_reply=stream_assistant, i18n=tr,
                )

        demo.load(
            load_providers, None,
            [main_url, main_model, main_key, main_context, main_mode,
             main_detection, main_pending_detection,
             logic_on, logic_url, logic_model, logic_key, logic_context, logic_mode,
             logic_detection, logic_pending_detection,
             server_status, multibrain_api, main_provider_api, logic_provider_api],
        )
        prompt_load_event = demo.load(
            prompt_ui.callbacks.load, None, prompt_ui.form_outputs,
        )
        prompt_load_event.then(
            prompt_ui.refresh_step_ui,
            prompt_ui.step_state_inputs,
            prompt_ui.step_ui_outputs,
            show_progress="hidden",
        )
        prompt_tab.select(
            prompt_ui.refresh_step_ui,
            prompt_ui.step_state_inputs,
            prompt_ui.step_ui_outputs,
            show_progress="hidden",
        )
        demo.load(lore_ui["load"], None, lore_ui["load_outputs"])
        lore_tab.select(
            lore_ui["full_refresh"], None,
            lore_ui["full_refresh_outputs"], show_progress="hidden",
        )
        demo.load(assistant_ui["load"], None, assistant_ui["load_outputs"])
        prompt_ui.save_event.then(
            assistant_ui["refresh"], assistant_ui["refresh_inputs"],
            assistant_ui["load_outputs"], show_progress="hidden",
        )
        prompt_ui.save_event.then(
            prompt_ui.refresh_step_ui,
            prompt_ui.step_state_inputs,
            prompt_ui.step_ui_outputs,
            show_progress="hidden",
        )
        for identity_event in prompt_ui.identity_events:
            identity_event.then(
                lore_ui["full_refresh"], None, lore_ui["full_refresh_outputs"],
                show_progress="hidden",
            ).then(
                assistant_ui["refresh"], assistant_ui["refresh_inputs"],
                assistant_ui["load_outputs"], show_progress="hidden",
            )
        main_test.click(
            test_connection,
            [main_url, main_model, main_key, main_mode, main_context,
             main_pending_detection],
            [main_test_status, main_detection, main_pending_detection],
        )
        logic_test.click(
            test_connection,
            [logic_url, logic_model, logic_key, logic_mode, logic_context,
             logic_pending_detection],
            [logic_test_status, logic_detection, logic_pending_detection],
        )
        main_refresh.click(
            probe_context,
            [main_url, main_model, main_key, main_mode, main_context,
             main_pending_detection],
            [main_detection, main_pending_detection],
        )
        logic_refresh.click(
            probe_context,
            [logic_url, logic_model, logic_key, logic_mode, logic_context,
             logic_pending_detection],
            [logic_detection, logic_pending_detection],
        )
        logic_on.change(
            toggle_logic, logic_on,
            [logic_url, logic_model, logic_key, logic_context, logic_mode,
             logic_refresh, logic_test],
            show_progress="hidden",
        )
        provider_save.click(
            save_providers,
            [main_url, main_model, main_key, main_context, main_mode,
             main_pending_detection, logic_on, logic_url, logic_model, logic_key,
             logic_context, logic_mode, logic_pending_detection],
            [provider_status, main_detection, logic_detection,
             main_pending_detection, logic_pending_detection],
        ).then(
            dashboard_values, None,
            [server_status, multibrain_api, main_provider_api, logic_provider_api],
        )
        language_apply.click(
            fn=None, inputs=language_selector, outputs=None,
            js="""(language) => {
                window.localStorage.setItem('brainengine-language', language);
                window.location.assign(`/ui/${language}/`);
            }""",
        )

    return demo


def mount(fastapi_app, server):
    import gradio as gr
    from fastapi.responses import FileResponse, RedirectResponse

    @fastapi_app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(FAVICON_PATH, media_type="image/png")

    @fastapi_app.get("/", include_in_schema=False)
    async def web_root():
        return RedirectResponse("/ui/ja/")

    @fastapi_app.get("/ui", include_in_schema=False)
    @fastapi_app.get("/ui/", include_in_schema=False)
    async def legacy_web_root():
        return RedirectResponse("/ui/ja/")

    mounted = fastapi_app
    allowed = [os.path.join(PROJECT_ROOT, "Preset")]
    for code in ui_text.SUPPORTED_LANGUAGES:
        mounted = gr.mount_gradio_app(
            mounted, create_ui(server, code), path=f"/ui/{code}",
            allowed_paths=allowed, theme=ui_theme.create_theme(), css=ui_theme.CUSTOM_CSS,
            footer_links=[], show_error=True, head=FAVICON_HEAD,
        )
    return mounted
