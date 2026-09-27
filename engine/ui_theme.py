"""BrainEngine's Gradio theme and stable, class-based CSS overrides."""

from __future__ import annotations

from typing import Any


FONT_STACK = (
    "Inter, 'Noto Sans JP', 'Yu Gothic UI', Meiryo, "
    "system-ui, -apple-system, BlinkMacSystemFont, sans-serif"
)
MONO_FONT_STACK = "'Cascadia Code', 'Noto Sans Mono', Consolas, monospace"


def create_theme() -> Any:
    """Create the Gradio theme lazily so non-UI utilities stay lightweight."""

    import gradio as gr

    return gr.themes.Base(
        primary_hue=gr.themes.colors.amber,
        secondary_hue=gr.themes.colors.green,
        neutral_hue=gr.themes.colors.slate,
        font=(gr.themes.GoogleFont("Inter"), "Noto Sans JP", "sans-serif"),
        font_mono=("Cascadia Code", "Noto Sans Mono", "monospace"),
    ).set(
        body_background_fill="#101613",
        body_background_fill_dark="#101613",
        body_text_color="#e6e2d6",
        body_text_color_dark="#e6e2d6",
        block_background_fill="#18211c",
        block_background_fill_dark="#18211c",
        block_border_color="#2a3830",
        block_border_color_dark="#2a3830",
        block_label_text_color="#b9c4bb",
        block_label_text_color_dark="#b9c4bb",
        input_background_fill="#111915",
        input_background_fill_dark="#111915",
        input_border_color="#34473b",
        input_border_color_dark="#34473b",
        button_primary_background_fill="#e8c47a",
        button_primary_background_fill_dark="#e8c47a",
        button_primary_background_fill_hover="#f0d394",
        button_primary_background_fill_hover_dark="#f0d394",
        button_primary_text_color="#20241c",
        button_primary_text_color_dark="#20241c",
        button_secondary_background_fill="#223028",
        button_secondary_background_fill_dark="#223028",
        button_secondary_border_color="#3b5042",
        button_secondary_border_color_dark="#3b5042",
        button_secondary_text_color="#e6e2d6",
        button_secondary_text_color_dark="#e6e2d6",
        border_color_primary="#34473b",
        border_color_primary_dark="#34473b",
        color_accent_soft="#26392e",
        color_accent_soft_dark="#26392e",
        shadow_drop="0 8px 24px rgb(0 0 0 / 18%)",
        block_radius="10px",
        button_large_radius="8px",
        input_radius="8px",
    )


