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
import os
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import Label, Button, Input, RichLog, TextArea, Markdown, OptionList, DirectoryTree
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from textual import work


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
    sem alterar nada naquele arquivo."""
    def __init__(self, label: str, on_line, on_close, on_status=None):
        self.label = label
        self._on_line = on_line
        self._on_close = on_close
        self._on_status = on_status

    def open(self): pass
    def end_line(self): pass
    
    def write(self, text: str):
        if text.strip():
            self._on_line(text)

    def tool_call(self, text: str):
        if self._on_status:
            self._on_status(f"[dim]⏳ {text}[/dim]")
        else:
            self._on_line(f"[dim]⏳ {text}[/dim]")

    def close(self, ok: bool = True, error_detail: str = "", changes_summary: str = ""):
        self._on_close(ok, error_detail, changes_summary)


# ─── Aplicação Principal ──────────────────────────────────────────────────────

class OrquestraTUI(App):
    """Interface Orquestra Terminal IDE v3.0"""

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

    #editor-panel {
        width: 1fr;
    }

    #idea-panel {
        width: 1fr;
    }

    #status-indicator {
        margin: 1 0;
        text-align: left;
    }

    #chat-input {
        width: 1fr;
        min-width: 40;
        border-right: none;
    }

    #chat-panel {
        width: 1fr;
        min-width: 40;
        border-right: none;
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

    RichLog {
        height: 1fr;
        padding: 1;
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

    .action-btn {
        margin: 0 1;
        min-width: 18;
    }

    Input {
        dock: bottom;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        # ── Header ───────────────────────────────────────────────────────────
        with Container(id="top-bar"):
            with Container(id="controls"):
                yield Label("🎼 ORQUESTRA", id="app-title")
                yield Button("📁 Arquivos", id="btn-files", classes="toggle-btn")
                yield Button("📝 Editor", id="btn-editor", classes="toggle-btn")
                yield Button("🎨 Idea Studio", id="btn-idea", classes="toggle-btn")
            with Container(id="models"):
                yield Button("Carregando...", id="btn-sel-orq", classes="model-btn")
                yield Button("Carregando...", id="btn-sel-op", classes="model-btn")
                yield Button("Carregando...", id="btn-sel-rev", classes="model-btn")

        # ── Painéis ───────────────────────────────────────────────────────────
        with Horizontal(id="main-content"):
            with Vertical(id="file-panel", classes="panel hidden"):
                yield Label("📁 ARQUIVOS", classes="panel-title")
                yield DirectoryTree("./")

            with Vertical(id="editor-panel", classes="panel hidden"):
                yield Label("📝 EDITOR", id="editor-title", classes="panel-title")
                yield TextArea("", language="python", id="code-editor")

            with Vertical(id="idea-panel", classes="panel hidden"):
                yield Label("🎨 IDEA STUDIO", classes="panel-title")
                yield Markdown(
                    "# Idea Studio\n\n"
                    "Aqui aparecerão seus **mapas mentais**, diagramas de fluxo "
                    "e prévias de telas geradas pelo Open Design."
                )

            with Vertical(id="chat-panel", classes="panel"):
                yield Label("💬 CHAT CENTRAL", classes="panel-title")
                yield RichLog(id="chat-log", markup=True, wrap=True)
                yield Label("", id="status-indicator", classes="hidden")
                # Barra do PLANO — aparece quando o Orquestrador propõe um <PLANO>
                with Container(id="plan-bar", classes="hidden"):
                    yield Button("▶ Executar plano", id="btn-exec-plan", classes="action-btn", variant="success")
                    yield Button("✏️ Ajustar", id="btn-adjust-plan", classes="action-btn")
                # Barra de aprovação — aparece só quando o Operador termina
                with Container(id="action-bar", classes="hidden"):
                    yield Button("✅ Aprovar", id="btn-approve", classes="action-btn", variant="success")
                    yield Button("❌ Rejeitar", id="btn-reject", classes="action-btn", variant="error")
                    yield Button("🔍 Revisar", id="btn-review", classes="action-btn")
                yield Input(placeholder="Digite aqui... [Enter para enviar]", id="chat-input")

        # ── Rodapé Watchdog ───────────────────────────────────────────────────
        yield Label("🐕 Watchdog: Aguardando alterações para revisão...", id="watchdog-footer")

    # ── Inicialização ─────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        """Carrega modelos e configuração ao iniciar."""
        import orquestra
        self.grouped_models: dict = {}
        cfg = orquestra.load_config()
        orq = cfg.get("roles", {}).get("orquestrador", {})
        ops = cfg.get("workers", {}).get("operador", {})
        rev = cfg.get("workers", {}).get("revisor", {})

        self._orq_model: str = orq.get("model", "deepseek-v4-flash")
        self._orq_binary: str = orq.get("binary", "brain")
        self._orq_provider: str = orq.get("provider", "deepseek")
        self._op_model: str = ops.get("model", "claude-sonnet-5")
        self._rev_model: str = rev.get("model", "claude-opus-5")

        self._current_file: str | None = None   # GPS: arquivo aberto no editor
        self._task_pending: bool = False        # Há tarefa aguardando aprovação?
        self._pending_changes: str = ""         # Resumo das mudanças do Operador
        self._plan_pending: str = ""            # <PLANO> aguardando "Executar"
        self._last_user_prompt: str = ""        # último pedido do usuário (p/ re-enviar no "Executar")
        self._pipeline_rest = None              # etapas restantes de um <PIPELINE> pausado na aprovação

        log = self.query_one("#chat-log", RichLog)
        log.write("[bold cyan]🎼 Orquestrador[/bold cyan] Bem-vindo ao Orquestra. Converse comigo — eu planejo e a equipe executa.\n")

        self.query_one("#btn-sel-orq").label = f"Orq: {shorten_name(self._orq_model)}"
        self.query_one("#btn-sel-op").label  = f"Op: {shorten_name(self._op_model)}"
        self.query_one("#btn-sel-rev").label = f"Rev: {shorten_name(self._rev_model)}"

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
        (orq_raw, claude_raw, copilot_raw, deepseek_raw, codex_raw, ollama_raw) = await asyncio.gather(
            loop.run_in_executor(None, orquestra._fetch_agy_models),
            loop.run_in_executor(None, orquestra._fetch_claude_models),
            loop.run_in_executor(None, orquestra._fetch_copilot_models),
            loop.run_in_executor(None, orquestra._fetch_deepseek_models),
            loop.run_in_executor(None, orquestra._fetch_codex_models),
            loop.run_in_executor(None, orquestra._fetch_ollama_models),
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
        }

        # Os defaults (orq/op/rev) já vieram do ~/.orquestra.json em on_mount.
        # Aqui só populamos as listas dos modais de troca de modelo. As labels
        # dos botões já foram setadas em on_mount.

    def _binary_from_model(self, model_id: str) -> str:
        """Infere qual binary usar a partir do ID do modelo."""
        m = model_id.lower()
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
        if "deepseek-v4" in str(fields.get("model", "")) and fields.get("binary") == "claude":
            target["env_file"] = str(orquestra.DEEPSEEK_ENV)
        elif slot != "orquestrador" and fields.get("binary") != "claude":
            target.pop("env_file", None)
        with open(orquestra.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    # ── Eventos de UI: Toggles e Modelos ──────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        panel_map = {
            "btn-files":  ("#file-panel",   "📁 Arquivos"),
            "btn-editor": ("#editor-panel", "📝 Editor"),
            "btn-idea":   ("#idea-panel",   "🎨 Idea Studio"),
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

        # Seleção de Modelos
        if event.button.id == "btn-sel-orq":
            # O Orquestrador só aceita "cérebros" de chat puro (DeepSeek/Ollama).
            brains = {k: v for k, v in self.grouped_models.items()
                      if "DeepSeek" in k or "Ollama" in k}
            def update_orq(selected: str | None):
                if selected:
                    self._orq_model = selected
                    self._orq_binary = "brain"
                    self._orq_provider = "deepseek" if "deepseek" in selected.lower() else "ollama-cloud"
                    event.button.label = f"Orq: {shorten_name(selected)}"
                    self._persist_agent("orquestrador", binary="brain",
                                        provider=self._orq_provider, model=selected)
            self.push_screen(ModelSelectionModal("Orquestrador", brains or self.grouped_models), update_orq)

        elif event.button.id == "btn-sel-op":
            def update_op(selected: str | None):
                if selected:
                    self._op_model = selected
                    event.button.label = f"Op: {shorten_name(selected)}"
                    self._persist_agent("operador", binary=self._binary_from_model(selected), model=selected)
            self.push_screen(ModelSelectionModal("Operador", self.grouped_models), update_op)

        elif event.button.id == "btn-sel-rev":
            def update_rev(selected: str | None):
                if selected:
                    self._rev_model = selected
                    event.button.label = f"Rev: {shorten_name(selected)}"
                    self._persist_agent("revisor", binary=self._binary_from_model(selected), model=selected)
            self.push_screen(ModelSelectionModal("Revisor", self.grouped_models), update_rev)

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
            revertidos = orquestra.revert_pending(orquestra.get_project_dir())
            if revertidos:
                log.write(f"\n[bold red]❌ Rejeitado — revertido:[/bold red] {revertidos}\n")
            else:
                log.write("\n[bold red]❌ Rejeitado.[/bold red] (nada a reverter)\n")
            self._re_enable_input()
        else:
            self._pipeline_rest = None  # o usuário assumiu a revisão manualmente
            log.write("\n[yellow]🔍 Revisor analisando as mudanças...[/yellow]\n")
            editor_panel = self.query_one("#editor-panel")
            if editor_panel.has_class("hidden"):
                editor_panel.remove_class("hidden")
                self.query_one("#btn-editor").add_class("active")
                self.query_one("#btn-editor").label = "📝 Editor [✓]"
            self._run_worker("revisor",
                             "Revise as mudanças não commitadas deste projeto (git diff). "
                             "Aponte bugs, riscos de segurança e problemas de arquitetura. Seja direto.")

    # ── Evento: Arquivo clicado na árvore → abre no Editor (GPS) ─────────────

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Abre o arquivo clicado no Editor. Se o painel estiver oculto, abre-o."""
        self._open_file_in_editor(str(event.path))

    def _open_file_in_editor(self, filepath: str) -> None:
        """Carrega um arquivo no TextArea do Editor e atualiza o título do painel."""
        try:
            content = Path(filepath).read_text(encoding="utf-8")
        except Exception:
            return  # Ignora arquivos binários ou sem permissão

        editor = self.query_one("#code-editor", TextArea)
        editor.text = content
        self._current_file = filepath

        # Atualiza o título do painel com o nome do arquivo
        filename = Path(filepath).name
        self.query_one("#editor-title").update(f"📝 EDITOR — {filename}")

        # Garante que o painel do editor está visível
        editor_panel = self.query_one("#editor-panel")
        if editor_panel.has_class("hidden"):
            editor_panel.remove_class("hidden")
            btn = self.query_one("#btn-editor")
            btn.add_class("active")
            btn.label = "📝 Editor [✓]"

    # ── Chat: Recebe o Enter do usuário ───────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Intercepta o Enter no chat e dispara o Orquestrador em background."""
        prompt = event.value.strip()
        if not prompt:
            return

        self.query_one("#chat-input", Input).value = ""
        log = self.query_one("#chat-log", RichLog)

        if prompt.startswith("/"):
            self._handle_slash(prompt, log)
            return

        log.write(f"\n[bold white]▶ Você:[/bold white] {prompt}\n")
        self.query_one("#chat-input").disabled = True
        self._run_orchestrator(prompt)

    def _handle_slash(self, cmd: str, log) -> None:
        """Comandos rápidos dentro da TUI (/modo, /detalhe, /ajuda)."""
        import orquestra, json
        parts = cmd.split()
        head = parts[0].lower()
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
        elif head in ("/ajuda", "/help"):
            log.write("\n[dim]/modo chat|tecnico · /detalhe minimal|normal|full · "
                      "botões do topo trocam os modelos[/dim]\n")
        else:
            log.write(f"\n[dim]Comando não reconhecido: {cmd}[/dim]\n")

    # ── Worker: Orquestrador ──────────────────────────────────────────────────

    @work(thread=True)
    def _run_orchestrator(self, prompt: str) -> None:
        """Roda o Orquestrador (estrategista, zero ferramentas) e trata a resposta:
        conversa pura, <PLANO> (com gate de aprovação) ou <DELEGATE>/<PIPELINE>."""
        import orquestra

        log = self.query_one("#chat-log", RichLog)
        self._last_user_prompt = prompt

        def on_status(text: str):
            def _update():
                status_lbl = self.query_one("#status-indicator", Label)
                if text:
                    status_lbl.update(text)
                    status_lbl.remove_class("hidden")
                else:
                    status_lbl.update("")
                    status_lbl.add_class("hidden")
            self.call_from_thread(_update)

        config = orquestra.load_config()
        orq_role = dict(config.get("roles", {}).get("orquestrador", {}))
        orq_role["model"] = self._orq_model
        orq_role["binary"] = self._orq_binary
        orq_role["provider"] = self._orq_provider

        orquestra.save_chat_message("orquestrador", "user", prompt)
        history = orquestra.load_chat_history("orquestrador")
        memory = orquestra._load_memory_context(orquestra.get_project_id())
        full_prompt = orquestra.build_orchestrator_prompt(config, history, memory)

        cmd, binary = orquestra._build_orchestrator_cmd(orq_role, full_prompt)
        env = os.environ.copy()

        self.call_from_thread(log.write, f"\n[dim]🎼 Orquestrador ({shorten_name(self._orq_model)}) pensando...[/dim]\n")

        section = _TUILiveSection("Orquestrador", lambda t: None, lambda *a: None, on_status)
        section.open()
        final_text, ok = orquestra._run_orchestrator_stream(cmd, env, binary, section, timeout_s=150)
        on_status("")

        if not ok or not final_text:
            self.call_from_thread(log.write, "[bold red]⚠ Sem resposta do Orquestrador. Tente de novo.[/bold red]\n")
            self.call_from_thread(self._re_enable_input)
            return

        orquestra.save_chat_message("orquestrador", "assistant", final_text)

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

        if plano and not delegates and not pipelines:
            # Gate: mostra o plano e espera o usuário mandar executar.
            self._plan_pending = plano.group(1).strip()
            self.call_from_thread(log.write,
                f"\n[bold]📋 Plano:[/bold]\n{self._plan_pending}\n")
            self.call_from_thread(self._show_plan_bar)
            return

        if delegates or pipelines:
            for m in delegates:
                self._run_worker(m.group(1).lower(), m.group(2).strip())
            for steps in pipelines:
                if steps:
                    self._run_pipeline_tui(steps)
            return

        self.call_from_thread(self._re_enable_input)

    # ── Worker: Operário (Implementador) ─────────────────────────────────────

    @work(thread=True)
    def _run_worker(self, binary_tag: str, instruction: str) -> None:
        """Roda um operário (Operador ou Revisor) em thread separada, com GPS e
        barra de aprovação quando ele mexe em arquivos."""
        import orquestra

        log = self.query_one("#chat-log", RichLog)
        cwd = orquestra.get_project_dir()

        config = orquestra.load_config()
        worker_cfg, actual_binary, label, icon, color = orquestra._resolve_worker(config, binary_tag)
        worker_cfg = dict(worker_cfg)

        # Modelo escolhido no header: Rev: para o revisor, Op: para o resto.
        canon = orquestra._WORKER_ALIASES.get(binary_tag.lower().strip(), binary_tag.lower().strip())
        chosen = self._rev_model if canon == "revisor" else self._op_model
        if chosen:
            worker_cfg["model"] = chosen

        cmd, worker_env, output_file = orquestra._build_worker_cmd(worker_cfg, actual_binary, instruction)

        # GPS: monitora quais arquivos o agente está editando em tempo real
        gps_stop = threading.Event()
        files_seen: set[str] = set()

        def _gps_watch():
            """Thread que detecta arquivos modificados e os abre no editor."""
            while not gps_stop.wait(2):
                try:
                    result = subprocess.run(
                        ["git", "diff", "--name-only"],
                        cwd=cwd, capture_output=True, text=True, timeout=5
                    )
                    for filepath in result.stdout.strip().splitlines():
                        if filepath and filepath not in files_seen:
                            files_seen.add(filepath)
                            full_path = str(Path(cwd) / filepath)
                            self.call_from_thread(self._open_file_in_editor, full_path)
                except Exception:
                    pass

        gps_thread = threading.Thread(target=_gps_watch, daemon=True)
        gps_thread.start()

        def on_line(text: str):
            self.call_from_thread(log.write, text + "\n")

        def on_close(ok: bool, error_detail: str, changes_summary: str):
            pass

        def on_status(text: str):
            def _update():
                status_lbl = self.query_one("#status-indicator", Label)
                if text:
                    status_lbl.update(text)
                    status_lbl.remove_class("hidden")
                else:
                    status_lbl.update("")
                    status_lbl.add_class("hidden")
            self.call_from_thread(_update)

        section = _TUILiveSection(label, on_line, on_close, on_status)
        section.open()
        final_text, is_error, error_detail, changes_summary = orquestra._run_worker_headless(
            cmd, worker_env, cwd, section,
            is_agy=(actual_binary == "agy"),
            is_codex=(actual_binary == "codex"),
            is_copilot=(actual_binary == "copilot"),
            output_file=output_file,
        )
        
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
            self.call_from_thread(
                log.write,
                f"\n[bold red]⚠ Erro no {label}:[/bold red] {error_detail[:500]}\n"
            )
            self.call_from_thread(self._re_enable_input)
            return

        if final_text:
            orquestra.save_chat_message("orquestrador", "assistant",
                                        f"[Resposta do {label}]:\n{final_text[:4000]}")
            self.call_from_thread(
                log.write,
                f"\n[bold green]{icon} {label}[/bold green] {final_text[:2000]}\n"
            )

        if changes_summary:
            self.call_from_thread(log.write, f"\n[dim]{changes_summary}[/dim]\n")
            self._pending_changes = changes_summary
            self.call_from_thread(self._show_action_bar)
        else:
            self.call_from_thread(self._re_enable_input)

    # ── Worker: Watchdog ──────────────────────────────────────────────────────

    @work(thread=True)
    def _run_watchdog(self, commit_after: bool = False) -> None:
        """Cão de Guarda: usa orquestra.watchdog_verdict (mesma lógica do REPL).
        Verde por padrão no rodapé; vermelho só se achar bug. Se commit_after,
        commita o diff DEPOIS da auditoria (o guarda audita as mudanças ainda
        não commitadas)."""
        import orquestra
        log = self.query_one("#chat-log", RichLog)
        footer = self.query_one("#watchdog-footer")
        self.call_from_thread(footer.update, "🐕 Cão de Guarda: analisando o diff...")

        config = orquestra.load_config()
        text, has_bug = orquestra.watchdog_verdict(config)

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

        # Se um <PIPELINE> ficou pausado nesta aprovação, retoma as etapas restantes.
        rest = self._pipeline_rest
        self._pipeline_rest = None
        if rest and rest["steps"]:
            self.call_from_thread(log.write, "\n[dim]🔗 Retomando o pipeline...[/dim]\n")
            self.call_from_thread(lambda: self._run_pipeline_tui(rest["steps"], rest["previous"]))
        else:
            self.call_from_thread(self._re_enable_input)

    # ── Pipeline (operador → revisor → ...) ──────────────────────────────────

    @work(thread=True)
    def _run_pipeline_tui(self, steps: list, previous: str = "") -> None:
        """Roda uma cadeia <PIPELINE> em sequência, passando o resultado de cada
        etapa como contexto para a próxima. Se uma etapa mexe em arquivos, pausa
        na aprovação e guarda as etapas restantes em self._pipeline_rest —
        _run_watchdog retoma daqui depois do 'Aprovar'."""
        import orquestra
        log = self.query_one("#chat-log", RichLog)
        cwd = orquestra.get_project_dir()
        config = orquestra.load_config()

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

            self.call_from_thread(log.write, f"\n[dim]🔗 Etapa {label} ({idx} de {len(steps)} restante(s))[/dim]\n")
            cmd, env, out_file = orquestra._build_worker_cmd(worker_cfg, actual_binary, full)
            section = _TUILiveSection(label, lambda t: None, lambda *a: None, None)
            section.open()
            final_text, is_error, err, changes = orquestra._run_worker_headless(
                cmd, env, cwd, section,
                is_agy=(actual_binary == "agy"), is_codex=(actual_binary == "codex"),
                is_copilot=(actual_binary == "copilot"), output_file=out_file,
            )
            if out_file:
                try:
                    os.unlink(out_file)
                except OSError:
                    pass
            if is_error:
                self.call_from_thread(log.write, f"\n[bold red]⚠ Pipeline parou em {label}: {err[:400]}[/bold red]\n")
                orquestra.save_chat_message("orquestrador", "assistant", f"[Pipeline parou em {label}]: {err[:1500]}")
                self.call_from_thread(self._re_enable_input)
                return
            previous = (final_text or "").strip()
            orquestra.save_chat_message("orquestrador", "assistant", f"[Pipeline — {label}]:\n{previous[:4000]}")
            self.call_from_thread(log.write, f"\n[bold green]{icon} {label}[/bold green] {previous[:1500]}\n")
            if changes:
                self._pending_changes = changes
                remaining = steps[idx:]  # etapas ainda não rodadas
                self._pipeline_rest = {"steps": remaining, "previous": previous} if remaining else None
                if remaining:
                    self.call_from_thread(log.write,
                        f"\n[dim](o pipeline continua depois do Aprovar: {len(remaining)} etapa(s))[/dim]\n")
                self.call_from_thread(self._show_action_bar)
                return

        self.call_from_thread(self._re_enable_input)

    # ── Helpers de UI (devem ser chamados na thread da UI) ────────────────────

    def _show_plan_bar(self) -> None:
        self.query_one("#plan-bar").remove_class("hidden")

    def _show_action_bar(self) -> None:
        self.query_one("#action-bar").remove_class("hidden")
        self._task_pending = True

    def _re_enable_input(self) -> None:
        self.query_one("#chat-input").disabled = False
        self.query_one("#chat-input").focus()


def _startup():
    """Roda ANTES do Textual assumir o terminal: memória, bridge, log, cofre."""
    import orquestra
    from pathlib import Path as _P
    orquestra._init_memory_db()
    orquestra._start_bridge_server()
    try:
        log_path = _P(orquestra.get_project_dir()) / orquestra.LOG_FILE_NAME
        log_path.write_text("# Orquestra — Logs de Execução ao Vivo\n\n", encoding="utf-8")
        orquestra._auto_open_in_cursor([str(log_path)])
    except OSError:
        pass
    try:
        vault = orquestra.VaultManager(orquestra.get_project_dir())
        if vault.needs_setup():
            vault.setup_wizard()
        else:
            vault.unlock_interactive()
        vault.inject_env()
    except RuntimeError as e:
        print(f"⚠ Cofre indisponível: {e}\n  Continuando sem o cofre nesta sessão.\n")


if __name__ == "__main__":
    _startup()
    OrquestraTUI().run()
