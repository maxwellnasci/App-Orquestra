"""
Orquestra Terminal IDE v3.0 — Interface TUI
Fases 1+2: Painéis toggleáveis + seleção de modelos  (CONCLUÍDO)
Fase 3:    Chat conectado ao backend (não bloqueante)  (ESTE ARQUIVO)
Fase 4:    GPS — editor segue o agente em tempo real   (ESTE ARQUIVO)
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
import time
import os
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import Label, Button, RichLog, TextArea, Markdown, OptionList, DirectoryTree, TabbedContent, TabPane
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from textual.message import Message
from textual.binding import Binding
from textual import work, events
from rich.markup import escape as rich_escape


# ─── Utilitário: encurtamento de nomes para o header ─────────────────────────

def shorten_name(name: str) -> str:
    """Encurta o nome do modelo para caber nos botões do header (max 18 chars)."""
    if not name:
        return "-"
    name = name.replace("gemini-", "")
    name = name.replace("claude-", "")
    name = name.replace("-high", "-hi")
    name = name.replace("-medium", "-me")
    name = name.replace("-low", "-lo")
    name = name.replace("-thinking", "-thk")
    if len(name) > 18:
        name = name[:16] + ".."
    return name


# ─── Editor: linguagem por extensão (só as nativas do Textual/tree-sitter) ───

_EXT_TO_LANGUAGE = {
    ".py": "python", ".pyw": "python",
    ".md": "markdown", ".markdown": "markdown",
    ".json": "json",
    ".toml": "toml",
    ".yml": "yaml", ".yaml": "yaml",
    ".html": "html", ".htm": "html",
    ".css": "css",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".sql": "sql",
    ".java": "java",
    ".sh": "bash", ".bash": "bash",
    ".xml": "xml",
}


def _language_for_path(path: str) -> str | None:
    """Linguagem do TextArea pela extensão. None = texto puro (sem highlight)."""
    return _EXT_TO_LANGUAGE.get(Path(path).suffix.lower())


# ─── Modo Aprendizado: instruções do Revisor read-only ────────────────────────

_SUGESTAO_FORMAT_HINT = (
    "\n\nPara cada problema com correção objetiva, emita também um bloco:\n"
    '<SUGESTAO arquivo="caminho/relativo" linha_inicio="N" linha_fim="M" motivo="frase curta">\n'
    "código corrigido, já pronto pra substituir as linhas N a M\n"
    "</SUGESTAO>\n"
    "O usuário vê isso como um cartão com Aceitar/Recusar — não repita esse código no texto "
    "solto, só a explicação. Use linha_inicio/linha_fim EXATAMENTE como aparecem no arquivo "
    "(a primeira linha é 1, não 0). Se o problema não tiver uma correção objetiva de código "
    "(ex: sugestão arquitetural ampla), só comente em texto, sem <SUGESTAO>."
)


def _auto_review_instruction(path: str) -> str:
    return (
        f"Modo Aprendizado: o usuário acabou de salvar o arquivo {path} manualmente "
        "(ele mesmo escreveu — você está em modo leitura, não pode editar nada). "
        "Dê um feedback RÁPIDO e direto: bugs, riscos de segurança ou pontos de estilo "
        "importantes. Se estiver tudo bem, diga isso em 1 frase curta. Seja objetivo — "
        "não repita o código de volta."
        + _SUGESTAO_FORMAT_HINT
    )


def _deep_review_instruction(path: str) -> str:
    return (
        f"Modo Aprendizado: faça uma revisão PROFUNDA do arquivo {path}, que o usuário "
        "escreveu manualmente (você está em modo leitura, não pode editar nada). Aponte "
        "bugs, riscos de segurança, problemas de arquitetura e boas práticas — explique "
        "o PORQUÊ de cada ponto, como um mentor ensinando, não só corrigindo. Estruture "
        "a resposta em tópicos."
        + _SUGESTAO_FORMAT_HINT
    )


# ─── Modal de Seleção de Modelos ──────────────────────────────────────────────

class ModelSelectionModal(ModalScreen[str]):
    """Modal de tela cheia para escolher modelos agrupados por empresa."""
    CSS = """
    ModelSelectionModal {
        align: center middle;
        background: $background 50%;
    }

    #modal-container {
        width: 80%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }

    #model-list {
        height: 1fr;
        margin: 1 0;
        border: solid $primary-background;
    }
    """

    def __init__(self, role: str, grouped_models: dict):
        super().__init__()
        self.role = role
        self.grouped_models = grouped_models
        # Mapeia ÍNDICE absoluto → value real do modelo (nunca falha)
        self._index_to_value: dict[int, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Label(f"Selecione o modelo para: {self.role}", classes="panel-title")
            options = []
            idx = 0
            for company, models in self.grouped_models.items():
                if models:
                    options.append(Option(f"▼ {company}", disabled=True))
                    idx += 1
                    for label, value in models:
                        self._index_to_value[idx] = value
                        options.append(Option(f"    {label}"))
                        idx += 1
            yield OptionList(*options, id="model-list")
            yield Button("✕  Cancelar", id="btn-cancel", variant="error")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        value = self._index_to_value.get(event.option_index)
        if value:
            self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)


# ─── Stub mínimo de LiveSection para usar as funções do orquestra.py ─────────

class _TUILiveSection:
    """Shim que substitui a LiveSection do Rich por callbacks para a TUI.
    Permite chamar _run_worker_headless / _run_orchestrator_stream do orquestra.py
    sem alterar nada naquele arquivo.

    on_tool (PLANO_PALCO_CHAT.md, Etapa 0e): callback opcional, separado de
    on_status — cada tool_call() também é reportado aqui, além de ir pro
    status. Hoje (antes do palco existir) fica None em todo mundo e o
    comportamento é idêntico ao de sempre (tool_call só no status); quando a
    aba "⚡ Atividade" existir (Etapa 2), passa a receber uma linha por
    chamada, sem tirar o status de 1 linha que já funciona."""
    def __init__(self, label: str, on_line, on_close, on_status=None, on_tool=None):
        self.label = label
        self._on_line = on_line
        self._on_close = on_close
        self._on_status = on_status
        self._on_tool = on_tool

    def open(self): pass
    def end_line(self): pass

    def write(self, text: str):
        if text.strip():
            self._on_line(text)

    def tool_call(self, text: str):
        if self._on_tool:
            self._on_tool(text)
        if self._on_status:
            self._on_status(f"[dim]⏳ {text}[/dim]")
        elif not self._on_tool:
            self._on_line(f"[dim]⏳ {text}[/dim]")

    def close(self, ok: bool = True, error_detail: str = "", changes_summary: str = ""):
        self._on_close(ok, error_detail, changes_summary)


# ─── Campo de chat multi-linha ────────────────────────────────────────────────

class ChatInput(TextArea):
    """Campo de chat baseado em TextArea (não em Input).

    O Input padrão do Textual descarta tudo além da primeira linha ao colar
    texto (`event.text.splitlines()[0]` em _on_paste) — por isso era impossível
    colar um texto grande/multi-linha para o Orquestrador ler. TextArea cola o
    texto inteiro. Enter continua enviando a mensagem (como antes); Ctrl+J
    insere uma quebra de linha manual.
    """

    class Submitted(Message):
        def __init__(self, chat_input: "ChatInput", value: str) -> None:
            self.chat_input = chat_input
            self.value = value
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return
        if event.key == "ctrl+j":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)


# ─── Aplicação Principal ──────────────────────────────────────────────────────

class OrquestraTUI(App):
    """Interface Orquestra Terminal IDE v3.0"""

    BINDINGS = [
        Binding("ctrl+s", "save_file", "Salvar", show=False),
        Binding("ctrl+w", "close_tab", "Fechar aba", show=False),
        # PLANO_PALCO_CHAT.md, Etapa 1 — atalhos do palco/chat-dock.
        Binding("ctrl+1", "stage_tab('stage-activity')", "Atividade", show=False),
        Binding("ctrl+2", "stage_tab('stage-changes')", "Mudanças", show=False),
        Binding("ctrl+3", "stage_tab('stage-editor')", "Editor", show=False),
        Binding("ctrl+4", "stage_tab('stage-idea')", "Idea Studio", show=False),
        Binding("ctrl+b", "toggle_files", "Arquivos", show=False),
        Binding("ctrl+up", "resize_chat(1)", "Chat maior", show=False),
        Binding("ctrl+down", "resize_chat(-1)", "Chat menor", show=False),
        Binding("ctrl+l", "focus_chat", "Foco no chat", show=False),
    ]

    _CHAT_DOCK_SIZES = ("compact", "normal", "tall")
    _SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    CSS = """
    Screen {
        layout: vertical;
    }

    #app-title {
        width: auto;
        padding: 0 2;
        color: $primary;
        text-style: bold;
        content-align: left middle;
    }

    #top-bar {
        height: 3;
        layout: horizontal;
        dock: top;
        background: $boost;
        border-bottom: solid $primary;
        padding: 0 1;
    }

    #controls {
        layout: horizontal;
        width: 1fr;
        align: left middle;
    }

    #models {
        layout: horizontal;
        width: auto;
        align: right middle;
    }

    .toggle-btn {
        min-width: 6;
        height: 1;
        margin: 0 1;
        border: none;
        background: $panel;
        color: $text-muted;
    }

    .toggle-btn:hover {
        background: $primary;
        color: $text;
    }

    .toggle-btn.active {
        background: $success;
        color: $text;
        text-style: bold;
    }

    .model-btn {
        min-width: 25;
        height: 1;
        margin: 0 1;
        border: none;
        background: $surface;
        color: $text;
    }

    .model-btn:hover {
        background: $primary;
    }

    #main-content {
        layout: horizontal;
        height: 1fr;
    }

    .panel {
        border-right: solid $primary-background;
        height: 1fr;
    }

    .panel-title {
        background: $boost;
        color: $text;
        width: 100%;
        text-align: center;
        text-style: bold;
        padding: 1 0;
    }

    #file-panel {
        width: 25;
    }

    #stage-tabs {
        width: 1fr;
        height: 1fr;
    }

    #idea-image {
        height: 3fr;
        overflow: auto;
        align: center top;
        background: black;
        padding: 1;
    }

    #idea-md {
        height: 1fr;
        overflow-y: auto;
    }

    #chat-dock {
        height: 14;
        min-height: 8;
        border-top: heavy $primary;
        background: $surface;
    }

    #chat-dock.compact {
        height: 8;
    }

    #chat-dock.tall {
        height: 50%;
    }

    #chat-header {
        height: 1;
        background: $boost;
        padding: 0 1;
    }

    #chat-title {
        width: auto;
        color: $primary;
        text-style: bold;
    }

    #status-indicator {
        width: 1fr;
        text-align: right;
        color: $text-muted;
    }

    #chat-input {
        height: 3;
    }

    #activity-log, #diff-log {
        height: 1fr;
    }

    #changes-header {
        height: 1;
        padding: 0 1;
        background: $boost;
    }

    .hidden {
        display: none;
    }

    #watchdog-footer {
        height: 3;
        dock: bottom;
        background: $surface;
        color: $text;
        content-align: left middle;
        padding: 0 2;
        border-top: solid $primary-background;
    }

    #watchdog-footer.alert {
        background: $error;
        color: $text;
        text-style: bold;
    }

    #usage-footer {
        height: 1;
        dock: bottom;
        background: $surface;
        color: $text-muted;
        content-align: left middle;
        padding: 0 2;
    }

    #plan-panel {
        height: auto;
        max-height: 10;
        overflow-y: auto;
        background: $boost;
        border-top: solid $primary-background;
        padding: 0 1;
    }

    #plan-panel.hidden {
        display: none;
    }

    .plan-step {
        height: 1;
        padding: 0 1;
    }

    RichLog {
        height: 1fr;
        padding: 1;
    }

    #chat-log {
        padding: 0 1;
    }

    #action-bar, #plan-bar {
        height: 3;
        layout: horizontal;
        align: center middle;
        padding: 0 1;
        background: $boost;
        border-top: solid $primary-background;
    }

    #action-bar.hidden, #plan-bar.hidden {
        display: none;
    }

    #editor-toolbar {
        height: 3;
        layout: horizontal;
        align: center middle;
        padding: 0 1;
        background: $boost;
        border-bottom: solid $primary-background;
    }

    #suggestions-panel {
        height: auto;
        max-height: 12;
        overflow-y: auto;
        background: $boost;
        border-bottom: solid $primary-background;
    }

    .suggestion-card {
        height: auto;
        padding: 1;
        border-bottom: solid $primary-background;
    }

    .suggestion-label {
        color: $text;
        margin-bottom: 1;
    }

    .suggestion-buttons {
        height: 3;
        layout: horizontal;
    }

    .action-btn {
        margin: 0 1;
        min-width: 18;
    }
    """

    def compose(self) -> ComposeResult:
        # ── Header ───────────────────────────────────────────────────────────
        # PLANO_PALCO_CHAT.md, Etapa 1: Editor/Idea Studio saem do header —
        # viram abas do palco (#stage-tabs), não mais toggles independentes.
        with Container(id="top-bar"):
            with Container(id="controls"):
                yield Label("🎼 ORQUESTRA", id="app-title")
                yield Button("📁 Arquivos", id="btn-files", classes="toggle-btn")
                yield Button("🎓 Aprendizado", id="btn-learning", classes="toggle-btn")
            with Container(id="models"):
                yield Button("Carregando...", id="btn-sel-orq", classes="model-btn")
                yield Button("Carregando...", id="btn-sel-op", classes="model-btn")
                yield Button("Carregando...", id="btn-sel-rev", classes="model-btn")

        # ── Palco: Atividade ao vivo / Mudanças (diff) / Editor / Idea Studio ──
        with Horizontal(id="main-content"):
            with Vertical(id="file-panel", classes="panel hidden"):
                yield Label("📁 ARQUIVOS", classes="panel-title")
                yield DirectoryTree("./")

            with TabbedContent(id="stage-tabs", initial="stage-activity"):
                with TabPane("⚡ Atividade", id="stage-activity"):
                    yield RichLog(id="activity-log", markup=True, wrap=True, min_width=1)
                with TabPane("± Mudanças", id="stage-changes"):
                    yield Label("Nenhuma mudança pendente.", id="changes-header")
                    yield RichLog(id="diff-log", markup=True, wrap=False, min_width=1)
                with TabPane("📝 Editor", id="stage-editor"):
                    with Container(id="editor-toolbar", classes="hidden"):
                        yield Button("🔍 Revisão profunda", id="btn-deep-review", classes="action-btn")
                    yield Vertical(id="suggestions-panel", classes="hidden")
                    yield TabbedContent(id="editor-tabs")
                with TabPane("🎨 Idea Studio", id="stage-idea"):
                    yield Container(id="idea-image")
                    yield Markdown(
                        "As prévias visuais do **Open Design** são renderizadas aqui, "
                        "dentro do Orquestra — sem abrir navegador.",
                        id="idea-md",
                    )

        # ── Chat: pequeno, embaixo, largura cheia (não é mais coluna lateral) ──
        with Vertical(id="chat-dock"):
            with Horizontal(id="chat-header"):
                yield Label("💬 Orquestrador", id="chat-title")
                yield Label("", id="status-indicator", classes="hidden")
            # min_width baixo: o RichLog nunca quebra linha abaixo do seu
            # min_width (default 78!) — sem isso a mensagem "vazava" e exigia
            # rolagem horizontal pra ler.
            yield RichLog(id="chat-log", markup=True, wrap=True, min_width=1)
            # Painel "📋 Plano" — checklist persistente do <PLANO>/<PIPELINE>
            # ativo (some quando Aprovar/Rejeitar resolve a tarefa).
            yield Vertical(id="plan-panel", classes="hidden")
            # Barra do PLANO — aparece quando o Orquestrador propõe um <PLANO>
            with Container(id="plan-bar", classes="hidden"):
                yield Button("▶ Executar plano", id="btn-exec-plan", classes="action-btn", variant="success")
                yield Button("✏️ Ajustar", id="btn-adjust-plan", classes="action-btn")
            # Barra de aprovação — aparece só quando o Operador termina
            with Container(id="action-bar", classes="hidden"):
                yield Button("✅ Aprovar", id="btn-approve", classes="action-btn", variant="success")
                yield Button("❌ Rejeitar", id="btn-reject", classes="action-btn", variant="error")
                yield Button("🔍 Revisar", id="btn-review", classes="action-btn")
            yield ChatInput(placeholder="Digite aqui... [Enter envia · Ctrl+J quebra linha]", id="chat-input")

        # ── Rodapés ───────────────────────────────────────────────────────────
        yield Label("🐕 Watchdog: Aguardando alterações para revisão...", id="watchdog-footer")
        yield Label("💰 Uso: $0.00 · 0 chamadas", id="usage-footer")

    # ── Inicialização ─────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        """Carrega modelos e configuração ao iniciar."""
        import orquestra
        self.grouped_models: dict = {}
        self._model_provider: dict = {}
        cfg = orquestra.load_config()
        orq = cfg.get("roles", {}).get("orquestrador", {})
        ops = cfg.get("workers", {}).get("operador", {})
        rev = cfg.get("workers", {}).get("revisor", {})

        self._orq_model: str = orq.get("model", "deepseek-v4-flash")
        self._orq_binary: str = orq.get("binary", "brain")
        self._orq_provider: str = orq.get("provider", "deepseek")
        self._op_model: str = ops.get("model", "claude-sonnet-5")
        self._rev_model: str = rev.get("model", "claude-opus-5")

        self._open_files: dict[str, dict] = {}   # pane_id -> {"path", "original_text", "dirty"}
        self._path_to_pane: dict[str, str] = {}  # caminho absoluto -> pane_id
        self._tab_counter: int = 0
        self._task_pending: bool = False        # Há tarefa aguardando aprovação?
        self._pending_changes: str = ""         # Resumo das mudanças do Operador
        self._plan_pending: str = ""            # <PLANO> aguardando "Executar"
        self._last_user_prompt: str = ""        # último pedido do usuário (p/ re-enviar no "Executar")
        self._pipeline_rest = None              # etapas restantes de um <PIPELINE> pausado na aprovação
        self._learning_mode: bool = bool(cfg.get("ui", {}).get("learning_mode", False))
        self._last_auto_review: float = 0.0     # cooldown da revisão automática ao salvar
        self._last_editor_watchdog: float = 0.0  # cooldown do Cão de Guarda em edição manual
        self._pending_suggestions: dict[str, dict] = {}  # sug_id -> {"path","linha_inicio","linha_fim","motivo","codigo"}
        self._suggestion_counter: int = 0
        self._pending_attribution: dict | None = None  # agente/instrução/arquivos da mudança aguardando aprovação
        self._plan_steps: list[dict] = []       # [{"text","status"}] do <PLANO>/<PIPELINE> ativo — painel "📋 Plano"

        # PLANO_PALCO_CHAT.md, Etapa 2 — palco segue a atividade + spinner.
        # Contador, não bool (furo nº2 da revisão): <DELEGATE> soltos rodam em
        # paralelo (várias chamadas de _run_worker sem esperar uma da outra) —
        # um bool zeraria assim que o PRIMEIRO worker paralelo terminasse.
        self._active_workers: int = 0
        self._stage_pinned: bool = False        # usuário trocou de aba na mão durante uma tarefa
        self._status_text: str = ""             # texto "cru" do status (sem frame do spinner)
        self._spinner_idx: int = 0
        self._spinner_started_at: float = 0.0
        self._spinner_timer = None

        import orquestra
        self._session_usage = orquestra.get_session_usage_totals(orquestra.get_project_id())

        log = self.query_one("#chat-log", RichLog)
        log.write("[bold cyan]🎼 Orquestrador[/bold cyan] Bem-vindo ao Orquestra. Converse comigo — eu planejo e a equipe executa.\n")

        # Transparência: sem isso, um projeto sem git faz Aprovar/Rejeitar,
        # Cão de Guarda, autoria por IA e gate por risco ficarem silenciosamente
        # inertes — o usuário via as coisas acontecendo sem nenhuma rede de
        # segurança visível e sem saber por quê.
        if not (Path(orquestra.get_project_dir()) / ".git").is_dir():
            log.write(
                "\n[bold yellow]⚠ Esta pasta não é um repositório git.[/bold yellow] "
                "[dim]Aprovar/Rejeitar, Cão de Guarda, autoria por IA e gate por risco "
                "dependem de git e ficam sem efeito aqui. Rode `git init` na pasta se "
                "quiser essas proteções ativas.[/dim]\n"
            )

        self.query_one("#btn-sel-orq").label = f"Orq: {shorten_name(self._orq_model)}"
        self.query_one("#btn-sel-op").label  = f"Op: {shorten_name(self._op_model)}"
        self.query_one("#btn-sel-rev").label = f"Rev: {shorten_name(self._rev_model)}"

        if self._learning_mode:
            btn = self.query_one("#btn-learning")
            btn.add_class("active")
            btn.label = "🎓 Aprendizado [✓]"
            self.query_one("#editor-toolbar").remove_class("hidden")

        self._update_usage_footer()
        self._refresh_changes_panel()  # mudança pendente de uma sessão anterior, se houver
        self._load_models_and_config()

    @work(exclusive=True)
    async def _load_models_and_config(self) -> None:
        """Carrega modelos de todos os provedores e config em background."""
        import asyncio
        import orquestra

        _AGY_FALLBACK = [
            ("gemini-3.1-pro-high",   "Gemini 3.1 Pro High"),
            ("gemini-3.1-pro-low",    "Gemini 3.1 Pro Low"),
            ("gemini-3.6-flash-high", "Gemini 3.6 Flash High"),
        ]

        loop = asyncio.get_running_loop()
        (orq_raw, claude_raw, copilot_raw, deepseek_raw, codex_raw, ollama_raw, opencode_raw) = await asyncio.gather(
            loop.run_in_executor(None, orquestra._fetch_agy_models),
            loop.run_in_executor(None, orquestra._fetch_claude_models),
            loop.run_in_executor(None, orquestra._fetch_copilot_models),
            loop.run_in_executor(None, orquestra._fetch_deepseek_models),
            loop.run_in_executor(None, orquestra._fetch_codex_models),
            loop.run_in_executor(None, orquestra._fetch_ollama_models),
            loop.run_in_executor(None, orquestra._fetch_opencode_models),
        )

        if not orq_raw:
            orq_raw = _AGY_FALLBACK

        # grouped_models: {empresa: [(label_display, value), ...]}
        self.grouped_models = {
            "☁️ Ollama Cloud (Kimi)":    [(label, val) for val, label in ollama_raw],
            "🚀 Antigravity (Gemini)": [(label, val) for val, label in orq_raw],
            "🧠 Anthropic Claude":      [(label, val) for val, label in claude_raw],
            "🛸 GitHub Copilot":        [(label, val) for val, label in copilot_raw],
            "🐋 DeepSeek":             [(label, val) for val, label in deepseek_raw],
            "🤖 OpenAI Codex":         [(label, val) for val, label in codex_raw],
            "🌐 OpenCode (OpenRouter)": [(label, val) for val, label in opencode_raw],
        }

        # value do modelo → provedor (pra saber como falar com ele).
        _COMPANY_PROVIDER = {
            "☁️ Ollama Cloud (Kimi)": "ollama-cloud",
            "🚀 Antigravity (Gemini)": "gemini",
            "🧠 Anthropic Claude": "anthropic",
            "🛸 GitHub Copilot": "copilot",
            "🐋 DeepSeek": "deepseek",
            "🤖 OpenAI Codex": "openai",
            "🌐 OpenCode (OpenRouter)": "opencode",
        }
        self._model_provider = {}
        for company, models in self.grouped_models.items():
            for _label, val in models:
                self._model_provider.setdefault(val, _COMPANY_PROVIDER.get(company, "ollama-cloud"))

        # Os defaults (orq/op/rev) já vieram do ~/.orquestra.json em on_mount.
        # Aqui só populamos as listas dos modais de troca de modelo. As labels
        # dos botões já foram setadas em on_mount.

    def _binary_from_model(self, model_id: str) -> str:
        """Infere qual binary usar a partir do ID do modelo."""
        m = model_id.lower()
        if "openrouter" in m:
            return "opencode"  # ANTES dos outros checks: "deepseek"/"qwen"/"glm" bateriam errado
        if "claude" in m and "haiku" not in m and "deepseek" not in m:
            return "claude"
        if "gpt" in m or "codex" in m or "o1" in m or "o3" in m:
            if "oss" in m: return "ollama-cloud"
            return "codex"
        if "copilot" in m or m.startswith("claude-haiku"):
            return "copilot"
        if "deepseek" in m:
            return "claude"  # DeepSeek usa interface claude com endpoint alternativo
        if "kimi" in m or "nemotron" in m or "mistral" in m or "minimax" in m or "glm" in m or "gemma" in m or "qwen" in m:
            return "ollama-cloud"
        return "agy"  # Gemini / Antigravity

    def _agent_models(self) -> dict:
        """Só provedores que têm CLI de agente com ferramentas (Operador/Revisor).
        Fora: Ollama Cloud (chat puro, sem ferramentas — só serve pro Orquestrador)."""
        return {k: v for k, v in self.grouped_models.items() if "Ollama" not in k}

    def _set_worker_model(self, slot: str, selected: str) -> bool:
        """Valida e persiste a troca de modelo de um operário. Retorna False
        (e avisa no chat) se o modelo não roda como agente com ferramentas."""
        binary = self._binary_from_model(selected)
        if binary in ("ollama-cloud", "brain"):
            self.query_one("#chat-log", RichLog).write(
                f"\n[yellow]⚠ '{selected}' é um modelo de chat puro (sem ferramentas) — "
                f"não serve pro {slot}, que precisa ler/escrever arquivos. "
                f"Escolha um Claude, Gemini, Codex ou DeepSeek.[/yellow]\n")
            return False
        self._persist_agent(slot, binary=binary, model=selected)
        return True

    def _brain_provider_from_model(self, m: str) -> str:
        """Fallback quando o modelo não está no _model_provider (lista não carregou)."""
        m = m.lower()
        if m.startswith("claude") or any(x in m for x in ("sonnet", "opus", "haiku")):
            return "anthropic"
        if m.startswith("gemini"):
            return "gemini"
        if m.startswith("deepseek-v4") or m == "deepseek-chat":
            return "deepseek"
        if m.startswith(("gpt-3", "gpt-4", "gpt-5", "o1", "o3", "o4")):
            return "openai"
        return "ollama-cloud"  # kimi, gpt-oss, nemotron, glm, qwen, minimax...

    def _orch_route(self, model: str) -> tuple:
        """Modelo escolhido pro Orquestrador → (binary, provider). Usa a CLI logada
        no terminal quando dá (Gemini→agy, Claude→claude, GPT→codex); DeepSeek e
        Ollama vão pelo chat API (binary 'brain')."""
        m = model.lower()
        grp = self._model_provider.get(model, "")
        if m.startswith("gemini") or grp == "gemini":
            return "agy", ""
        if m.startswith("claude") or any(x in m for x in ("sonnet", "opus")) or grp == "anthropic":
            return "claude", ""
        if m.startswith("deepseek-v4") or m == "deepseek-chat" or grp == "deepseek":
            return "brain", "deepseek"
        if grp == "ollama-cloud" or any(x in m for x in ("kimi", "gpt-oss", "nemotron", "glm", "qwen", "minimax", "gemma")):
            return "brain", "ollama-cloud"
        if m.startswith(("gpt-", "o1", "o3", "o4")) or grp == "openai":
            return "codex", ""   # usa o login do codex CLI
        return "brain", "ollama-cloud"

    def _check_brain_key(self, provider: str) -> None:
        """Avisa no chat se o cérebro escolhido precisa de uma chave que não está no ambiente."""
        import os
        need = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                "gemini": "GEMINI_API_KEY"}
        var = need.get(provider)
        if var and not (os.environ.get(var) or (provider == "gemini" and os.environ.get("GOOGLE_API_KEY"))):
            self.query_one("#chat-log", RichLog).write(
                f"\n[yellow]⚠ Esse modelo precisa de {var}. Adicione com  "
                f"[bold]/senha {var} <sua-chave>[/bold]  e reabra o Orquestra.[/yellow]\n")

    def _persist_agent(self, slot: str, **fields) -> None:
        """Grava a escolha de modelo/binary de um agente no ~/.orquestra.json,
        pra TUI, REPL e próxima sessão concordarem."""
        import orquestra, json
        config = orquestra.load_config()
        if slot == "orquestrador":
            target = config.setdefault("roles", {}).setdefault("orquestrador", {})
        else:
            target = config.setdefault("workers", {}).setdefault(slot, {})
        target.update(fields)
        if slot == "orquestrador" and fields.get("binary") != "brain":
            target.pop("provider", None)   # agy/claude/codex não usam provider
        _model = str(fields.get("model", ""))
        _wants_ds_env = "deepseek-v4" in _model and fields.get("binary") == "claude"
        if _wants_ds_env:
            target["env_file"] = str(orquestra.DEEPSEEK_ENV)
        elif fields.get("binary") in ("agy", "codex", "brain") or (slot != "orquestrador" and fields.get("binary") != "claude"):
            target.pop("env_file", None)
        with open(orquestra.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    # ── Eventos de UI: Toggles e Modelos ──────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # PLANO_PALCO_CHAT.md, Etapa 1: Editor e Idea Studio saíram daqui —
        # agora são abas do palco (#stage-tabs), trocadas por _stage_show.
        # Só o painel Arquivos continua sendo um toggle de mostrar/ocultar.
        panel_map = {
            "btn-files": ("#file-panel", "📁 Arquivos"),
        }

        # Toggles de painéis
        if event.button.id in panel_map:
            panel_id, base_label = panel_map[event.button.id]
            panel = self.query_one(panel_id)
            panel.toggle_class("hidden")
            if panel.has_class("hidden"):
                event.button.remove_class("active")
                event.button.label = base_label
            else:
                event.button.add_class("active")
                event.button.label = f"{base_label} [✓]"
            return

        # Toggle Modo Aprendizado — flag de comportamento, não abre/fecha painel
        if event.button.id == "btn-learning":
            import orquestra, json
            self._learning_mode = not self._learning_mode
            event.button.set_class(self._learning_mode, "active")
            event.button.label = "🎓 Aprendizado [✓]" if self._learning_mode else "🎓 Aprendizado"
            self.query_one("#editor-toolbar").set_class(not self._learning_mode, "hidden")
            config = orquestra.load_config()
            config.setdefault("ui", {})["learning_mode"] = self._learning_mode
            with open(orquestra.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            return

        # Revisão profunda sob demanda (Modo Aprendizado) do arquivo aberto no Editor
        if event.button.id == "btn-deep-review":
            tabs = self.query_one("#editor-tabs", TabbedContent)
            pane_id = tabs.active
            info = self._open_files.get(pane_id) if pane_id else None
            if info is None:
                self.notify("Abra um arquivo no Editor primeiro.", severity="warning")
                return
            self._run_worker("revisor", _deep_review_instruction(info["path"]), read_only=True)
            return

        # Cartões de sugestão (Bloco 5) — Aceitar/Recusar por trecho
        if event.button.id and event.button.id.startswith("sug-accept-"):
            self._accept_suggestion(event.button.id.removeprefix("sug-accept-"))
            return
        if event.button.id and event.button.id.startswith("sug-reject-"):
            self._reject_suggestion(event.button.id.removeprefix("sug-reject-"))
            return

        # Seleção de Modelos
        if event.button.id == "btn-sel-orq":
            # O Orquestrador roda em MODO PLANO (read-only). Gemini→login do agy,
            # Claude→login do claude, GPT→login do codex, DeepSeek/Kimi→chat API.
            # Copilot fica fora (não tem chat direto nem plan-mode utilizável).
            brains = {k: v for k, v in self.grouped_models.items() if "Copilot" not in k}
            btn = event.button

            def update_orq(selected: str | None):
                if not selected:
                    return
                b, prov = self._orch_route(selected)
                self._orq_model, self._orq_binary, self._orq_provider = selected, b, prov
                btn.label = f"Orq: {shorten_name(selected)}"
                fields = {"binary": b, "model": selected}
                fields["provider"] = prov if b == "brain" else None
                self._persist_agent("orquestrador", **{k: v for k, v in fields.items() if v is not None})
                if b == "brain":
                    self._check_brain_key(prov)
                elif b in ("agy", "claude", "codex"):
                    self.query_one("#chat-log", RichLog).write(
                        f"\n[dim]🎼 Orquestrador via [bold]{b}[/bold] (conta logada no terminal), modo plano.[/dim]\n")

            self.push_screen(
                ModelSelectionModal("Orquestrador — modo plano (só lê, planeja e delega)", brains or self.grouped_models),
                update_orq)

        elif event.button.id == "btn-sel-op":
            def update_op(selected: str | None):
                if selected and self._set_worker_model("operador", selected):
                    self._op_model = selected
                    event.button.label = f"Op: {shorten_name(selected)}"
            self.push_screen(ModelSelectionModal("Operador — executa (precisa de ferramentas)", self._agent_models()), update_op)

        elif event.button.id == "btn-sel-rev":
            def update_rev(selected: str | None):
                if selected and self._set_worker_model("revisor", selected):
                    self._rev_model = selected
                    event.button.label = f"Rev: {shorten_name(selected)}"
            self.push_screen(ModelSelectionModal("Revisor — revisa (precisa de ferramentas)", self._agent_models()), update_rev)

        # Botões de aprovação do Operário
        elif event.button.id == "btn-approve":
            self._handle_approval("approve")

        elif event.button.id == "btn-reject":
            self._handle_approval("reject")

        elif event.button.id == "btn-review":
            self._handle_approval("review")

        elif event.button.id == "btn-exec-plan":
            self.query_one("#plan-bar").add_class("hidden")
            self._plan_pending = ""
            log = self.query_one("#chat-log", RichLog)
            log.write("\n[bold green]▶ Executando o plano...[/bold green]\n")
            self.query_one("#chat-input").disabled = True
            self._run_orchestrator("Aprovado. Execute o plano agora — emita os <DELEGATE>/<PIPELINE>.")

        elif event.button.id == "btn-adjust-plan":
            self.query_one("#plan-bar").add_class("hidden")
            self._plan_pending = ""
            self.query_one("#chat-input").focus()
            self.query_one("#chat-log", RichLog).write(
                "\n[dim]✏️ Diga o que quer mudar no plano.[/dim]\n")

    def _handle_approval(self, decision: str) -> None:
        """Processa a decisão do usuário após o Operador terminar."""
        self.query_one("#action-bar").add_class("hidden")
        self._task_pending = False
        log = self.query_one("#chat-log", RichLog)
        if decision == "approve":
            log.write("\n[bold green]✅ Aprovado.[/bold green] Cão de Guarda auditando antes do commit...\n")
            self._run_watchdog(commit_after=True)
        elif decision == "reject":
            import orquestra
            self._pipeline_rest = None  # abandona o resto do pipeline
            self._pending_attribution = None  # nada foi commitado — nada a atribuir
            self._clear_plan_panel()
            revertidos = orquestra.revert_pending(orquestra.get_project_dir())
            if revertidos:
                log.write(f"\n[bold red]❌ Rejeitado — revertido:[/bold red] {revertidos}\n")
            else:
                log.write("\n[bold red]❌ Rejeitado.[/bold red] (nada a reverter)\n")
            self._refresh_changes_panel()
            self._re_enable_input()
        else:
            self._pipeline_rest = None  # o usuário assumiu a revisão manualmente
            log.write("\n[yellow]🔍 Revisor analisando as mudanças...[/yellow]\n")
            self._stage_show("stage-changes")
            self._run_worker("revisor",
                             "Revise as mudanças não commitadas deste projeto (git diff). "
                             "Aponte bugs, riscos de segurança e problemas de arquitetura. Seja direto.",
                             suppress_report=True)

    # ── Evento: Arquivo clicado na árvore → abre no Editor (GPS) ─────────────

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Abre o arquivo clicado no Editor. Se o painel estiver oculto, abre-o."""
        self._open_file_in_editor(str(event.path))

    def _open_file_in_editor(self, filepath: str, switch_stage: bool = True) -> "TextArea | None":
        """Abre um arquivo numa aba do Editor. Se já estiver aberto, só foca a
        aba (nunca recarrega do disco — preservaria uma edição não salva).
        Retorna o TextArea da aba (novo ou existente) — útil pra quem precisa
        mexer nele na hora, sem esperar o mount assíncrono do add_pane.

        switch_stage=False (PLANO_PALCO_CHAT.md, Etapa 2 — GPS não pode
        chamar _stage_show): o GPS (thread que abre no Editor cada arquivo
        tocado pelo Operador, a cada 2s, DURANTE a tarefa) chama com
        switch_stage=False — ele só precisa que a aba exista no Editor, não
        que o palco seja puxado da Atividade pra lá a cada arquivo novo. Os
        outros chamadores (clique na árvore, <MOSTRAR>, aceitar sugestão) são
        ação explícita do usuário/Orquestrador e usam o default True."""
        abs_path = str(Path(filepath).resolve())
        tabs = self.query_one("#editor-tabs", TabbedContent)

        existing_pane_id = self._path_to_pane.get(abs_path)
        if existing_pane_id is not None:
            tabs.active = existing_pane_id
            if switch_stage:
                self._stage_show("stage-editor")
            return self.query_one(f"#code-{existing_pane_id}", TextArea)

        try:
            content = Path(abs_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self.notify(f"Arquivo binário, não é possível editar: {Path(abs_path).name}", severity="warning")
            return None
        except Exception as e:
            self.notify(f"Não foi possível abrir {Path(abs_path).name}: {e}", severity="error")
            return None

        self._tab_counter += 1
        pane_id = f"tab-{self._tab_counter}"
        filename = Path(abs_path).name
        editor = TextArea(content, language=_language_for_path(abs_path), id=f"code-{pane_id}")
        pane = TabPane(filename, editor, id=pane_id)

        tabs.add_pane(pane)
        tabs.active = pane_id

        self._open_files[pane_id] = {"path": abs_path, "original_text": content, "dirty": False}
        self._path_to_pane[abs_path] = pane_id

        if switch_stage:
            self._stage_show("stage-editor")
        return editor

    def _stage_show(self, pane_id: str) -> None:
        """Traz uma aba do palco (#stage-tabs) pra frente. Chamar só na thread
        da UI (via call_from_thread se vier de background)."""
        self.query_one("#stage-tabs", TabbedContent).active = pane_id

    def _set_activity_running(self, running: bool) -> None:
        """Marca (ou some com) o "●" no título da aba Atividade — sinal visual
        de que a equipe está trabalhando mesmo com outra aba em foco."""
        try:
            tab = self.query_one("#stage-tabs", TabbedContent).get_tab("stage-activity")
            tab.label = "⚡ Atividade ●" if running else "⚡ Atividade"
        except Exception:
            pass

    def _set_changes_badge(self, count: int) -> None:
        """Atualiza o contador no título da aba "± Mudanças"."""
        try:
            tab = self.query_one("#stage-tabs", TabbedContent).get_tab("stage-changes")
            tab.label = f"± Mudanças ({count})" if count else "± Mudanças"
        except Exception:
            pass

    def _refresh_changes_panel(self) -> None:
        """Recalcula o diff de verdade (PLANO_PALCO_CHAT.md, Etapa 3) e
        redesenha a aba "± Mudanças". Faz I/O bloqueante (subprocess/disco) —
        por isso quem chama de uma thread de fundo (GPS, fim de worker) chama
        ESTE método direto ali mesmo (já está numa thread), não via
        call_from_thread; só a pintura final dos widgets
        (_render_changes_panel) precisa de call_from_thread."""
        import orquestra
        cwd = orquestra.get_project_dir()
        stat_text, entries = orquestra.git_diff_for_display(cwd)
        if threading.current_thread() is threading.main_thread():
            self._render_changes_panel(stat_text, entries)
        else:
            self.call_from_thread(self._render_changes_panel, stat_text, entries)

    def _render_changes_panel(self, stat_text: str, entries: list) -> None:
        """Só pintura de widget — chamar na thread da UI (ou via
        call_from_thread).

        Nota: `RichLog.write()` adia a renderização até o widget ter um
        tamanho conhecido (docs do Textual) — como "± Mudanças" pode não ser
        a aba ativa no momento em que isto roda, o `#diff-log` só mostra o
        conteúdo de verdade quando o usuário abre a aba (a fila de
        `_deferred_renders` é esvaziada no primeiro resize com tamanho real).
        Não é bug: o título da aba (`_set_changes_badge`) e o `#changes-header`
        (um `Label`, sem esse adiamento) já avisam que há mudança pendente
        antes disso — confirmado com teste headless."""
        import orquestra
        header = self.query_one("#changes-header", Label)
        diff_log = self.query_one("#diff-log", RichLog)
        diff_log.clear()

        if not entries:
            if not (Path(orquestra.get_project_dir()) / ".git").is_dir():
                header.update("⚠ Esta pasta não é um repositório git — sem diff pra mostrar.")
            else:
                header.update("Nenhuma mudança pendente.")
            self._set_changes_badge(0)
            return

        header.update(f"{stat_text} · aguardando aprovação")
        self._set_changes_badge(len(entries))
        for entry in entries:
            diff_log.write(f"[bold]━━ {rich_escape(entry['path'])} "
                           f"(+{entry['added']} −{entry['removed']})[/bold]")
            for line in entry["diff"].splitlines():
                esc = rich_escape(line)
                if line.startswith("+") and not line.startswith("+++"):
                    diff_log.write(f"[green]{esc}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    diff_log.write(f"[red]{esc}[/red]")
                elif line.startswith("@@"):
                    diff_log.write(f"[cyan]{esc}[/cyan]")
                else:
                    diff_log.write(f"[dim]{esc}[/dim]")
            diff_log.write("")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Marca a aba como suja (●) quando o texto diverge do que está no disco."""
        editor_id = event.text_area.id or ""
        if not editor_id.startswith("code-"):
            return
        pane_id = editor_id.removeprefix("code-")
        info = self._open_files.get(pane_id)
        if info is None:
            return

        is_dirty = event.text_area.text != info["original_text"]
        if is_dirty == info["dirty"]:
            return  # evita reescrever o label da aba a cada tecla

        info["dirty"] = is_dirty
        tabs = self.query_one("#editor-tabs", TabbedContent)
        filename = Path(info["path"]).name
        tabs.get_tab(pane_id).label = f"● {filename}" if is_dirty else filename

    def action_save_file(self, editor: "TextArea | None" = None) -> None:
        """Ctrl+S: grava a aba ativa do Editor no disco. Aceita opcionalmente
        o TextArea já resolvido (ex: logo após _open_file_in_editor, cuja aba
        pode ainda não estar montada no DOM pro query_one achar sozinho)."""
        tabs = self.query_one("#editor-tabs", TabbedContent)
        pane_id = tabs.active
        if not pane_id:
            return
        info = self._open_files.get(pane_id)
        if info is None:
            return

        if editor is None:
            editor = self.query_one(f"#code-{pane_id}", TextArea)
        text = editor.text

        try:
            Path(info["path"]).write_text(text, encoding="utf-8")
        except PermissionError:
            self.notify(f"Sem permissão para salvar {Path(info['path']).name}", severity="error")
            return
        except OSError as e:
            self.notify(f"Erro ao salvar {Path(info['path']).name}: {e}", severity="error")
            return

        info["original_text"] = text
        info["dirty"] = False
        tabs.get_tab(pane_id).label = Path(info["path"]).name
        self.notify(f"Salvo: {Path(info['path']).name}", timeout=2)
        self._refresh_changes_panel()

        if self._learning_mode:
            import time
            now = time.time()
            if now - self._last_auto_review >= 20:
                self._last_auto_review = now
                self._run_worker("revisor", _auto_review_instruction(info["path"]), read_only=True,
                                  orchestrator_react=True)
            # Cão de Guarda: até aqui só auditava mudança do Operador antes de
            # commitar — uma edição manual nunca passava por ele. Cooldown
            # próprio (15s, igual ao _run_copilot_watchdog do REPL), desacoplado
            # do cooldown do Revisor acima — são auditorias diferentes.
            if now - self._last_editor_watchdog >= 15:
                self._last_editor_watchdog = now
                self._run_watchdog(commit_after=False, file_path=info["path"])

    def _add_suggestion_card(self, sug_id: str, sug: dict) -> None:
        """Monta um cartão de sugestão (Aceitar/Recusar) no painel do Editor."""
        panel = self.query_one("#suggestions-panel")
        filename = Path(sug["path"]).name
        card = Vertical(
            Label(f"📍 {filename}:{sug['linha_inicio']}-{sug['linha_fim']} — {sug['motivo']}",
                  classes="suggestion-label"),
            Horizontal(
                Button("✅ Aceitar", id=f"sug-accept-{sug_id}", classes="action-btn", variant="success"),
                Button("❌ Recusar", id=f"sug-reject-{sug_id}", classes="action-btn", variant="error"),
                classes="suggestion-buttons",
            ),
            id=f"sug-card-{sug_id}", classes="suggestion-card",
        )
        panel.mount(card)
        panel.remove_class("hidden")

    def _remove_suggestion_card(self, sug_id: str) -> None:
        try:
            self.query_one(f"#sug-card-{sug_id}").remove()
        except Exception:
            pass
        if not self._pending_suggestions:
            self.query_one("#suggestions-panel").add_class("hidden")

    def _accept_suggestion(self, sug_id: str) -> None:
        """Aplica a sugestão de verdade no arquivo (TextArea.replace + save),
        reaproveitando o mesmo mecanismo de save do Bloco 1."""
        sug = self._pending_suggestions.pop(sug_id, None)
        if sug is None:
            return
        self._remove_suggestion_card(sug_id)

        try:
            current_lines = Path(sug["path"]).read_text(encoding="utf-8").count("\n") + 1
        except OSError as e:
            self.notify(f"Não foi possível ler {Path(sug['path']).name}: {e}", severity="error")
            return
        if sug["linha_fim"] > current_lines:
            self.notify(
                f"Sugestão desatualizada (arquivo mudou desde a revisão) — ignorada: {Path(sug['path']).name}",
                severity="warning")
            return

        editor = self._open_file_in_editor(sug["path"])
        if editor is None:
            return
        codigo = sug["codigo"]
        if not codigo.endswith("\n"):
            codigo += "\n"
        editor.replace(codigo, (sug["linha_inicio"] - 1, 0), (sug["linha_fim"], 0))
        self.action_save_file(editor=editor)
        self.notify(f"Sugestão aplicada em {Path(sug['path']).name}", timeout=2)

    def _reject_suggestion(self, sug_id: str) -> None:
        self._pending_suggestions.pop(sug_id, None)
        self._remove_suggestion_card(sug_id)

    def _warn_pending_manual_edits(self, cwd: str, log: "RichLog") -> None:
        """Se já havia edição manual (Ctrl+S no Editor) não commitada antes de um
        worker começar, avisa no chat — evita confundir a origem de uma mudança
        no diff que o worker/Cão de Guarda vai revisar. Chamado de dentro de uma
        thread de worker; usa call_from_thread pra escrever no log com segurança."""
        import orquestra
        try:
            dirty = orquestra._git_dirty_files(cwd)
        except Exception:
            return
        if dirty:
            arquivos = ", ".join(sorted(dirty))
            self.call_from_thread(
                log.write,
                f"\n[dim]ℹ️ Havia edição manual pendente em: {arquivos} — "
                f"será incluída no diff revisado junto com o que for feito agora.[/dim]\n",
            )

    def action_close_tab(self) -> None:
        """Ctrl+W: fecha a aba ativa do Editor."""
        tabs = self.query_one("#editor-tabs", TabbedContent)
        pane_id = tabs.active
        if not pane_id:
            return
        info = self._open_files.pop(pane_id, None)
        if info is None:
            return
        self._path_to_pane.pop(info["path"], None)
        if info["dirty"]:
            self.notify(f"Fechado sem salvar: {Path(info['path']).name}", severity="warning")
        tabs.remove_pane(pane_id)

    # ── Atalhos do palco/chat-dock (PLANO_PALCO_CHAT.md, Etapa 1) ────────────

    def action_stage_tab(self, pane_id: str) -> None:
        """Ctrl+1..4: troca a aba ativa do palco direto pelo teclado."""
        self._stage_show(pane_id)

    def action_toggle_files(self) -> None:
        """Ctrl+B: mostra/oculta o painel Arquivos (mesmo efeito do botão)."""
        panel = self.query_one("#file-panel")
        panel.toggle_class("hidden")
        btn = self.query_one("#btn-files")
        if panel.has_class("hidden"):
            btn.remove_class("active")
            btn.label = "📁 Arquivos"
        else:
            btn.add_class("active")
            btn.label = "📁 Arquivos [✓]"

    def action_resize_chat(self, direction: int) -> None:
        """Ctrl+↑/Ctrl+↓: alterna o chat-dock entre compact ↔ normal ↔ tall."""
        dock = self.query_one("#chat-dock")
        current = "compact" if dock.has_class("compact") else ("tall" if dock.has_class("tall") else "normal")
        idx = self._CHAT_DOCK_SIZES.index(current)
        idx = max(0, min(len(self._CHAT_DOCK_SIZES) - 1, idx + direction))
        dock.remove_class("compact", "tall")
        new_size = self._CHAT_DOCK_SIZES[idx]
        if new_size != "normal":
            dock.add_class(new_size)

    def action_focus_chat(self) -> None:
        """Ctrl+L: foco direto no campo de texto do chat."""
        self.query_one("#chat-input").focus()

    # ── Chat: Recebe o Enter do usuário ───────────────────────────────────────

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """Intercepta o Enter no chat e dispara o Orquestrador em background."""
        prompt = event.value.strip()
        if not prompt:
            return

        self.query_one("#chat-input", ChatInput).text = ""
        log = self.query_one("#chat-log", RichLog)

        if prompt.startswith("/"):
            self._handle_slash(prompt, log)
            return

        log.write(f"\n[bold white]▶ Você:[/bold white] {prompt}\n")
        self.query_one("#chat-input").disabled = True
        self._run_orchestrator(prompt)

    def _handle_slash(self, cmd: str, log) -> None:
        """Comandos rápidos dentro da TUI (/senha, /modo, /detalhe, /design, /ajuda)."""
        import orquestra, json, os
        parts = cmd.split()
        head = parts[0].lower()

        if head.startswith("/senha") or head in ("/senhas", "/cofre", "/cofre-status"):
            if not (_VAULT and _VAULT.is_unlocked):
                log.write("\n[yellow]Cofre bloqueado. Reabra o Orquestra e digite a senha mestra.[/yellow]\n")
                return
            resp = _VAULT.handle_command(cmd)
            # /senha NOME valor → injeta já nesta sessão (senão só valeria ao reabrir)
            sp = cmd.split(None, 2)
            if head == "/senha" and len(sp) == 3:
                os.environ[sp[1].upper().strip()] = sp[2].strip()
            log.write(f"\n[green]🔐 {resp or 'ok'}[/green]\n")
            return
        if head in ("/modo", "/mode") and len(parts) > 1:
            mode = "tecnico" if parts[1].lower().startswith(("tec", "téc", "dev")) else "chat"
            config = orquestra.load_config()
            config.setdefault("ui", {})["mode"] = mode
            with open(orquestra.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            log.write(f"\n[green]Modo: {mode}[/green]\n")
        elif head in ("/detalhe", "/detail") and len(parts) > 1 and parts[1] in ("minimal", "normal", "full"):
            config = orquestra.load_config()
            config.setdefault("ui", {})["detail"] = parts[1]
            with open(orquestra.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            log.write(f"\n[green]Detalhe: {parts[1]}[/green]\n")
        elif head in ("/risco", "/risk"):
            config = orquestra.load_config()
            gates = config.setdefault("risk_gates", dict(orquestra.DEFAULT_CONFIG["risk_gates"]))
            if len(parts) > 1 and parts[1].lower() in ("on", "ligado", "ativar", "true"):
                gates["enabled"] = True
            elif len(parts) > 1 and parts[1].lower() in ("off", "desligado", "desativar", "false"):
                gates["enabled"] = False
            elif len(parts) > 1:
                log.write("\n[dim]Uso: /risco on | off (sem argumento mostra o status atual)[/dim]\n")
                return
            if len(parts) > 1:
                with open(orquestra.CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            estado = "[green]ligado[/green]" if gates.get("enabled") else "[dim]desligado[/dim]"
            padroes = ", ".join(gates.get("always_confirm_patterns", []))
            log.write(
                f"\n🛡️ Gate por risco: {estado}\n"
                f"[dim]Quando ligado, pula a pausa de Aprovar se nenhum arquivo tocado bater nestes "
                f"padrões (e o Cão de Guarda não achar nada): {padroes}[/dim]\n"
            )
        elif head in ("/relato", "/report"):
            config = orquestra.load_config()
            ui_cfg = config.setdefault("ui", dict(orquestra.DEFAULT_CONFIG["ui"]))
            if len(parts) > 1 and parts[1].lower() in ("on", "ligado", "ativar", "true"):
                ui_cfg["orchestrator_reports"] = True
            elif len(parts) > 1 and parts[1].lower() in ("off", "desligado", "desativar", "false"):
                ui_cfg["orchestrator_reports"] = False
            elif len(parts) > 1:
                log.write("\n[dim]Uso: /relato on | off (sem argumento mostra o status atual)[/dim]\n")
                return
            if len(parts) > 1:
                with open(orquestra.CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            estado = "[green]ligado[/green]" if ui_cfg.get("orchestrator_reports", True) else "[dim]desligado[/dim]"
            log.write(
                f"\n🎼 Orquestrador presta contas: {estado}\n"
                "[dim]Quando ligado, depois que o Operador (ou uma etapa de <PIPELINE>) termina, "
                "o Orquestrador comenta o resultado em vez do chat só mostrar a resposta crua "
                "do worker (custa 1 chamada extra por tarefa).[/dim]\n"
            )
        elif head in ("/quem", "/autor"):
            if len(parts) < 2:
                log.write("\n[dim]Uso: /quem <caminho/do/arquivo>[/dim]\n")
                return
            fp = " ".join(parts[1:]).strip()
            rel = fp
            try:
                rel = str(Path(fp).resolve().relative_to(Path(orquestra.get_project_dir()).resolve()))
            except (ValueError, OSError):
                pass
            info = orquestra.get_file_author(orquestra.get_project_id(), rel) or orquestra.get_file_author(orquestra.get_project_id(), fp)
            if not info:
                log.write(f"\n[dim]Sem registro de autoria pra `{rel}` (nunca foi tocado por um agente do Orquestra, ou a mudança nunca foi aprovada).[/dim]\n")
            else:
                log.write(
                    f"\n[bold]✏️ {rel}[/bold] — última edição: [bold]{info['agent_label']}[/bold] "
                    f"em {info['updated_at'][:16].replace('T', ' ')}\n"
                    f"[dim]{info['last_instruction'] or '(sem descrição)'}[/dim]\n"
                )
        elif head in ("/uso", "/gasto", "/saldo"):
            u = self._session_usage
            log.write(
                f"\n💰 [bold]Uso estimado (Claude Code):[/bold] ${u['cost_usd']:.4f} · "
                f"{u['tokens_in']} tokens in / {u['tokens_out']} tokens out · {u['calls']} chamada(s)\n"
                "[dim]Codex/Agy (assinatura) contam nas chamadas, mas não têm preço por token exposto pela CLI — só Claude e DeepSeek têm custo real.[/dim]\n"
            )
            if head == "/saldo":
                deepseek_env = orquestra.load_env_file(str(orquestra.DEEPSEEK_ENV))
                deepseek_key = deepseek_env.get("ANTHROPIC_API_KEY", "")
                balance = orquestra._deepseek_balance(deepseek_key) if deepseek_key else None
                if balance is not None:
                    log.write(f"[green]DeepSeek — saldo real: ${balance:.2f}[/green]\n")
                else:
                    log.write("[dim]DeepSeek: sem key configurada ou saldo indisponível agora.[/dim]\n")
        elif head in ("/design", "/open-design"):
            rest = cmd.split(maxsplit=1)
            if len(rest) > 1:
                log.write(f"\n[bold white]▶ Você:[/bold white] /design {rest[1]}\n")
                self.query_one("#chat-input").disabled = True
                self._run_open_design(rest[1].strip())
            else:
                self._open_design_project(log)
        elif head in ("/ajuda", "/help"):
            log.write("\n[dim]/senha NOME valor · /senhas · /design <o que prever> · "
                      "/modo chat|tecnico · /detalhe minimal|normal|full · "
                      "/risco on|off · /relato on|off · /quem <arquivo> · /uso · /saldo · "
                      "botões do topo trocam os modelos[/dim]\n")
        else:
            log.write(f"\n[dim]Comando não reconhecido: {cmd}[/dim]\n")

    @work(thread=True)
    def _open_design_project(self, log) -> None:
        """/design sem argumento: sobe o Open Design e abre o projeto no navegador."""
        import subprocess as _sp, shutil as _sh
        import orquestra
        self.call_from_thread(log.write, "\n[dim]🎨 Verificando o Open Design...[/dim]\n")
        resolved = orquestra.open_design_resolve_project(orquestra.load_config())
        if not resolved or not resolved.get("web_url"):
            self.call_from_thread(log.write, "\n[yellow]⚠ Não consegui subir o Open Design.[/yellow]\n")
            return
        url = f"{resolved['web_url']}/projects/{resolved['project_id']}"
        self.call_from_thread(log.write, f"\n[magenta]🎨 Open Design:[/magenta] {url}\n")
        if _sh.which("xdg-open"):
            _sp.Popen(["xdg-open", url], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)

    # ── Worker: Orquestrador ──────────────────────────────────────────────────

    @work(thread=True)
    def _run_orchestrator(self, prompt: str) -> None:
        """Roda o Orquestrador (estrategista, zero ferramentas) e trata a resposta:
        conversa pura, <PLANO> (com gate de aprovação) ou <DELEGATE>/<PIPELINE>."""
        import orquestra

        log = self.query_one("#chat-log", RichLog)
        self._last_user_prompt = prompt
        self.call_from_thread(self._start_task)
        try:
            self._run_orchestrator_body(prompt, log)
        finally:
            self.call_from_thread(self._end_task)

    def _run_orchestrator_body(self, prompt: str, log: "RichLog") -> None:
        import orquestra

        def on_status(text: str):
            self.call_from_thread(self._set_status, text)

        def on_tool(text: str):
            self._activity("🎼", "Orquestrador", text)

        config = orquestra.load_config()
        orq_role = dict(config.get("roles", {}).get("orquestrador", {}))
        orq_role["model"] = self._orq_model
        orq_role["binary"] = self._orq_binary
        orq_role["provider"] = self._orq_provider

        orquestra.save_chat_message("orquestrador", "user", prompt)
        history = orquestra.load_chat_history("orquestrador")
        memory = orquestra._load_memory_context(orquestra.get_project_id())
        full_prompt = orquestra.build_orchestrator_prompt(config, history, memory)

        cmd, binary, ofile = orquestra._build_orchestrator_cmd(orq_role, full_prompt)
        env = os.environ.copy()
        ef = orq_role.get("env_file")
        if ef:
            env.update(orquestra.load_env_file(ef))

        self.call_from_thread(log.write, f"\n[dim]🎼 Orquestrador ({shorten_name(self._orq_model)}) pensando...[/dim]\n")
        self._activity("🎼", "Orquestrador", "começou a pensar...")

        # on_tool (Etapa 0e/2): tool call do Orquestrador (lendo/rodando comando
        # em modo plano) também narra na Atividade, além de ir pro status.
        section = _TUILiveSection("Orquestrador", lambda t: None, lambda *a: None, on_status, on_tool)
        section.open()
        # Etapa 0d: Codex lendo um repo em modo read-only pode legitimamente
        # passar dos 150s que bastavam pro brain/DeepSeek (chat direto, sem
        # ferramenta nenhuma) — timeout curto pra ele virava "sem resposta"
        # sem causa clara.
        timeout_s = 300 if binary == "codex" else 150
        final_text, ok = orquestra._run_orchestrator_stream(
            cmd, env, binary, section, timeout_s=timeout_s, output_file=ofile,
            detail=orquestra._detail_level(config),
        )
        on_status("")

        if not ok or not final_text:
            self.call_from_thread(log.write, "[bold red]⚠ Sem resposta do Orquestrador. Tente de novo.[/bold red]\n")
            self.call_from_thread(self._re_enable_input)
            return

        orquestra.save_chat_message("orquestrador", "assistant", final_text)

        # <MOSTRAR arquivo="..."> — extrai e remove ANTES do parsing de
        # PLANO/DELEGATE/PIPELINE, senão o corte de "texto visível" (abaixo)
        # jogaria fora qualquer coisa escrita depois da tag.
        MOSTRAR_MAX = 3
        mostrar_hits = list(orquestra.MOSTRAR_RE.finditer(final_text))[:MOSTRAR_MAX]
        final_text = orquestra.MOSTRAR_RE.sub("", final_text).replace("</MOSTRAR>", "")
        for hit in mostrar_hits:
            rel_path = hit.group(1).strip()
            abs_path = orquestra._mostrar_resolve_path(rel_path)
            if abs_path:
                self.call_from_thread(self._open_file_in_editor, abs_path)
            else:
                self.call_from_thread(
                    log.write,
                    f"\n[dim]⚠ Orquestrador citou um arquivo que não existe no projeto ({rel_path}) — ignorado.[/dim]\n",
                )

        plano = orquestra.PLANO_RE.search(final_text)
        pipelines = [
            [(m.group(1).lower(), m.group(2).strip())
             for m in orquestra.DELEGATE_RE.finditer(pm.group(1))]
            for pm in orquestra.PIPELINE_RE.finditer(final_text)
        ]
        no_pipeline = orquestra.PIPELINE_RE.sub("", final_text)
        delegates = list(orquestra.DELEGATE_RE.finditer(no_pipeline))

        # Texto que o usuário vê = só o que vem ANTES da 1ª tag. Tudo depois de um
        # <DELEGATE>/<PIPELINE>/<PLANO> é descartado (o modelo às vezes "alucina"
        # um resultado depois da tag — quem produz o resultado é o operário).
        tag = re.search(r'<(?:PLANO|PIPELINE|DELEGATE)\b', final_text, re.IGNORECASE)
        visible = (final_text[:tag.start()] if tag else final_text).strip()
        if visible:
            self.call_from_thread(log.write, f"\n[bold cyan]🎼 Orquestrador[/bold cyan] {visible}\n")
            self._activity("🎼", "Orquestrador", "→ respondeu")

        if plano and not delegates and not pipelines:
            # Gate: mostra o plano e espera o usuário mandar executar.
            self._plan_pending = plano.group(1).strip()
            self.call_from_thread(log.write,
                f"\n[bold]📋 Plano:[/bold]\n{self._plan_pending}\n")
            self.call_from_thread(self._show_plan_bar)
            return

        if delegates or pipelines:
            _OD = ("open-design", "opendesign", "open_design")
            for m in delegates:
                bt = m.group(1).lower()
                if bt in _OD:
                    self._run_open_design(m.group(2).strip())
                else:
                    self._run_worker(bt, m.group(2).strip())
            for steps in pipelines:
                steps = [(t, i) for t, i in steps if t.lower() not in _OD]
                if steps:
                    self._run_pipeline_tui(steps)
            return

        self.call_from_thread(self._re_enable_input)

    # ── Worker: Operário (Implementador) ─────────────────────────────────────

    @work(thread=True)
    def _run_worker(self, binary_tag: str, instruction: str, read_only: bool = False,
                     orchestrator_react: bool = False, suppress_report: bool = False) -> None:
        """Roda um operário (Operador ou Revisor) em thread separada, com GPS e
        barra de aprovação quando ele mexe em arquivos.

        read_only=True (Modo Aprendizado): roda em modo plano/read-only de
        verdade (mesmas flags do Orquestrador) — o agente lê e comenta, nunca
        escreve. Sem GPS (nada deveria mudar no disco).

        orchestrator_react=True: depois de uma resposta bem-sucedida, o
        Orquestrador comenta o resultado (mesmo padrão do modo chat do REPL,
        _orchestrator_react) em vez de deixar só a bolha crua do worker — usado
        pela revisão automática do Modo Aprendizado, pra o chat não parecer
        mudo entre uma revisão e outra.

        suppress_report=True (PLANO_PALCO_CHAT.md, Etapa 4): desliga o "o
        Orquestrador presta contas" desta chamada específica mesmo com
        read_only=False e ui.orchestrator_reports ligado — usado só pelo botão
        "🔍 Revisar" (_handle_approval), porque ali o Revisor JÁ É o retorno
        por natureza (pedido explícito do usuário, não um <DELEGATE> do
        Orquestrador). Sem afetar o Operador nem etapas de <PIPELINE>."""
        self.call_from_thread(self._start_task)
        try:
            self._run_worker_body(binary_tag, instruction, read_only=read_only,
                                   orchestrator_react=orchestrator_react,
                                   suppress_report=suppress_report)
        finally:
            self.call_from_thread(self._end_task)

    def _run_worker_body(self, binary_tag: str, instruction: str, read_only: bool = False,
                          orchestrator_react: bool = False, suppress_report: bool = False) -> None:
        import orquestra

        log = self.query_one("#chat-log", RichLog)
        cwd = orquestra.get_project_dir()
        self._warn_pending_manual_edits(cwd, log)

        config = orquestra.load_config()
        worker_cfg, actual_binary, label, icon, color = orquestra._resolve_worker(config, binary_tag)
        worker_cfg = dict(worker_cfg)

        # Modelo escolhido no header: Rev: para o revisor, Op: para o resto.
        canon = orquestra._WORKER_ALIASES.get(binary_tag.lower().strip(), binary_tag.lower().strip())
        chosen = self._rev_model if canon == "revisor" else self._op_model
        if chosen:
            worker_cfg["model"] = chosen

        cmd, worker_env, output_file = orquestra._build_worker_cmd(worker_cfg, actual_binary, instruction, read_only=read_only)

        # Transparência: a instrução completa vai pra Atividade (histórico
        # de verdade, sem limite de tamanho útil pra debugar depois); o chat
        # só recebe um aviso curto apontando pra lá — PLANO_PALCO_CHAT.md,
        # Etapa 2 (antes, o prompt inteiro competia com a conversa no mesmo
        # #chat-log).
        self._activity(icon, label, f"📤 prompt:\n{instruction}")
        self.call_from_thread(
            log.write,
            f"\n[dim]{icon} {label} começou · acompanhe em ⚡ Atividade (Ctrl+1)[/dim]\n",
        )

        # GPS: monitora quais arquivos o agente está editando em tempo real
        # (pulado em read_only — nada deveria mudar no disco pra monitorar)
        gps_stop = threading.Event()
        files_seen: set[str] = set()

        def _gps_watch():
            """Thread que detecta arquivos modificados e os abre no editor —
            e também mantém a aba "± Mudanças" atualizada ao vivo (Etapa 3),
            já que roda o mesmo `git diff --name-only` a cada 2s de qualquer
            forma."""
            while not gps_stop.wait(2):
                try:
                    result = subprocess.run(
                        ["git", "diff", "--name-only"],
                        cwd=cwd, capture_output=True, text=True, timeout=5
                    )
                    novos = False
                    for filepath in result.stdout.strip().splitlines():
                        if filepath and filepath not in files_seen:
                            files_seen.add(filepath)
                            novos = True
                            full_path = str(Path(cwd) / filepath)
                            self.call_from_thread(self._open_file_in_editor, full_path, switch_stage=False)
                    if novos:
                        self._refresh_changes_panel()
                except Exception:
                    pass

        if not read_only:
            gps_thread = threading.Thread(target=_gps_watch, daemon=True)
            gps_thread.start()

        def on_line(text: str):
            self._activity(icon, label, text)

        def on_close(ok: bool, error_detail: str, changes_summary: str):
            pass

        def on_status(text: str):
            self.call_from_thread(self._set_status, text)

        def on_tool(text: str):
            self._activity(icon, label, text)

        section = _TUILiveSection(label, on_line, on_close, on_status, on_tool)
        section.open()
        result = orquestra._run_worker_headless(
            cmd, worker_env, cwd, section,
            is_agy=(actual_binary == "agy"),
            is_codex=(actual_binary == "codex"),
            is_copilot=(actual_binary == "copilot"),
            is_opencode=(actual_binary == "opencode"),
            output_file=output_file,
            detail=orquestra._detail_level(config),
        )
        final_text, is_error, error_detail, changes_summary = (
            result.final_text, result.is_error, result.error_detail, result.changes_summary,
        )
        if not read_only:
            orquestra._record_agent_performance(
                orquestra.get_project_id(), label, orquestra._infer_task_kind(instruction),
                "error" if is_error else "success",
                result.cost_usd, result.tokens_in, result.tokens_out, result.duration_ms,
            )
            self.call_from_thread(self._add_usage, result.cost_usd, result.tokens_in, result.tokens_out)

        on_status("")

        # Para o GPS
        gps_stop.set()

        if output_file:
            try:
                os.unlink(output_file)
            except OSError:
                pass

        if is_error:
            orquestra.save_chat_message("orquestrador", "assistant",
                                        f"[Erro do {label}]: {(error_detail or '')[:1500]}")
            self._activity(icon, label, f"❌ erro: {error_detail}")
            self.call_from_thread(
                log.write,
                f"\n[bold red]⚠ Erro no {label}:[/bold red] {error_detail[:500]}\n"
            )
            self.call_from_thread(self._re_enable_input)
            return

        if final_text:
            orquestra.save_chat_message("orquestrador", "assistant",
                                        f"[Resposta do {label}]:\n{final_text[:4000]}")

            display_text = final_text
            if read_only:
                sugestoes = list(orquestra.SUGESTAO_RE.finditer(final_text))
                if sugestoes:
                    display_text = orquestra.SUGESTAO_RE.sub("", final_text).strip()
                    for m in sugestoes:
                        self._suggestion_counter += 1
                        sug_id = f"sug-{self._suggestion_counter}"
                        sug = {
                            "path": str((Path(cwd) / m.group(1)).resolve()),
                            "linha_inicio": int(m.group(2)),
                            "linha_fim": int(m.group(3)),
                            "motivo": m.group(4).strip(),
                            "codigo": m.group(5).strip("\n"),
                        }
                        self._pending_suggestions[sug_id] = sug
                        self.call_from_thread(self._add_suggestion_card, sug_id, sug)

            self._activity(icon, label, f"✅ terminou:\n{display_text}")

            # Etapa 4 (resolve A5): o Orquestrador presta contas em vez do
            # chat só mostrar a bolha crua do worker — independente do
            # parâmetro orchestrator_react abaixo (esse é só do Modo
            # Aprendizado, sempre read_only=True; os dois nunca disparam na
            # mesma chamada). suppress_report desliga isso só pro botão
            # "🔍 Revisar" (o Revisor ali já É o retorno pedido pelo usuário).
            reported = False
            if not read_only and not suppress_report and config.get("ui", {}).get("orchestrator_reports", True):
                self.call_from_thread(self._set_status, f"⠹ 🎼 Orquestrador resumindo o que {label} fez...")
                orq_cfg = config.setdefault("roles", {}).setdefault("orquestrador", {})
                orq_cfg["model"] = self._orq_model
                orq_cfg["binary"] = self._orq_binary
                orq_cfg["provider"] = self._orq_provider
                react_section = _TUILiveSection("Orquestra", lambda t: None, lambda *a: None, None)
                react_section.open()
                report_input = f"{display_text}\n\n{changes_summary}".strip() if changes_summary else display_text
                comment = orquestra._orchestrator_react(
                    config, label, report_input, section=react_section,
                    has_pending_changes=bool(changes_summary),
                )
                self.call_from_thread(self._set_status, "")
                if comment:
                    orquestra.save_chat_message("orquestrador", "assistant", comment)
                    self.call_from_thread(
                        log.write, f"\n[bold cyan]🎼 Orquestrador[/bold cyan] {comment[:2000]}\n"
                    )
                    reported = True

            if not reported:
                self.call_from_thread(
                    log.write,
                    f"\n[bold green]{icon} {label}[/bold green] {display_text[:2000]}\n"
                )

            if orchestrator_react:
                orq_cfg = config.setdefault("roles", {}).setdefault("orquestrador", {})
                orq_cfg["model"] = self._orq_model
                orq_cfg["binary"] = self._orq_binary
                orq_cfg["provider"] = self._orq_provider
                react_section = _TUILiveSection("Orquestra", lambda t: None, lambda *a: None, None)
                react_section.open()
                comment = orquestra._orchestrator_react(config, label, display_text, section=react_section)
                if comment:
                    orquestra.save_chat_message("orquestrador", "assistant", comment)
                    self.call_from_thread(
                        log.write, f"\n[bold cyan]🎼 Orquestrador[/bold cyan] {comment[:2000]}\n"
                    )

        if changes_summary:
            self._activity(icon, label, changes_summary)
            self._refresh_changes_panel()
            self.call_from_thread(
                log.write,
                f"\n[dim]{changes_summary} · ver ± Mudanças (Ctrl+2)[/dim]\n",
            )
            self._pending_changes = changes_summary
            attribution = None
            if not read_only and result.changed_files:
                attribution = {
                    "agent_tag": actual_binary, "agent_label": label,
                    "instruction": instruction, "files": result.changed_files,
                }

            auto_ok = (not read_only) and self._should_auto_approve(config, result.changed_files)
            has_bug = False
            if auto_ok:
                footer = self.query_one("#watchdog-footer")
                self.call_from_thread(footer.update, "🐕 Cão de Guarda: analisando o diff...")
                wtext, has_bug = orquestra.watchdog_verdict(config)
                if wtext and has_bug:
                    self.call_from_thread(footer.update, f"🐕 Cão de Guarda ⚠ {wtext[:220]}")
                    self.call_from_thread(footer.add_class, "alert")
                    self.call_from_thread(log.write, f"\n[bold red]🐕 Cão de Guarda[/bold red]\n{wtext[:1500]}\n")
                    self._activity("🐕", "Cão de Guarda", f"⚠ {wtext}")
                else:
                    self.call_from_thread(footer.update, f"🐕 Cão de Guarda: {wtext[:220] if wtext else 'nada a apontar. ✅'}")
                    self.call_from_thread(footer.remove_class, "alert")
                    self._activity("🐕", "Cão de Guarda", wtext or "nada a apontar. ✅")

            if auto_ok and not has_bug:
                self.call_from_thread(log.write, "\n[dim](gate por risco: seguindo sem pausar)[/dim]\n")
                commited = orquestra.commit_pending(cwd, f"Orquestra: {label} (gate por risco)")
                if commited:
                    self.call_from_thread(log.write, f"\n[dim]📦 Commit criado: {commited}[/dim]\n")
                    self._activity("📦", "git", f"commit criado: {commited}")
                    if attribution:
                        for fp in attribution["files"]:
                            orquestra.set_file_author(orquestra.get_project_id(), fp, attribution["agent_tag"],
                                                       attribution["agent_label"], attribution["instruction"])
                self.call_from_thread(self._re_enable_input)
            else:
                if auto_ok and has_bug:
                    self.call_from_thread(log.write,
                        "\n[dim](gate por risco: caminho parecia seguro, mas o Cão de Guarda achou algo — pausando mesmo assim)[/dim]\n")
                self._pending_attribution = attribution
                self.call_from_thread(self._show_action_bar)
        else:
            self.call_from_thread(self._re_enable_input)

    # ── Worker: Watchdog ──────────────────────────────────────────────────────

    @work(thread=True)
    def _run_watchdog(self, commit_after: bool = False, file_path: str | None = None) -> None:
        """Cão de Guarda: usa orquestra.watchdog_verdict (mesma lógica do REPL).
        Verde por padrão no rodapé; vermelho só se achar bug. Se commit_after,
        commita o diff DEPOIS da auditoria (o guarda audita as mudanças ainda
        não commitadas).

        file_path: repassado a watchdog_verdict como fallback — se a pasta não
        for git (ou não houver diff detectável), audita esse arquivo inteiro em
        vez de desistir em silêncio. gh copilot/claude/agy/codex não precisam
        de git pra rodar; só o cálculo do diff precisava."""
        import orquestra
        log = self.query_one("#chat-log", RichLog)
        footer = self.query_one("#watchdog-footer")
        self.call_from_thread(footer.update, "🐕 Cão de Guarda: analisando o diff...")

        config = orquestra.load_config()
        text, has_bug = orquestra.watchdog_verdict(config, file_path=file_path)

        if not text:
            self.call_from_thread(footer.update, "🐕 Cão de Guarda: nada a apontar. ✅")
            self.call_from_thread(footer.remove_class, "alert")
        elif has_bug:
            self.call_from_thread(footer.update, f"🐕 Cão de Guarda ⚠ {text[:220]}")
            self.call_from_thread(footer.add_class, "alert")
            self.call_from_thread(log.write, f"\n[bold red]🐕 Cão de Guarda[/bold red]\n{text[:1500]}\n")
        else:
            self.call_from_thread(footer.update, f"🐕 Cão de Guarda: {text[:220]}")
            self.call_from_thread(footer.remove_class, "alert")

        if commit_after:
            commited = orquestra.commit_pending(orquestra.get_project_dir(), "Orquestra: mudança aprovada")
            if commited:
                self.call_from_thread(log.write, f"\n[dim]📦 Commit criado: {commited}[/dim]\n")
                self._refresh_changes_panel()
                # Autoria por IA — índice paralelo (file_authorship): só grava
                # depois que o commit de verdade aconteceu; se fosse Rejeitar,
                # _handle_approval descarta o buffer sem chamar isto.
                attr = self._pending_attribution
                if attr:
                    for fp in attr["files"]:
                        orquestra.set_file_author(
                            orquestra.get_project_id(), fp, attr["agent_tag"], attr["agent_label"],
                            attr["instruction"],
                        )
            self._pending_attribution = None

        if commit_after:
            # Se um <PIPELINE> ficou pausado nesta aprovação, retoma as etapas
            # restantes. Só faz sentido quando esta chamada É a aprovação de
            # verdade — uma auditoria de edição manual (commit_after=False,
            # ver action_save_file) não deve mexer num pipeline pausado que
            # não tem nada a ver com ela.
            rest = self._pipeline_rest
            self._pipeline_rest = None
            if rest and rest["steps"]:
                self.call_from_thread(log.write, "\n[dim]🔗 Retomando o pipeline...[/dim]\n")
                self.call_from_thread(lambda: self._run_pipeline_tui(rest["steps"], rest["previous"], is_resume=True))
            else:
                self.call_from_thread(self._clear_plan_panel)
                self.call_from_thread(self._re_enable_input)

    # ── Open Design (prévia visual dentro do Orquestra) ─────────────────────

    _CHROME = None

    def _chrome_bin(self):
        import shutil as _sh
        if self._CHROME is None:
            self._CHROME = (_sh.which("chromium") or _sh.which("google-chrome")
                            or _sh.which("google-chrome-stable") or _sh.which("chromium-browser") or "")
        return self._CHROME

    def _shot(self, target: str, width: int = 1400, height: int = 1000):
        """Renderiza uma URL ou um arquivo HTML local para PNG com o Chrome headless.
        Retorna o caminho do PNG ou None. Roda numa thread (bloqueante)."""
        import subprocess as _sp, tempfile
        chrome = self._chrome_bin()
        if not chrome:
            return None
        fd, png = tempfile.mkstemp(prefix="orq_preview_", suffix=".png")
        os.close(fd)
        try:
            _sp.run([chrome, "--headless", "--no-sandbox", "--disable-gpu",
                     "--hide-scrollbars", "--force-color-profile=srgb",
                     "--virtual-time-budget=5000",
                     f"--window-size={width},{height}", f"--screenshot={png}", target],
                    capture_output=True, timeout=50)
            if os.path.getsize(png) > 800:
                return png
        except Exception:
            pass
        try:
            os.unlink(png)
        except OSError:
            pass
        return None

    @work(thread=True)
    def _run_open_design(self, instruction: str) -> None:
        """Gera uma prévia visual real via Open Design, RENDERIZA dentro da aba
        Idea Studio (Chrome headless → imagem no terminal) e não abre navegador."""
        import re as _re
        import orquestra

        cwd = orquestra.get_project_dir()
        preview_file = Path(cwd) / ".orquestra_preview.html"
        try:
            preview_file.unlink()
        except OSError:
            pass

        log = self.query_one("#chat-log", RichLog)
        self._warn_pending_manual_edits(cwd, log)
        self.call_from_thread(log.write, "\n[bold magenta]🎨 Open Design[/bold magenta] gerando a prévia (1-3 min)...\n")

        def on_status(text):
            def _u():
                lbl = self.query_one("#status-indicator", Label)
                if text:
                    lbl.update(f"[magenta]🎨 {text}[/magenta]"); lbl.remove_class("hidden")
                else:
                    lbl.update(""); lbl.add_class("hidden")
            self.call_from_thread(_u)

        # Pede ao Open Design pra também salvar o HTML final autossuficiente aqui,
        # pra eu renderizar a prévia LIMPA (sem a interface do Open Design em volta).
        instruction_plus = (
            f"{instruction}\n\nIMPORTANTE: além da prévia normal, salve uma cópia "
            f"COMPLETA e AUTOSSUFICIENTE do HTML final da prévia (todo o CSS embutido "
            f"em <style> ou inline, sem depender de arquivos externos) em: {preview_file}"
        )

        config = orquestra.load_config()
        section = _TUILiveSection("Open Design", lambda t: None, lambda *a: None, on_status)
        section.open()
        ok, text = orquestra.open_design_run_task(config, instruction_plus, section=section, timeout_s=360)
        on_status("")

        orquestra.save_chat_message("orquestrador", "assistant",
                                    f"[{'Prévia do Open Design' if ok else 'Erro do Open Design'}]:\n{text[:4000]}")
        if not ok:
            self.call_from_thread(log.write, f"\n[bold red]⚠ Open Design falhou:[/bold red] {text[:600]}\n")
            self.call_from_thread(self._re_enable_input)
            return

        urls = _re.findall(r'https?://\S+', text)
        preview_url = urls[-1].rstrip('.,;)') if urls else ""
        # tira a linha do link do texto mostrado (a prévia aparece renderizada, não como link)
        clean = _re.sub(r'\n*🔗 Prévia ao vivo:.*', '', text).strip()
        self.call_from_thread(log.write, f"\n[bold magenta]🎨 Open Design[/bold magenta] {clean[:1200]}\n")

        # 1ª escolha: o HTML autossuficiente local (prévia limpa). Fallback: a URL ao vivo.
        self.call_from_thread(log.write, "\n[dim]🖼  Renderizando a prévia dentro do Orquestra...[/dim]\n")
        png = None
        if preview_file.is_file():
            png = self._shot(preview_file.as_uri())
        if not png and preview_url:
            png = self._shot(preview_url, height=1600)

        def _show_idea(png_path):
            self.query_one("#idea-md", Markdown).update(clean or "# Prévia gerada")
            box = self.query_one("#idea-image", Container)
            for w in list(box.children):
                w.remove()
            if png_path:
                try:
                    from textual_image.widget import Image as TImage
                    box.mount(TImage(png_path))
                except Exception as e:
                    box.mount(Label(f"(não consegui renderizar a imagem: {e})"))
            else:
                box.mount(Label("(Chrome headless indisponível — sem prévia renderizada)"))
            self._stage_show("stage-idea")  # a prévia toma a tela inteira do palco
        # guarda uma cópia PNG na pasta do projeto — se o terminal renderizar mal
        # (qterminal etc.), dá pra abrir num visualizador de imagem (não é navegador).
        if png:
            try:
                import shutil as _sh2
                dest = Path(cwd) / ".orquestra_preview.png"
                _sh2.copyfile(png, dest)
                self.call_from_thread(log.write,
                    f"\n[dim]✅ Prévia renderizada na aba 🎨 Idea Studio  ·  cópia em {dest.name}[/dim]\n")
            except OSError:
                self.call_from_thread(log.write, "\n[dim]✅ Prévia renderizada na aba 🎨 Idea Studio.[/dim]\n")
        self.call_from_thread(_show_idea, png)

        # .orquestra_preview.html é artefato (gitignored, _is_noise_path) — não conta
        # como alteração. Se o Open Design mexeu em OUTRO arquivo real, cai no gate.
        dirty = orquestra._git_dirty_files(cwd)
        if dirty:
            self.call_from_thread(log.write,
                f"\n[yellow]⚠ Open Design alterou arquivos reais:[/yellow] {', '.join(sorted(dirty))}\n")
            self._pending_changes = ", ".join(sorted(dirty))
            self.call_from_thread(self._show_action_bar)
        else:
            self.call_from_thread(self._re_enable_input)

    # ── Pipeline (operador → revisor → ...) ──────────────────────────────────

    @work(thread=True)
    def _run_pipeline_tui(self, steps: list, previous: str = "", is_resume: bool = False) -> None:
        """Roda uma cadeia <PIPELINE> em sequência, passando o resultado de cada
        etapa como contexto para a próxima. Se uma etapa mexe em arquivos, pausa
        na aprovação e guarda as etapas restantes em self._pipeline_rest —
        _run_watchdog retoma daqui depois do 'Aprovar'."""
        self.call_from_thread(self._start_task)
        try:
            self._run_pipeline_tui_body(steps, previous=previous, is_resume=is_resume)
        finally:
            self.call_from_thread(self._end_task)

    def _run_pipeline_tui_body(self, steps: list, previous: str = "", is_resume: bool = False) -> None:
        import orquestra
        log = self.query_one("#chat-log", RichLog)
        cwd = orquestra.get_project_dir()
        self._warn_pending_manual_edits(cwd, log)
        config = orquestra.load_config()

        # Painel "📋 Plano": na primeira chamada (não-retomada) monta a lista
        # cheia de passos; numa retomada pós-aprovação, self._plan_steps já
        # existe (com as etapas já feitas marcadas "done") — só o offset muda.
        if not is_resume or not self._plan_steps:
            self._plan_steps = [{"text": instr, "status": "pending"} for _, instr in steps]
            offset = 0
        else:
            offset = max(0, len(self._plan_steps) - len(steps))
        self.call_from_thread(self._render_plan_panel)

        for idx, (tag, instruction) in enumerate(steps, 1):
            worker_cfg, actual_binary, label, icon, color = orquestra._resolve_worker(config, tag)
            worker_cfg = dict(worker_cfg)
            canon = orquestra._WORKER_ALIASES.get(tag.lower().strip(), tag.lower().strip())
            chosen = self._rev_model if canon == "revisor" else self._op_model
            if chosen:
                worker_cfg["model"] = chosen

            full = instruction
            if previous:
                full = f"Contexto — resultado da etapa anterior:\n\n{previous[:4000]}\n\nSua tarefa:\n{instruction}"

            step_no = offset + idx
            if offset + idx - 1 < len(self._plan_steps):
                self._plan_steps[offset + idx - 1]["status"] = "running"
                self.call_from_thread(self._render_plan_panel)

            self.call_from_thread(log.write, f"\n[dim]🔗 Etapa {label} ({idx} de {len(steps)} restante(s))[/dim]\n")
            self._activity(icon, label, f"📤 prompt (etapa {step_no}):\n{instruction}")
            cmd, env, out_file = orquestra._build_worker_cmd(worker_cfg, actual_binary, full)

            # Atividade ao vivo: antes, o pipeline passava callbacks no-op e
            # nada aparecia entre uma etapa e a próxima — agora religa no mesmo
            # #status-indicator que o <DELEGATE> solto já usa, e também escreve
            # cada linha/tool-call na aba ⚡ Atividade (Etapa 2).
            def on_status(text: str, _label=label, _step=step_no, _total=len(steps) + offset):
                def _update():
                    status_lbl = self.query_one("#status-indicator", Label)
                    if text:
                        status_lbl.update(f"🔗 Etapa {_step}/{_total} — {_label}: {text}")
                        status_lbl.remove_class("hidden")
                    else:
                        status_lbl.update("")
                        status_lbl.add_class("hidden")
                self.call_from_thread(_update)

            def on_line(text: str, _icon=icon, _label=label):
                self._activity(_icon, _label, text)

            section = _TUILiveSection(label, on_line, lambda *a: None, on_status, on_line)
            section.open()
            result = orquestra._run_worker_headless(
                cmd, env, cwd, section,
                is_agy=(actual_binary == "agy"), is_codex=(actual_binary == "codex"),
                is_copilot=(actual_binary == "copilot"), is_opencode=(actual_binary == "opencode"),
                output_file=out_file, detail=orquestra._detail_level(config),
            )
            final_text, is_error, err, changes = (
                result.final_text, result.is_error, result.error_detail, result.changes_summary,
            )
            on_status("")
            if out_file:
                try:
                    os.unlink(out_file)
                except OSError:
                    pass
            orquestra._record_agent_performance(
                orquestra.get_project_id(), label, orquestra._infer_task_kind(instruction),
                "error" if is_error else "success",
                result.cost_usd, result.tokens_in, result.tokens_out, result.duration_ms,
            )
            self.call_from_thread(self._add_usage, result.cost_usd, result.tokens_in, result.tokens_out)
            if is_error:
                if offset + idx - 1 < len(self._plan_steps):
                    self._plan_steps[offset + idx - 1]["status"] = "error"
                    self.call_from_thread(self._render_plan_panel)
                self._activity(icon, label, f"❌ erro: {err}")
                self.call_from_thread(log.write, f"\n[bold red]⚠ Pipeline parou em {label}: {err[:400]}[/bold red]\n")
                orquestra.save_chat_message("orquestrador", "assistant", f"[Pipeline parou em {label}]: {err[:1500]}")
                self.call_from_thread(self._re_enable_input)
                return
            if offset + idx - 1 < len(self._plan_steps):
                self._plan_steps[offset + idx - 1]["status"] = "done"
                self.call_from_thread(self._render_plan_panel)
            previous = (final_text or "").strip()
            orquestra.save_chat_message("orquestrador", "assistant", f"[Pipeline — {label}]:\n{previous[:4000]}")
            self._activity(icon, label, f"✅ terminou (etapa {step_no}):\n{previous}")

            # Etapa 4 (resolve A5): mesmo relato do <DELEGATE> solto, ao fim
            # de CADA etapa do pipeline — "só em Operador e etapas de
            # pipeline" (não se aplica ao botão "🔍 Revisar", que não passa
            # por aqui).
            reported = False
            if config.get("ui", {}).get("orchestrator_reports", True):
                self.call_from_thread(self._set_status, f"⠹ 🎼 Orquestrador resumindo o que {label} fez...")
                orq_cfg = config.setdefault("roles", {}).setdefault("orquestrador", {})
                orq_cfg["model"] = self._orq_model
                orq_cfg["binary"] = self._orq_binary
                orq_cfg["provider"] = self._orq_provider
                react_section = _TUILiveSection("Orquestra", lambda t: None, lambda *a: None, None)
                react_section.open()
                report_input = f"{previous}\n\n{changes}".strip() if changes else previous
                comment = orquestra._orchestrator_react(
                    config, label, report_input, section=react_section,
                    has_pending_changes=bool(changes),
                )
                self.call_from_thread(self._set_status, "")
                if comment:
                    orquestra.save_chat_message("orquestrador", "assistant", comment)
                    self.call_from_thread(
                        log.write, f"\n[bold cyan]🎼 Orquestrador[/bold cyan] {comment[:2000]}\n"
                    )
                    reported = True

            if not reported:
                self.call_from_thread(log.write, f"\n[bold green]{icon} {label}[/bold green] {previous[:1500]}\n")

            if changes:
                self._activity(icon, label, changes)
                self._refresh_changes_panel()
                self.call_from_thread(log.write, f"\n[dim]{changes} · ver ± Mudanças (Ctrl+2)[/dim]\n")
                self._pending_changes = changes
                attribution = None
                if result.changed_files:
                    attribution = {
                        "agent_tag": actual_binary, "agent_label": label,
                        "instruction": instruction, "files": result.changed_files,
                    }

                auto_ok = self._should_auto_approve(config, result.changed_files)
                has_bug = False
                if auto_ok:
                    footer = self.query_one("#watchdog-footer")
                    self.call_from_thread(footer.update, "🐕 Cão de Guarda: analisando o diff...")
                    wtext, has_bug = orquestra.watchdog_verdict(config)
                    if wtext and has_bug:
                        self.call_from_thread(footer.update, f"🐕 Cão de Guarda ⚠ {wtext[:220]}")
                        self.call_from_thread(footer.add_class, "alert")
                        self.call_from_thread(log.write, f"\n[bold red]🐕 Cão de Guarda[/bold red]\n{wtext[:1500]}\n")
                        self._activity("🐕", "Cão de Guarda", f"⚠ {wtext}")
                    else:
                        self.call_from_thread(footer.update, f"🐕 Cão de Guarda: {wtext[:220] if wtext else 'nada a apontar. ✅'}")
                        self.call_from_thread(footer.remove_class, "alert")
                        self._activity("🐕", "Cão de Guarda", wtext or "nada a apontar. ✅")

                if auto_ok and not has_bug:
                    # Gate por risco: nenhum caminho sensível e o Cão de Guarda não
                    # achou nada — commita e segue pro próximo passo sem pausar.
                    self.call_from_thread(log.write, "\n[dim](gate por risco: seguindo sem pausar)[/dim]\n")
                    commited = orquestra.commit_pending(cwd, f"Orquestra: {label} (gate por risco)")
                    if commited:
                        self.call_from_thread(log.write, f"\n[dim]📦 Commit criado: {commited}[/dim]\n")
                        self._activity("📦", "git", f"commit criado: {commited}")
                        if attribution:
                            for fp in attribution["files"]:
                                orquestra.set_file_author(orquestra.get_project_id(), fp, attribution["agent_tag"],
                                                           attribution["agent_label"], attribution["instruction"])
                    # sem return — o loop segue pro idx seguinte
                else:
                    if auto_ok and has_bug:
                        self.call_from_thread(log.write,
                            "\n[dim](gate por risco: caminho parecia seguro, mas o Cão de Guarda achou algo — pausando mesmo assim)[/dim]\n")
                    self._pending_attribution = attribution
                    remaining = steps[idx:]  # etapas ainda não rodadas
                    self._pipeline_rest = {"steps": remaining, "previous": previous} if remaining else None
                    if remaining:
                        self.call_from_thread(log.write,
                            f"\n[dim](o pipeline continua depois do Aprovar: {len(remaining)} etapa(s))[/dim]\n")
                    self.call_from_thread(self._show_action_bar)
                    return

        self.call_from_thread(self._clear_plan_panel)

        self.call_from_thread(self._re_enable_input)

    # ── Evento: usuário troca de aba do palco na mão ──────────────────────────

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Se o usuário troca de aba do palco NA MÃO enquanto uma tarefa está
        rodando, isso "prende" o palco naquela aba — _start_task não vai mais
        forçar de volta pra Atividade até a tarefa atual terminar (senão o
        usuário lendo o Editor levaria um "puxão" de volta a cada chamada)."""
        if event.tabbed_content.id == "stage-tabs" and self._active_workers > 0:
            self._stage_pinned = True

    # ── Helpers de UI (devem ser chamados na thread da UI) ────────────────────

    def _show_plan_bar(self) -> None:
        self.query_one("#plan-bar").remove_class("hidden")

    def _show_action_bar(self) -> None:
        self.query_one("#action-bar").remove_class("hidden")
        self._task_pending = True

    def _re_enable_input(self) -> None:
        self.query_one("#chat-input").disabled = False
        self.query_one("#chat-input").focus()

    def _activity(self, who_icon: str, who: str, text: str) -> None:
        """Escreve uma linha na aba "⚡ Atividade" do palco — PLANO_PALCO_CHAT.md,
        Etapa 2. Thread-safe: chama de qualquer thread, se encarrega do
        call_from_thread sozinho (quem chama não precisa lembrar disso)."""
        if not text or not text.strip():
            return
        ts = time.strftime("%H:%M:%S")
        line = f"[dim]{ts}[/dim] {who_icon} [bold]{who}[/bold]  {rich_escape(text)}"

        def _write():
            try:
                self.query_one("#activity-log", RichLog).write(line)
            except Exception:
                pass
        if threading.current_thread() is threading.main_thread():
            _write()
        else:
            self.call_from_thread(_write)

    def _set_status(self, text: str) -> None:
        """Atualiza a linha de status única do #chat-header. Chamar só na
        thread da UI (quem chama de uma thread de fundo usa call_from_thread)."""
        self._status_text = text
        lbl = self.query_one("#status-indicator", Label)
        if text:
            lbl.update(text)
            lbl.remove_class("hidden")
        else:
            lbl.update("")
            lbl.add_class("hidden")

    def _tick_spinner(self) -> None:
        """set_interval enquanto houver worker ativo — redesenha o status com
        um frame de spinner + segundos decorridos, pra nunca parecer travado."""
        if not self._status_text:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(self._SPINNER_FRAMES)
        elapsed = int(time.monotonic() - self._spinner_started_at)
        frame = self._SPINNER_FRAMES[self._spinner_idx]
        try:
            self.query_one("#status-indicator", Label).update(f"{frame} {self._status_text} · {elapsed}s")
        except Exception:
            pass

    def _start_task(self) -> None:
        """Chamar (via call_from_thread) quando um worker/orquestrador/pipeline
        começa a rodar. Contador, não bool — <DELEGATE> soltos rodam em
        paralelo, um bool zeraria cedo demais com o primeiro que terminasse."""
        self._active_workers += 1
        if self._active_workers == 1:
            # Início de um novo lote de trabalho (não de um <DELEGATE> paralelo
            # adicional, que cai no ramo abaixo) — sempre reseta o pino e vai
            # pra Atividade. Durante o lote, se o usuário trocar de aba na mão,
            # on_tabbed_content_tab_activated marca _stage_pinned=True e mais
            # nenhum worker deste MESMO lote força a volta (só chamamos
            # _stage_show aqui, uma vez por lote, nunca de novo até zerar).
            self._stage_pinned = False
            self._spinner_started_at = time.monotonic()
            if self._spinner_timer is None:
                self._spinner_timer = self.set_interval(0.12, self._tick_spinner)
            self._stage_show("stage-activity")
            self._set_activity_running(True)

    def _end_task(self) -> None:
        """Chamar (via call_from_thread) quando um worker/orquestrador/pipeline
        termina — só desliga o spinner e despina o palco quando NENHUM outro
        worker paralelo ainda está rodando."""
        self._active_workers = max(0, self._active_workers - 1)
        if self._active_workers == 0:
            self._set_activity_running(False)
            if self._spinner_timer is not None:
                self._spinner_timer.stop()
                self._spinner_timer = None
            # O usuário trocou de aba na mão (stage_pinned) e ficou olhando
            # outra coisa (não a Atividade) enquanto isso terminava — um toast
            # avisa sem forçar a troca de volta.
            try:
                stage = self.query_one("#stage-tabs", TabbedContent)
                if self._stage_pinned and stage.active != "stage-activity":
                    self.notify("A equipe terminou — veja em ⚡ Atividade (Ctrl+1).", timeout=5)
            except Exception:
                pass
            self._stage_pinned = False
            self._set_status("")

    def _should_auto_approve(self, config: dict, changed_files: list[str]) -> bool:
        """Gate por risco (nasce desligado): True só quando risk_gates.enabled
        é True e nenhum arquivo tocado bate nos padrões sensíveis — aí a
        tarefa segue sem pausar em vez de exigir Aprovar."""
        import orquestra
        if not config.get("risk_gates", {}).get("enabled") or not changed_files:
            return False
        return not orquestra._classify_risk(changed_files, config)

    def _update_usage_footer(self) -> None:
        u = self._session_usage
        text = f"💰 Uso: ${u['cost_usd']:.2f} estimado · {u['calls']} chamada(s)"
        try:
            self.query_one("#usage-footer", Label).update(text)
        except Exception:
            pass

    def _add_usage(self, cost_usd: float, tokens_in: int, tokens_out: int) -> None:
        """Acumula custo/tokens no total do projeto e atualiza o rodapé. Chamar
        via call_from_thread — quem soma de verdade é o SQLite (agent_performance);
        isto só mantém o número na tela em sincronia sem reconsultar o banco."""
        self._session_usage["cost_usd"] += cost_usd or 0.0
        self._session_usage["tokens_in"] += tokens_in or 0
        self._session_usage["tokens_out"] += tokens_out or 0
        self._session_usage["calls"] += 1
        self._update_usage_footer()

    def _render_plan_panel(self) -> None:
        """Reescreve o painel '📋 Plano' a partir de self._plan_steps — chamado
        toda vez que um passo muda de status. Vazio = painel escondido."""
        panel = self.query_one("#plan-panel", Vertical)
        panel.remove_children()
        if not self._plan_steps:
            panel.add_class("hidden")
            return
        panel.remove_class("hidden")
        icons = {"pending": "⬜", "running": "🔄", "done": "✅", "error": "❌"}
        for step in self._plan_steps:
            icon = icons.get(step["status"], "⬜")
            panel.mount(Label(f"{icon} {step['text'][:100]}", classes="plan-step"))

    def _clear_plan_panel(self) -> None:
        self._plan_steps = []
        self._render_plan_panel()


_VAULT = None  # cofre desbloqueado, pra o /senha funcionar dentro da TUI


def _startup():
    """Roda ANTES do Textual assumir o terminal: memória + cofre.
    A TUI é autossuficiente — NÃO abre editor externo, NÃO sobe o bridge do
    Cursor (isso era do REPL antigo). Tudo acontece dentro do Orquestra."""
    global _VAULT
    import orquestra
    orquestra._init_memory_db()
    try:
        vault = orquestra.VaultManager(orquestra.get_project_dir())
        if vault.needs_setup():
            vault.setup_wizard()
        else:
            vault.unlock_interactive()
        vault.inject_env()
        if vault.is_unlocked:
            _VAULT = vault
    except RuntimeError as e:
        print(f"⚠ Cofre indisponível: {e}\n  Continuando sem o cofre nesta sessão.\n")


if __name__ == "__main__":
    _startup()
    OrquestraTUI().run()
