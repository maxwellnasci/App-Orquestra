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

    #idea-panel.featured {
        width: 3fr;
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
                yield Container(id="idea-image")
                yield Markdown(
                    "As prévias visuais do **Open Design** são renderizadas aqui, "
                    "dentro do Orquestra — sem abrir navegador.",
                    id="idea-md",
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

        # value do modelo → provedor (pra saber como falar com ele).
        _COMPANY_PROVIDER = {
            "☁️ Ollama Cloud (Kimi)": "ollama-cloud",
            "🚀 Antigravity (Gemini)": "gemini",
            "🧠 Anthropic Claude": "anthropic",
            "🛸 GitHub Copilot": "copilot",
            "🐋 DeepSeek": "deepseek",
            "🤖 OpenAI Codex": "openai",
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
                panel.remove_class("featured")
                event.button.remove_class("active")
                event.button.label = base_label
            else:
                event.button.add_class("active")
                event.button.label = f"{base_label} [✓]"
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

        cmd, binary, ofile = orquestra._build_orchestrator_cmd(orq_role, full_prompt)
        env = os.environ.copy()
        ef = orq_role.get("env_file")
        if ef:
            env.update(orquestra.load_env_file(ef))

        self.call_from_thread(log.write, f"\n[dim]🎼 Orquestrador ({shorten_name(self._orq_model)}) pensando...[/dim]\n")

        section = _TUILiveSection("Orquestrador", lambda t: None, lambda *a: None, on_status)
        section.open()
        final_text, ok = orquestra._run_orchestrator_stream(cmd, env, binary, section, timeout_s=150, output_file=ofile)
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
            panel = self.query_one("#idea-panel")
            panel.remove_class("hidden")
            panel.add_class("featured")   # a prévia toma a maior parte da tela
            btn = self.query_one("#btn-idea")
            btn.add_class("active"); btn.label = "🎨 Idea Studio [✓]"
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
