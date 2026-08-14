#!/usr/bin/env python3
"""
Orquestra — Interface de chat interativa para o Bernstein.
Arquitetura baseada em Papéis (Roles) e configuração via JSON.
"""

import os
import sys
import subprocess
import shutil
import json
import re
import time
import threading
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.table import Table
from rich import box
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style as PTStyle

# ─────────────────────────────────────────────────────────
# Configuração
# ─────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".orquestra.json"
HISTORY_FILE = Path.home() / ".orquestra_history"
DEEPSEEK_ENV = Path.home() / ".claude-deepseek-env"
CHAT_HISTORY_DIR = Path.home() / ".orquestra_chats"

DEFAULT_CONFIG = {
    "active_role": "orquestrador",
    "roles": {
        "orquestrador": {
            "binary": "agy",
            "model": "gemini-3.6-flash-high",
            "icon": "🎼",
            "color": "cyan",
            "description": "O Orquestra — chat fixo com memória do projeto. Troque o modelo aqui."
        },
        "ajudante": {
            "binary": "agy",
            "model": "gemini-3.6-flash-high",
            "icon": "⚡",
            "color": "yellow",
            "description": "Dúvidas rápidas, pesquisas e explicação de conceitos."
        },
        "implementador": {
            "binary": "claude",
            "env_file": str(DEEPSEEK_ENV),
            "icon": "💻",
            "color": "cyan",
            "description": "Codificador principal (escreve e altera arquivos)."
        },
        "fiscal": {
            "binary": "agy",
            "model": "gemini-3.1-pro-high",
            "icon": "🔎",
            "color": "magenta",
            "description": "Revisão de código e caça a bugs."
        },
        "segurança": {
            "binary": "agy",
            "model": "gemini-3.1-pro-high",
            "icon": "🛡️",
            "color": "red",
            "description": "Análise de vulnerabilidades de segurança."
        },
        "estrategista": {
            "binary": "agy",
            "model": "claude-opus-4-6-thinking",
            "icon": "🧠",
            "color": "blue",
            "description": "Planejamento de arquitetura e decisões complexas."
        },
        "analista": {
            "binary": "claude",
            "model": "claude-sonnet-5",
            "icon": "🔬",
            "color": "green",
            "description": "Análise profunda de código, diagnósticos e leitura crítica."
        }
    },
    "workers": {
        "claude": {
            "binary": "claude",
            "model": "",
            "icon": "💞",
            "label": "Operário Claude",
            "description": "Abertura no painel lateral para codificação pesada"
        },
        "agy": {
            "binary": "agy",
            "model": "gemini-3.1-pro-high",
            "icon": "💚",
            "label": "Operário Antigravity (Agy)",
            "description": "Abertura no painel lateral para pesquisa e análise"
        },
        "deepseek": {
            "binary": "claude",
            "model": "deepseek-v4-pro",
            "env_file": str(DEEPSEEK_ENV),
            "icon": "💙",
            "label": "Operário DeepSeek",
            "description": "Abertura no painel lateral via claude-deepseek"
        }
    }
}

console = Console()

def load_config() -> dict:
    CHAT_HISTORY_DIR.mkdir(exist_ok=True)
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        return DEFAULT_CONFIG
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    needs_save = False

    # Garante que todos os roles existem
    for role_name, role_data in DEFAULT_CONFIG["roles"].items():
        if role_name not in cfg.get("roles", {}):
            cfg.setdefault("roles", {})[role_name] = role_data
            needs_save = True

    # Garante que a seção workers existe
    if "workers" not in cfg:
        cfg["workers"] = DEFAULT_CONFIG["workers"]
        needs_save = True
    else:
        for w_name, w_data in DEFAULT_CONFIG["workers"].items():
            if w_name not in cfg["workers"]:
                cfg["workers"][w_name] = w_data
                needs_save = True
    
    if needs_save:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    
    return cfg

# ─────────────────────────────────────────────────────────
# Memória de Chat por Papel
# ─────────────────────────────────────────────────────────

import hashlib