CUSTOM_CSS = f"""
:root {{
  --brain-success: #9db4a6;
  --brain-warning: #e8c47a;
  --brain-danger: #d99a8f;
}}

.gradio-container {{
  font-family: {FONT_STACK};
  box-sizing: border-box;
  width: calc(100vw - 2rem) !important;
  max-width: 1480px !important;
  min-width: 0 !important;
  margin: 0 auto;
}}

/* A tab with only narrow form controls can otherwise make the mounted Gradio
   surface shrink-wrap to its contents.  Keep every page on the same outer
   measure while still allowing controls to wrap on genuinely small screens. */
.gradio-container > .main,
.gradio-container [role="tabpanel"] {{
  box-sizing: border-box;
  width: 100% !important;
  min-width: 0 !important;
}}

.brain-header {{
  border-bottom: 1px solid #2a3830;
  margin-bottom: 0.75rem;
  padding-bottom: 0.6rem;
}}

.brain-status-ok {{ color: var(--brain-success) !important; }}
.brain-status-warning {{ color: var(--brain-warning) !important; }}
.brain-status-error {{ color: var(--brain-danger) !important; }}

/* Gradio error toasts use the page's light text variable even when their
   surface is light. Keep error notifications readable in this dark theme. */
.toast-body.error {{
  background: #ffffff !important;
  border-color: #d1d5db !important;
  color: #20241c !important;
}}

.toast-body.error .toast-title,
.toast-body.error .toast-count,
.toast-body.error .toast-message-text,
.toast-body.error .toast-close {{
  color: #20241c !important;
}}

.toast-body.error .toast-icon {{
  color: #b42318 !important;
}}

/* Tabs use a light hover surface in Gradio 6. Keep its label readable. */
[role="tab"]:hover {{
  color: #20241c !important;
}}

.brain-dashboard-card,
.brain-dashboard-note {{
  background: #0c110e !important;
  color: #e6e2d6 !important;
  border: 1px solid #2a3830;
  border-radius: 10px;
  padding: 0.8rem 1rem;
}}

.brain-dashboard-card code,
.brain-dashboard-note code {{
  background: #152019 !important;
  color: #e6e2d6 !important;
  border: 1px solid #34473b;
}}

/* The read-only File component used for CSV exports otherwise keeps a
   light file-row surface even inside the dark theme. */
.brain-export-file,
.brain-export-file > div,
.brain-export-file [class*="file"],
.brain-export-file a,
.brain-export-file button {{
  background: #0c110e !important;
  color: #e6e2d6 !important;
  border-color: #34473b !important;
}}

.brain-export-file svg {{
  color: #e6e2d6 !important;
  fill: currentColor;
}}

/* Keep the generated CSV download in a compact, button-height row instead of
   reserving the large drop-zone area used by interactive File components. */
.brain-export-file,
.brain-export-file > div,
.brain-export-file [class*="file-preview"],
.brain-export-file [class*="file-item"] {{
  min-height: 0 !important;
}}

.brain-export-file > div,
.brain-export-file [class*="file-preview"],
.brain-export-file [class*="file-item"] {{
  padding-top: 0.25rem !important;
  padding-bottom: 0.25rem !important;
}}

.brain-editor textarea,
.brain-log textarea,
.brain-code textarea {{
  font-family: {MONO_FONT_STACK} !important;
}}

.brain-log textarea {{
  background: #0c110e !important;
  color: #cbd4cc !important;
}}

/* Gradio 6 renders chatbot messages and dropdown menus in light-mode portals
   even when the surrounding Blocks theme is dark. Keep those surfaces aligned
   with BrainEngine's dark palette and retain readable contrast. */
.brain-chat,
.brain-chat > div,
.brain-chat .message-wrap,
.brain-chat .bubble-wrap,
.brain-chat .message,
.brain-chat .bot,
.brain-chat .user,
.brain-chat [class*="message"] {{
  background: #0c110e !important;
  color: #e6e2d6 !important;
}}

.brain-chat .prose,
.brain-chat .prose *,
.brain-chat p,
.brain-chat li,
.brain-chat code,
.brain-chat pre {{
  color: #e6e2d6 !important;
}}

.brain-chat code,
.brain-chat pre {{
  background: #152019 !important;
  border-color: #34473b !important;
}}

[role="listbox"],
[role="listbox"] ul,
[role="option"],
.options,
.options ul,
.options li {{
  background: #0c110e !important;
  color: #e6e2d6 !important;
}}

[role="listbox"],
.options {{
  max-height: min(50vh, 32rem) !important;
  overflow-y: auto !important;
  overscroll-behavior: contain;
}}

[role="option"]:hover,
[role="option"][aria-selected="true"],
.options li:hover,
.options li.selected {{
  background: #26392e !important;
  color: #ffffff !important;
}}

.brain-lorebook-assignment-scroll {{
  box-sizing: border-box;
  max-height: min(64vh, 720px);
  overflow-y: auto;
  overscroll-behavior: contain;
  padding-right: 0.35rem;
  scrollbar-gutter: stable;
}}

.brain-lorebook-agent-card {{
  min-width: 0 !important;
}}

.brain-lorebook-agent-info {{
  box-sizing: border-box;
  padding: 0.4rem 0.75rem 0.45rem !important;
}}

.brain-lorebook-agent-label,
.brain-lorebook-active-books,
.brain-lorebook-active-books .prose {{
  min-width: 0 !important;
  white-space: normal !important;
  overflow-wrap: anywhere;
  word-break: break-word;
}}

@media (max-width: 720px) {{
  .gradio-container {{
    width: calc(100vw - 1rem) !important;
    padding-inline: 0.65rem !important;
  }}
}}
""".strip()
