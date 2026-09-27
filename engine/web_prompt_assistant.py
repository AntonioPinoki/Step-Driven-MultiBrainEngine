"""Gradio PromptAssistant panel.

The UI owns only per-browser chat state.  Prompt context and temporary orders
remain server-side so the existing API and SillyTavern behaviour stay aligned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any


MAX_MESSAGES = 20
MAX_MESSAGE_CHARS = 12000


def greeting_message(
    agents: list[dict[str, Any]] | None = None,
    *,
    text: Callable[..., str] | None = None,
    language: object = None,
) -> str:
    """Build the localized, display-only PromptAssistant greeting."""
    translate = text or (lambda key, _language=None: key)
    introduction = translate("prompt_assistant.greeting.intro", language) + "\n\n"
    orders = translate("prompt_assistant.greeting.orders", language) + "\n\n"
    labels = []
    for agent in agents or []:
        if agent.get("step") is not None:
            kind = f"Step {agent['step']}"
        elif agent.get("id") == "writer":
            kind = "Writer"
        elif agent.get("id") == "summary":
            kind = "Summarize"
        else:
            kind = "Fixed"
        labels.append(f"・{kind}: {agent.get('name') or agent.get('id')}")
    listing = (
        translate("prompt_assistant.greeting.agents_heading", language) + "\n" + "\n".join(labels)
        if labels else translate("prompt_assistant.greeting.agents_loading", language)
    )
    return introduction + orders + listing + "\n\n" + translate(
        "prompt_assistant.greeting.closing", language)


def append_user_message(message: str, history: list[dict[str, str]] | None):
    """Validate and append a user message without mutating the input history."""
    text = str(message or "").strip()
    if not text:
        raise ValueError("Message is empty")
    if len(text) > MAX_MESSAGE_CHARS:
        raise ValueError(f"Message must be {MAX_MESSAGE_CHARS} characters or fewer")
    clean = [dict(item) for item in (history or []) if isinstance(item, dict)]
    clean.append({"role": "user", "content": text})
    return clean[-MAX_MESSAGES:], ""


def build_prompt_assistant(
    gr: Any,
    *,
    text: Callable[[str, str], str],
    language: Any,
    get_context: Callable[[], dict[str, Any]],
    save_orders: Callable[[dict[str, str]], dict[str, str]],
    stream_reply: Callable[[list[dict[str, str]], float, int], AsyncIterator[str]],
    i18n: Any | None = None,
):
    """Build the PromptAssistant tab and wire it to server-side services."""
    initial_greeting = greeting_message(text=text, language=language)
    history_state = gr.State([])
    greeting_state = gr.State(initial_greeting)
    orders_state = gr.State({})
    tr = i18n or (lambda key: key)

    gr.Markdown("### PromptAssistant")
    context_status = gr.Markdown("")
    with gr.Accordion(tr("temporary_orders"), open=False) as orders_accordion:
        with gr.Row():
            agent = gr.Dropdown(choices=[], label=tr("agent"), scale=1)
            order = gr.Textbox(lines=4, label=tr("order_label"), scale=3)
        with gr.Row():
            apply_order = gr.Button(tr("apply_order"), variant="primary")
            clear_order = gr.Button(tr("clear_order"))
        order_status = gr.Markdown("")

    chatbot = gr.Chatbot(
        value=[{"role": "assistant", "content": initial_greeting}], height=480, layout="bubble",
        label="PromptAssistant", buttons=["copy", "copy_all"],
        elem_classes="brain-chat",
    )
    message = gr.Textbox(
        lines=1, max_lines=8, label=tr("message"),
        placeholder=tr("message_placeholder"), autofocus=True,
        elem_id="prompt-assistant-input",
    )
    with gr.Row():
        temperature = gr.Slider(0.0, 1.5, value=0.4, step=0.05, label=tr("temperature"))
        max_tokens = gr.Slider(512, 8192, value=2200, step=128, label=tr("max_tokens"))
    with gr.Row():
        send = gr.Button(tr("send"), variant="primary", elem_id="prompt-assistant-send")
        clear_chat = gr.Button(tr("clear_chat"))

    def refresh_context(history=None, request: Any = None):
        body = get_context()
        agents = body.get("agents") or []
        choices = [(item.get("name") or item["id"], str(item["id"])) for item in agents]
        orders = {str(item["id"]): str(item.get("order") or "") for item in agents}
        selected = choices[0][1] if choices else None
        current = orders.get(selected, "") if selected else ""
        selected_language = language
        if request is not None:
            query = dict(getattr(request, "query_params", {}) or {})
            selected_language = query.get("lang") or selected_language
        greeting = greeting_message(agents, text=text, language=selected_language)
        working = [dict(item) for item in (history or []) if isinstance(item, dict)]
        return (
            orders,
            gr.Dropdown(choices=choices, value=selected),
            current,
            f"{len(agents)} agents available",
            greeting,
            [{"role": "assistant", "content": greeting}, *working],
        )

    refresh_context.__annotations__["request"] = gr.Request

    def load_context(request: Any = None):
        return refresh_context([], request)

    load_context.__annotations__["request"] = gr.Request

    def select_agent(agent_id, orders):
        return (orders or {}).get(str(agent_id), "")

    def apply_selected_order(agent_id, value, orders):
        if not agent_id:
            return orders or {}, "No agent selected"
        updated = dict(orders or {})
        value = str(value or "").strip()
        if value:
            updated[str(agent_id)] = value
        else:
            updated.pop(str(agent_id), None)
        saved = save_orders(updated)
        return saved, "Orders applied"

    def submit_user(user_text, history, greeting):
        try:
            working, _ = append_user_message(user_text, history)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc
        display = [{"role": "assistant", "content": greeting}, *working]
        return display, working, ""

    async def stream_response(history, temp, tokens, greeting):
        working = [dict(item) for item in (history or []) if isinstance(item, dict)]
        request_messages = list(working)
        working.append({"role": "assistant", "content": ""})
        yield [{"role": "assistant", "content": greeting}, *working], working
        reply = ""
        async for delta in stream_reply(request_messages, float(temp), int(tokens)):
            reply += str(delta or "")
            working[-1] = {"role": "assistant", "content": reply}
            working = working[-MAX_MESSAGES:]
            # Forward every provider chunk immediately; do not buffer display updates.
            yield [{"role": "assistant", "content": greeting}, *working], working

    def clear_all(greeting):
        return [{"role": "assistant", "content": greeting}], [], ""

    agent.change(select_agent, [agent, orders_state], order, show_progress="hidden")
    apply_order.click(
        apply_selected_order, [agent, order, orders_state], [orders_state, order_status]
    )
    clear_order.click(
        lambda agent_id, orders: apply_selected_order(agent_id, "", orders),
        [agent, orders_state], [orders_state, order_status],
    ).then(lambda: "", None, order)
    def wire_submit(event):
        return event(
            submit_user, [message, history_state, greeting_state],
            [chatbot, history_state, message], queue=False, show_progress="hidden",
        ).then(
            stream_response,
            [history_state, temperature, max_tokens, greeting_state],
            [chatbot, history_state], concurrency_limit=1, show_progress="hidden",
        )

    wire_submit(send.click)
    wire_submit(message.submit)
    clear_chat.click(
        clear_all, greeting_state, [chatbot, history_state, message], queue=False,
    )

    return {
        "load": load_context,
        "load_outputs": [
            orders_state, agent, order, context_status, greeting_state, chatbot,
        ],
        "refresh": refresh_context,
        "refresh_inputs": [history_state],
        "i18n": {
            "orders_accordion": orders_accordion,
            "agent": agent,
            "order": order,
            "apply_order": apply_order,
            "clear_order": clear_order,
            "chatbot": chatbot,
            "message": message,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "send": send,
            "clear_chat": clear_chat,
        },
    }