def get_project_chat_dir() -> Path:
    """Cria um subdiretório único para o histórico de conversas do projeto atual."""
    cwd = Path.cwd().resolve()
    dir_hash = hashlib.md5(str(cwd).encode('utf-8')).hexdigest()[:8]
    safe_name = "".join(c for c in cwd.name if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
    if not safe_name:
        safe_name = "projeto"
    project_dir = CHAT_HISTORY_DIR / f"{safe_name}_{dir_hash}"
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir

def get_chat_file(role_name: str) -> Path:
    return get_project_chat_dir() / f"{role_name}.json"

def load_chat_history(role_name: str) -> list:
    chat_file = get_chat_file(role_name)
    if not chat_file.exists():
        return []
    with open(chat_file, "r", encoding="utf-8") as f:
        return json.load(f)

def save_chat_message(role_name: str, role: str, content: str):
    history = load_chat_history(role_name)
    history.append({"role": role, "content": content})
    # Mantém apenas as últimas 50 mensagens
    history = history[-50:]
    with open(get_chat_file(role_name), "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def get_session_id(role_name: str) -> str | None:
    """Retorna o ID da última sessão do agy para este papel no projeto atual."""
    session_file = get_project_chat_dir() / f"{role_name}.session"
    if session_file.exists():
        return session_file.read_text().strip()
    return None

def save_session_id(role_name: str, session_id: str):
    session_file = get_project_chat_dir() / f"{role_name}.session"
    session_file.write_text(session_id)

def show_recent_context():
    """Exibe silenciosamente as últimas 2 mensagens do orquestrador, sem nenhuma chamada de rede."""
    history = load_chat_history("orquestrador")
    if not history:
        return
    
    recent = history[-2:]
    console.print("[dim]── Retomando conversa ──[/dim]")
    for msg in recent:
        if msg["role"] == "user":
            console.print(f"[dim]  Você: {msg['content'][:120]}[/dim]")
        else:
            console.print(f"[dim]  🎼  {msg['content'][:120]}[/dim]")
    console.print()

def handle_safe_exit(config: dict):
    active_role = config.get("active_role", "")
    history = load_chat_history(active_role)
    
    if not history:
        console.print("\n[dim]Saindo... até mais! 👋[/dim]")
        sys.exit(0)
        
    try:
        ans = console.input("\n[bold yellow]Deseja gerar um arquivo RESUMO_DIA.md com o que foi feito hoje? (s/N): [/bold yellow]").strip().lower()
        if ans == "s":
            with console.status("[bold magenta]⏳ O Ajudante está gerando o resumo executivo (pode levar alguns segundos)...[/bold magenta]", spinner="dots"):
                history_text = "\n".join([f"{msg['role'].capitalize()}: {msg['content']}" for msg in history[-30:]])
                prompt = (
                    "Escreva um resumo em formato Markdown profissional explicando o que foi feito e decidido nesta sessão de trabalho. "
                    "Use o histórico abaixo como base. Foque em ações práticas, funcionalidades construídas e decisões tomadas. Seja direto.\n\n"
                    f"Histórico:\n{history_text}"
                )
                
                result = subprocess.run(
                    ["agy", "--model", "gemini-3.1-pro-high", "--print", prompt],
                    capture_output=True, text=True
                )
                
                if result.returncode == 0:
                    out = result.stdout.strip()
                    out = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', out)
                    
                    with open(Path(get_project_dir()) / "RESUMO_DIA.md", "w", encoding="utf-8") as f:
                        f.write(out)
                        
                    console.print("[green]✓ Arquivo RESUMO_DIA.md criado com sucesso no diretório do projeto![/green]")
                else:
                    console.print("[red]⚠ Falha ao gerar o resumo.[/red]")
    except (KeyboardInterrupt, EOFError):
        pass
        
    console.print("\n[dim]Saindo... até mais! 👋[/dim]")
    sys.exit(0)

# ─────────────────────────────────────────────────────────
# Utilitários
# ─────────────────────────────────────────────────────────

def load_env_file(env_path_str: str) -> dict:
    env = {}
    if not env_path_str:
        return env
    
    env_path = Path(os.path.expanduser(env_path_str))
    if not env_path.exists():
        return env
        
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" in line:
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip()
    return env

def get_project_dir() -> str:
    return os.getcwd()

def print_banner():
    console.print()
    title = Text()
    title.append("🎼  Orquestra", style="bold cyan")
    title.append("  —  Sua Equipe de Agentes", style="dim white")

    panel = Panel(
        title,
        subtitle=Text(f"pasta: {get_project_dir()}", style="dim"),
        border_style="cyan",
        padding=(0, 2),
    )
    console.print(panel)
    console.print()

def print_help():
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("Comando", style="bold cyan", no_wrap=True)
    table.add_column("Descrição", style="white")

    table.add_row("/agente <nome>",       "Troca o especialista ativo (ex: /agente fiscal)")
    table.add_row("/agentes",             "Menu interativo: troca o modelo de qualquer agente com setas")
    table.add_row("/config",              "Abre o arquivo ~/.orquestra.json para editar os modelos")
    table.add_row("/historico",           "Mostra o histórico de conversa do especialista ativo")
    table.add_row("/limpar-chat",         "Apaga o histórico do especialista ativo (começa do zero)")
    table.add_row("/interativo",          "Abre o agente ativo em sessão interativa")
    table.add_row("/status",              "Mostra agente ativo e pasta do projeto")
    table.add_row("/limpar",              "Limpa a tela")
    table.add_row("/sair  ou  /exit",     "Encerra o Orquestra")
    table.add_row("",                     "")
    table.add_row("<texto livre>  Enter", "Conversa com o Gerente Fixo e permite delegar tarefas.")

    console.print(Panel(table, title="[bold]Comandos[/bold]", border_style="dim cyan"))

def print_roles(config: dict):
    roles = config.get("roles", {})
    active = config.get("active_role", "")
    
    table = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
    table.add_column("", style="bold", no_wrap=True)
    table.add_column("Papel", style="bold cyan", no_wrap=True)
    table.add_column("Binário", style="dim white")
    table.add_column("Modelo Configurado", style="bold green")
    table.add_column("Descrição", style="white")

    for name, info in roles.items():
        marker = "▶" if name == active else " "
        style = f"bold {info.get('color', 'white')}" if name == active else "dim"
        
        # extrai o modelo se houver, ou avisa
        model_name = info.get("model", "-")
        if info.get("binary") == "claude" and not model_name or model_name == "-":
            if "deepseek" in info.get("env_file", ""):
                model_name = "API via ENV"
            else:
                model_name = "Anthropic Nativo"
                
        table.add_row(
            Text(f"{marker} {info.get('icon', '')}", style=style),
            Text(name.capitalize(), style=style),
            info.get("binary", ""),
            model_name,
            Text(info.get("description", ""), style=style),
        )

    console.print(Panel(table, title="[bold]Sua Equipe de Especialistas[/bold]", border_style="dim cyan"))

def auto_route_prompt(prompt: str, config: dict) -> str:
    roles = list(config.get("roles", {}).keys())
    if not roles:
        return config.get("active_role", "")
    
    roles_str = ", ".join(roles)
    
    system_prompt = (
        f"Você é o orquestrador inteligente de uma equipe de especialistas de IA. "
        f"Sua única função é ler o pedido do usuário e retornar EXATAMENTE UMA PALAVRA "
        f"da seguinte lista de especialistas, que melhor se encaixa no pedido: [{roles_str}]. "
        f"Não adicione nenhuma explicação, nem pontuação. Apenas o nome do especialista em minúsculas.\n\n"
        f"Pedido do usuário: {prompt}"
    )
    
    try:
        # Usando o agy com o modelo Flash que é ultra rápido
        result = subprocess.run(
            ["agy", "--model", "gemini-3.6-flash-high", "--print", system_prompt],
            capture_output=True,
            text=True,
            check=True
        )
        
        chosen_role = result.stdout.strip().lower()
        # Limpar pontuação caso a IA tenha retornado com ponto
        chosen_role = "".join(c for c in chosen_role if c.isalnum() or c == "-")
        
        # Verifica se a IA respondeu um papel válido
        if chosen_role in roles:
            return chosen_role
            
        # Fallback tenta encontrar se alguma role está contida na resposta
        for r in roles:
            if r in chosen_role:
                return r
                
    except Exception as e:
        # Se falhar silenciosamente (ex: CLI indisponível), retorna a role atual
        pass
        
    return config.get("active_role", "")

def edit_config():
    editor = os.environ.get("EDITOR", "nano")
    console.print(f"[dim]Abrindo {CONFIG_FILE} no editor ({editor})...[/dim]")
    subprocess.run([editor, str(CONFIG_FILE)])
    console.print("[green]\u2713 Configuração recarregada![/green]")

# ─────────────────────────────────────────────────────────
# Menu Interativo de Agentes (Swappable Brains)
# ─────────────────────────────────────────────────────────

# Modelos fixos de fallback (atualize aqui se sair modelo novo)
_CLAUDE_FALLBACK = [
    ("claude-sonnet-5",       "Claude Sonnet 5       (Recomendado)"),
    ("claude-opus-5",         "Claude Opus 5         (Máxima qualidade)"),
    ("claude-fable-5",        "Claude Fable 5        (Alta complexidade)"),
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5  (Rápido e barato)"),
]

_DEEPSEEK_FALLBACK = [
    ("deepseek-v4-pro",   "Deepseek V4 Pro   (Mais capaz)"),
    ("deepseek-v4-flash", "Deepseek V4 Flash (Rápido e barato)"),
]

def _fetch_agy_models() -> list[tuple[str, str]]:
    """Busca os modelos disponíveis no Antigravity em tempo real."""
    try:
        result = subprocess.run(["agy", "models"], capture_output=True, text=True, timeout=10)
        models = []
        for line in result.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                model_id, label = parts[0].strip(), parts[1].strip()
                if model_id and label and model_id != "Fetching":
                    models.append((model_id, f"{label:<45} ({model_id})"))
        return models if models else []
    except Exception:
        return []

def _fetch_claude_models() -> list[tuple[str, str]]:
    """Busca os modelos Claude via API Anthropic. Fallback para lista fixa."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                models = [
                    (m["id"], f"{m.get('display_name', m['id']):<45} ({m['id']})") 
                    for m in data.get("data", [])
                    if "claude" in m.get("id", "").lower()
                ]
                if models:
                    return models
        except Exception:
            pass
    return _CLAUDE_FALLBACK

def _fetch_deepseek_models() -> list[tuple[str, str]]:
    """Busca os modelos DeepSeek via API. Fallback para lista fixa."""
    env = load_env_file(str(DEEPSEEK_ENV))
    token = env.get("ANTHROPIC_AUTH_TOKEN", "")
    if token:
        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/models",
                headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                models = [
                    (m["id"], f"{m.get('id', m['id']):<45}")
                    for m in data.get("data", [])
                ]
                if models:
                    return models
        except Exception:
            pass
    return _DEEPSEEK_FALLBACK

_DIALOG_STYLE = PTStyle.from_dict({
    "dialog": "bg:#1e1e2e",
    "dialog.body": "bg:#1e1e2e fg:#cdd6f4",
    "dialog frame.label": "bg:#89b4fa fg:#1e1e2e bold",
    "dialog shadow": "bg:#11111b",
    "button": "bg:#313244 fg:#cdd6f4",
    "button.focused": "bg:#89b4fa fg:#1e1e2e bold",
    "radio-list": "bg:#1e1e2e fg:#cdd6f4",
    "radio-list .selected": "bg:#313244 fg:#89b4fa bold",
})

def interactive_agent_selection(config: dict):
    """Menu para trocar o modelo do Orquestrador ou dos Operários (Claude, Agy, DeepSeek)."""

    workers = config.get("workers", {})
    orch = config.get("roles", {}).get("orquestrador", {})

    # ─── Passo 1: Quem configurar? ───
    def _modelo_label(d: dict) -> str:
        m = d.get("model") or d.get("binary", "?")
        return m[:30] if m else "padrão"

    slot_choices = [
        ("orquestrador", f"🎼  Orquestrador          [{_modelo_label(orch)}]  — chat principal"),
        ("claude",       f"💞  Operário Claude        [{_modelo_label(workers.get('claude', {}))}]  — codificação pesada"),
        ("agy",          f"💚  Operário Antigravity   [{_modelo_label(workers.get('agy', {}))}]  — pesquisa e análise"),
        ("deepseek",     f"💙  Operário DeepSeek      [{_modelo_label(workers.get('deepseek', {}))}]  — via claude-deepseek"),
    ]
    slot = radiolist_dialog(
        title="\n  🎼  Orquestra — Trocar Inteligência",
        text="Passo 1 de 2  ──  O que você quer reconfigurar?",
        values=slot_choices,
        style=_DIALOG_STYLE,
        ok_text="Próximo →",
        cancel_text="Cancelar",
    ).run()
    if slot is None:
        console.print("[dim]✓ Nenhuma alteração feita.[/dim]")
        return

    # Operário DeepSeek sempre vai para deepseek (sem escolha de família)
    if slot == "deepseek":
        console.print("[dim]⏳ Buscando modelos DeepSeek...[/dim]", end="")
        model_choices = _fetch_deepseek_models()
        console.print("\r                                        \r", end="")
        if not model_choices:
            console.print("[bold red]✗ Não foi possível carregar os modelos DeepSeek.[/bold red]")
            return
        modelo = radiolist_dialog(
            title="\n  🎼  Orquestra — Operário DeepSeek",
            text="Passo 2 de 2  ──  Qual modelo DeepSeek?",
            values=model_choices,
            style=_DIALOG_STYLE,
            ok_text="✔ Confirmar",
            cancel_text="← Voltar",
        ).run()
        if modelo is None:
            console.print("[dim]✓ Nenhuma alteração feita.[/dim]")
            return
        config.setdefault("workers", {})["deepseek"]["model"] = modelo
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        console.print(f"\n[bold green]✓ Operário DeepSeek configurado![/bold green] Modelo: [bold]{modelo}[/bold]\n")
        return

    # Para Orquestrador, Claude e Agy: escolhe a família
    if slot in ("claude",):
        familia_choices = [
            ("claude",   "💞 Claude Nativo   — Sonnet, Opus"),
        ]
    elif slot == "agy":
        familia_choices = [
            ("gemini",   "💚 Gemini / Antigravity (agy)"),
        ]
    else:  # orquestrador: todas as opções
        familia_choices = [
            ("gemini",   "💚 Gemini / Antigravity  (agy) — Rápido e barato"),
            ("claude",   "💞 Claude Nativo          (claude) — Sonnet, Opus"),
            ("deepseek", "💙 Deepseek               — V4 Pro, V4 Flash"),
        ]

    if len(familia_choices) == 1:
        familia = familia_choices[0][0]
    else:
        familia = radiolist_dialog(
            title="\n  🎼  Orquestra — Trocar Inteligência",
            text=f"Passo 1.5 de 2  ──  Qual provedor?",
            values=familia_choices,
            style=_DIALOG_STYLE,
            ok_text="Próximo →",
            cancel_text="← Voltar",
        ).run()
        if familia is None:
            console.print("[dim]✓ Nenhuma alteração feita.[/dim]")
            return

    # ─── Passo 2: Modelo ───
    console.print("[dim]⏳ Buscando modelos...[/dim]", end="")
    if familia == "gemini":
        model_choices = _fetch_agy_models()
    elif familia == "claude":
        model_choices = _fetch_claude_models()
    else:
        model_choices = _fetch_deepseek_models()
    console.print("\r                                        \r", end="")

    if not model_choices:
        console.print("[bold red]✗ Não foi possível carregar os modelos. Tente novamente.[/bold red]")
        return

    slot_label = {"orquestrador": "Orquestrador", "claude": "Operário Claude", "agy": "Operário Agy"}.get(slot, slot)
    modelo = radiolist_dialog(
        title="\n  🎼  Orquestra — Trocar Inteligência",
        text=f"Passo 2 de 2  ──  Qual modelo para o {slot_label}?",
        values=model_choices,
        style=_DIALOG_STYLE,
        ok_text="✔ Confirmar",
        cancel_text="← Voltar",
    ).run()
    if modelo is None:
        console.print("[dim]✓ Nenhuma alteração feita.[/dim]")
        return

    # ─── Salva ───
    if slot == "orquestrador":
        if familia == "gemini":
            config["roles"]["orquestrador"]["binary"] = "agy"
            config["roles"]["orquestrador"]["model"] = modelo
            config["roles"]["orquestrador"].pop("env_file", None)
        elif familia == "claude":
            config["roles"]["orquestrador"]["binary"] = "claude"
            config["roles"]["orquestrador"]["model"] = modelo
            config["roles"]["orquestrador"].pop("env_file", None)
        else:
            config["roles"]["orquestrador"]["binary"] = "claude"
            config["roles"]["orquestrador"]["model"] = modelo
            config["roles"]["orquestrador"]["env_file"] = str(DEEPSEEK_ENV)
    elif slot == "claude":
        config.setdefault("workers", {})["claude"]["model"] = modelo
        config["workers"]["claude"]["binary"] = "claude"
    elif slot == "agy":
        config.setdefault("workers", {})["agy"]["model"] = modelo
        config["workers"]["agy"]["binary"] = "agy"

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    console.print(
        f"\n[bold green]✓ {slot_label} configurado![/bold green] "
        f"Modelo: [bold]{modelo}[/bold] via [dim]{familia.capitalize()}[/dim]\n"
    )


def launch_interactive(config: dict) -> int:
    role_name = config.get("active_role", "")
    roles = config.get("roles", {})
    
    if role_name not in roles:
        console.print(f"[bold red]✗ Papel ativo '{role_name}' não encontrado na configuração.[/bold red]")
        return 1
        
    role = roles[role_name]
    env = os.environ.copy()

    env_file = role.get("env_file")
    if env_file:
        env.update(load_env_file(env_file))

    binary = role.get("binary", "agy")

    if not shutil.which(binary):
        console.print(f"[bold red]✗ Binário '{binary}' não encontrado no PATH.[/bold red]")
        return 1

    cmd = [binary]
    has_history = bool(load_chat_history(role_name))
    
    if binary == "claude":
        if has_history:
            cmd.append("--continue")
    elif binary == "agy":
        model = role.get("model")
        if model:
            cmd.extend(["--model", model])
        if has_history:
            cmd.append("--continue")

    model_display = role.get("model", "Padrão (ou via ENV)")
    color = role.get("color", "white")
    icon = role.get("icon", "")
    mem_label = "[dim]🧠 Retomando conversa anterior[/dim]" if has_history else "[dim]✨ Nova conversa[/dim]"

    console.print()
    console.print(
        Panel(
            f"[bold {color}]{icon}  Invocando o {role_name.capitalize()} interativamente ({binary})[/bold {color}]\n"
            f"[dim]modelo: {model_display}[/dim]  {mem_label}\n"
            f"[dim]Use Ctrl+C ou /exit dentro do agente para voltar ao Orquestra.[/dim]",
            border_style=color,
            padding=(0, 2),
        )
    )
    console.print(Rule(style="dim"))

    try:
        result = subprocess.run(cmd, env=env)
        console.print(Rule(style="dim"))
        console.print("[dim]↩  De volta ao Orquestra.[/dim]")
        return result.returncode

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Interrompido.[/yellow]")
        return 130



# ─────────────────────────────────────────────────────────
# REPL principal
# ─────────────────────────────────────────────────────────

def main():
    config = load_config()

    print_banner()

    orch = config.get("roles", {}).get("orquestrador", {})
    modelo_atual = orch.get("model", orch.get("binary", "?"))
    console.print(f"  modelo: [dim]{modelo_atual}[/dim]  ·  [dim]/agentes trocar[/dim] para mudar  ·  [dim]/ajuda[/dim] para comandos\n")
    
    # Exibe contexto recente silenciosamente (sem chamada de rede)
    show_recent_context()

    session = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        style=Style.from_dict({
            "prompt": "bold ansicyan",
        }),
    )

    while True:
        try:
            # reload config a cada loop (caso tenha editado o json por fora)
            config = load_config()
            
            prompt_text = HTML(
                '<ansigreen><b>orquestra</b></ansigreen>'
                '<ansiwhite> → </ansiwhite>'
            )
            raw = session.prompt(prompt_text).strip()
        except (KeyboardInterrupt, EOFError):
            handle_safe_exit(config)

        if not raw:
            continue

        low = raw.lower()

        if low in ("/sair", "/exit", "/quit", "exit", "quit"):
            handle_safe_exit(config)

        elif low in ("/ajuda", "/help", "/?"):
            print_help()

        elif low in ("/agentes", "/agents", "/lista", "/roles"):
            print_roles(config)
            console.print("[dim]Dica: use [bold]/agentes trocar[/bold] para abrir o menu interativo de modelos.[/dim]")

        elif low in ("/agentes trocar", "/agents swap", "/trocar-agente", "/swap"):
            interactive_agent_selection(config)
            config = load_config()  # recarrega após a mudança
            
        elif low in ("/config", "/editar-config", "/edit"):
            edit_config()

        elif low.startswith("/agente") or low.startswith("/agent"):
            parts = raw.split(maxsplit=1)
            if len(parts) == 1:
                console.print(f"[yellow]Uso correto: /agente <nome do especialista>[/yellow]")
                continue
                
            requested = parts[1].strip().lower()
            if requested in config.get("roles", {}):
                config["active_role"] = requested
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
                    
                info = config["roles"][requested]
                console.print(
                    f"[bold {info.get('color', 'white')}]{info.get('icon', '')} Especialista trocado para: {requested.capitalize()}[/bold {info.get('color', 'white')}]"
                )
            else:
                console.print(f"[bold red]✗ Especialista desconhecido: '{requested}'[/bold red]")
                console.print(f"  Disponíveis: {', '.join(config.get('roles', {}).keys())}")

        elif low in ("/status", "/info"):
            active_role = config.get("active_role", "")
            info = config.get("roles", {}).get(active_role, {})
            table = Table(box=box.SIMPLE, show_header=False)
            table.add_column("Campo", style="dim")
            table.add_column("Valor", style="bold")
            table.add_row("Papel ativo", f"{info.get('icon', '')} {active_role.capitalize()}")
            table.add_row("Modelo", str(info.get("model", "Padrão")))
            table.add_row("Pasta do projeto", get_project_dir())
            table.add_row("Arquivo de Config", str(CONFIG_FILE))
            console.print(table)



        elif low in ("/historico", "/history"):
            active_role = config.get("active_role", "")
            info = config.get("roles", {}).get(active_role, {})
            history = load_chat_history(active_role)
            if not history:
                console.print(f"[dim]Nenhuma conversa registrada com {active_role.capitalize()} ainda.[/dim]")
            else:
                console.print(f"\n[bold]Histórico com {info.get('icon', '')} {active_role.capitalize()}[/bold] ({len(history)} mensagens)")
                for msg in history[-10:]:
                    prefix = "[bold white]Você:[/bold white]" if msg["role"] == "user" else f"[bold {info.get('color','white')}]{info.get('icon','')}:[/bold {info.get('color','white')}]"
                    console.print(f"  {prefix} {msg['content'][:200]}")
                console.print()

        elif low in ("/limpar-chat", "/clear-chat", "/reset-chat"):
            active_role = config.get("active_role", "")
            chat_file = get_chat_file(active_role)
            session_file = CHAT_HISTORY_DIR / f"{active_role}.session"
            if chat_file.exists(): chat_file.unlink()
            if session_file.exists(): session_file.unlink()
            console.print(f"[green]✓ Histórico do {active_role.capitalize()} apagado. Próxima conversa começa do zero.[/green]")

        elif low in ("/limpar", "/clear", "clear"):
            console.clear()
            print_banner()

        elif low == "/interativo":
            launch_interactive(config)

        else:
            # O Chat Fixo sempre usa a identidade "orquestrador"
            import datetime
            role_name = "orquestrador"
            roles = config.get("roles", {})
            role = roles.get(role_name, {})
            binary = role.get("binary", "agy")
            model = role.get("model")
            color = role.get("color", "cyan")
            icon = role.get("icon", "🎼")
            projeto = Path(get_project_dir()).name
            agora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
            
            save_chat_message(role_name, "user", raw)
            history = load_chat_history(role_name)
            
            context_str = (
                f"Você é o Orquestra, o assistente inteligente do projeto '{projeto}'.\n"
                f"Hoje é {agora}. Você está no terminal do usuário, na pasta '{get_project_dir()}'.\n"
                "Seu objetivo é conversar naturalmente, lembrar o contexto, tirar dúvidas e gerenciar o projeto.\n"
                "REGRA DE OURO: Você NÃO DEVE inventar código, alucinar ou chutar o que não sabe com certeza.\n"
                "Quando precisar executar uma tarefa prática (ler/escrever código, rodar comandos, acessar web), DELEGUE para um operário.\n"
                "Para delegar, inclua ao final da resposta um ou mais blocos:\n"
                "<DELEGATE binary=\"claude\">\n(Instruções completas para o operário)\n</DELEGATE>\n"
                "Use binary=\"claude\" para codificação, ou binary=\"agy\" para pesquisa e análise rápida.\n"
                "Você pode convocar múltiplos operários de uma vez gerando múltiplos blocos <DELEGATE>.\n\n"
                "Histórico da Conversa:\n"
            )
            for msg in history[-20:]:
                prefix = "Usuário" if msg["role"] == "user" else "Orquestra"
                context_str += f"{prefix}: {msg['content']}\n"
            
            prompt = f"{context_str}\n\nResponda ao usuário agora:"
            
            cmd = [binary]
            if binary == "agy" and model:
                cmd.extend(["--model", model])
            if binary == "claude":
                cmd.extend(["-p", prompt])
            else:
                cmd.extend(["--print", prompt])
            
            env = os.environ.copy()
            env_file = role.get("env_file")
            if env_file:
                env.update(load_env_file(env_file))
                
            with console.status(f"[bold {color}]⏳ {icon} {role_name.capitalize()} está pensando e orquestrando...[/bold {color}]", spinner="dots"):
                result = subprocess.run(cmd, env=env, capture_output=True, text=True)
                
            if result.returncode != 0:
                console.print(f"[bold red]⚠ Erro ao processar no {role_name}[/bold red]")
                if result.stderr:
                    print(result.stderr.strip())
                continue

            output = result.stdout.strip()
            # Se usar claude via -p, o claude pode cuspir um monte de lixo visual, 
            # mas vamos tratar da mesma forma.
            clean_output = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', output)
            
            # Busca TODAS as tags de delegação
            delegate_matches = list(re.finditer(r'<DELEGATE\s+binary="([^"]+)">(.*?)</DELEGATE>', clean_output, re.DOTALL | re.IGNORECASE))
            
            if delegate_matches:
                # Exibe a resposta removendo TODAS as tags para o usuário ver
                user_output = re.sub(r'<DELEGATE.*?</DELEGATE>', '', output, flags=re.DOTALL | re.IGNORECASE).strip()
                
                save_chat_message(role_name, "assistant", clean_output)
                if user_output:
                    console.print(f"\n[bold {color}]{icon} {role_name.capitalize()}:[/bold {color}]\n{user_output}\n")
                
                for idx, match in enumerate(delegate_matches):
                    target_binary_tag = match.group(1).lower()
                    delegate_instruction = match.group(2).strip()
                    
                    # Resolve qual worker usar
                    workers = config.get("workers", {})
                    if target_binary_tag == "deepseek":
                        worker = workers.get("deepseek", {})
                        actual_binary = "claude"
                    elif target_binary_tag == "claude":
                        worker = workers.get("claude", {})
                        actual_binary = "claude"
                    elif target_binary_tag == "agy":
                        worker = workers.get("agy", {})
                        actual_binary = "agy"
                    else:
                        worker = workers.get("claude", {})
                        actual_binary = "claude"

                    worker_model = worker.get("model", "")
                    worker_env_file = worker.get("env_file", "")

                    # Escreve a instrução em arquivo temporário (evita problemas de aspas no shell)
                    import tempfile
                    tmp_fd, tmp_path = tempfile.mkstemp(prefix="orquestra_", suffix=".md")
                    with os.fdopen(tmp_fd, "w") as f:
                        f.write(delegate_instruction)

                    # Monta prefixo de env para deepseek
                    env_prefix = ""
                    if worker_env_file and Path(os.path.expanduser(worker_env_file)).exists():
                        env_vars = load_env_file(worker_env_file)
                        env_exports = " ".join(f'{k}="{v}"' for k, v in env_vars.items())
                        env_prefix = f"export {env_exports} && "

                    # Monta o comando do operário — abre INTERATIVAMENTE (não usa -p/--print)
                    if actual_binary == "agy" and worker_model:
                        binary_cmd = f"agy --model {worker_model}"
                    elif actual_binary == "agy":
                        binary_cmd = "agy"
                    elif actual_binary == "claude" and worker_model:
                        binary_cmd = f"claude --model {worker_model}"
                    else:
                        binary_cmd = "claude"

                    # Script que: mostra instrução → abre ferramenta interativamente
                    show_and_open = (
                        f"bash -c \""
                        f"printf '\\033[1;33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m\\n' && "
                        f"printf '\\033[1;36m🎼 Instrução do Orquestra (Tarefa {idx+1}):\\033[0m\\n' && "
                        f"printf '\\033[1;33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m\\n' && "
                        f"cat {tmp_path} && "
                        f"rm -f {tmp_path} && "
                        f"printf '\\n\\033[1;33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m\\n' && "
                        f"{env_prefix}{binary_cmd}\""
                    )

                    label = {"claude": "Claude Code", "agy": "Antigravity"}.get(actual_binary, actual_binary)
                    if target_binary_tag == "deepseek":
                        label = "DeepSeek"
                    console.print(f"[bold yellow]⚡ Abrindo {label} (Tarefa {idx+1})...[/bold yellow]")
                    
                    # Layout: 1º corta na vertical, 2º em diante corta na horizontal do lado direito
                    split_flag = "-h" if idx == 0 else "-v"
                    pane_result = subprocess.run(
                        ["tmux", "split-window", split_flag, "-p", "50",
                         "-P", "-F", "#{pane_id}", show_and_open],
                        capture_output=True, text=True
                    )
                    pane_id = pane_result.stdout.strip()

                    # Caminho do arquivo para capturar saída do operário
                    safe_pid = pane_id.replace("%", "")
                    output_capture_file = f"/tmp/orquestra_capture_{safe_pid}.txt"

                    # Ativa captura de saída do painel em tempo real
                    subprocess.run(["tmux", "pipe-pane", "-t", pane_id,
                                    f"cat >> {output_capture_file}"])

                    # Thread unificada: envia instrução + monitora encerramento + captura resposta
                    def _worker_lifecycle(pid: str, instr: str, cap_file: str, lbl: str):
                        # 1. Aguarda a ferramenta inicializar e envia a instrução
                        time.sleep(3)
                        subprocess.run(["tmux", "set-buffer", instr])
                        subprocess.run(["tmux", "paste-buffer", "-t", pid])
                        time.sleep(0.15)
                        subprocess.run(["tmux", "send-keys", "-t", pid, "", "Enter"])

                        # 2. Monitora até o painel fechar (usuário digita /exit ou fecha)
                        while True:
                            active = subprocess.run(
                                ["tmux", "list-panes", "-a", "-F", "#{pane_id}"],
                                capture_output=True, text=True
                            ).stdout
                            if pid not in active:
                                break
                            time.sleep(2)

                        # 3. Para a captura
                        subprocess.run(["tmux", "pipe-pane", "-t", pid], capture_output=True)

                        # 4. Lê e limpa a saída capturada
                        captured = ""
                        if os.path.exists(cap_file):
                            with open(cap_file, "r", errors="replace") as f:
                                captured = f.read()
                            os.remove(cap_file)

                        # Remove códigos ANSI
                        captured = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', captured)
                        captured = captured.strip()

                        if captured:
                            # Salva resposta do operário no histórico do Orquestrador
                            summary = f"[Resposta do {lbl}]:\n{captured[:4000]}"
                            save_chat_message("orquestrador", "assistant", summary)
                            # Notifica no painel principal
                            subprocess.run([
                                "tmux", "display-message",
                                f"✓ {lbl} terminou — resposta salva no contexto do Orquestra"
                            ])

                    threading.Thread(
                        target=_worker_lifecycle,
                        args=(pane_id, delegate_instruction, output_capture_file, label),
                        daemon=True
                    ).start()

                console.print()
            else:
                save_chat_message(role_name, "assistant", clean_output)
                console.print(f"\n[bold {color}]{icon} {role_name.capitalize()}:[/bold {color}]\n{output}\n")

if __name__ == "__main__":
    main()
