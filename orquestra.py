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
import fnmatch
import time
import datetime
import threading
import tempfile
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from vault import VaultManager

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.rule import Rule
from rich.table import Table
from rich.markdown import Markdown
from rich.padding import Padding
from rich import box
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
import os
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.patch_stdout import patch_stdout

# ─────────────────────────────────────────────────────────
# Configuração
# ─────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".orquestra.json"
HISTORY_FILE = Path.home() / ".orquestra_history"
DEEPSEEK_ENV = Path.home() / ".claude-deepseek-env"
CHAT_HISTORY_DIR = Path.home() / ".orquestra_chats"

# ── Arquitetura de 3 agentes + Cão de Guarda ────────────────────────────────
# roles.orquestrador  → o ESTRATEGISTA. binary="brain" = chat completions puro
#                       (orchestrator_brain.py), zero ferramentas por construção.
#                       Só conversa, planeja e delega — nunca executa nada.
# workers.operador     → EXECUTA: lê, escreve, altera código, roda comandos.
# workers.revisor      → REVISA sob demanda (bugs, arquitetura, segurança).
# workers.cao_guarda   → Copilot AUTOMÁTICO e silencioso: audita cada git diff.
# workers.claude/agy/deepseek/codex → atalhos de motor específico (uso avançado).
DEFAULT_CONFIG = {
    "active_role": "orquestrador",
    "roles": {
        "orquestrador": {
            "binary": "brain",
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "icon": "🎼",
            "color": "cyan",
            "description": "O estrategista — conversa, planeja e delega. Nunca executa nada sozinho."
        }
    },
    "workers": {
        "operador": {
            "binary": "claude",
            "model": "claude-sonnet-5",
            "icon": "🔨",
            "color": "cyan",
            "label": "Operador",
            "description": "Executa: lê, escreve e altera código, roda comandos."
        },
        "revisor": {
            "binary": "claude",
            "model": "claude-opus-5",
            "icon": "🔍",
            "color": "blue",
            "label": "Revisor",
            "description": "Revisa código sob demanda: bugs, arquitetura, segurança."
        },
        "cao_guarda": {
            "binary": "copilot",
            "model": "auto",
            "icon": "🐕",
            "color": "yellow",
            "label": "Cão de Guarda",
            "description": "Copilot automático — audita cada git diff em silêncio."
        },
        "claude": {
            "binary": "claude",
            "model": "",
            "icon": "💞",
            "label": "Motor Claude",
            "description": "Atalho: força o Claude Code nativo."
        },
        "agy": {
            "binary": "agy",
            "model": "gemini-3.1-pro-high",
            "icon": "💚",
            "label": "Motor Antigravity",
            "description": "Atalho: força o Antigravity (Gemini)."
        },
        "deepseek": {
            "binary": "claude",
            "model": "deepseek-v4-pro",
            "env_file": str(DEEPSEEK_ENV),
            "icon": "💙",
            "label": "Motor DeepSeek",
            "description": "Atalho: Claude Code apontado para a API do DeepSeek."
        },
        "codex": {
            "binary": "codex",
            "model": "",
            "icon": "🤖",
            "label": "Motor Codex",
            "description": "Atalho: força o Codex CLI (OpenAI)."
        },
        "opencode": {
            "binary": "opencode",
            "model": "",
            "icon": "🌐",
            "label": "Motor OpenCode",
            "description": "Atalho: força o OpenCode CLI (OpenRouter)."
        }
    },
    "ui": {
        "mode": "chat",
        "detail": "normal",
        "learning_mode": False
    },
    # Open Design (nexu-io/open-design) — prévia visual real de mudanças de UI.
    # repo_path = clone; node_bin_dir = Node oficial (o do Kali não roda TS);
    # agent = "claude" (o default do projeto, antigravity/Gemini, não replica
    # bem o design system real — ver VALIDACAO_OPEN_DESIGN.md §10).
    "open_design": {
        "repo_path": str(Path.home() / "Documentos" / "Kali Linux" / "open-design"),
        "node_bin_dir": str(Path.home() / ".local" / "opt" / "node-open-design" / "bin"),
        "agent": "claude"
    },
    # Gate por risco: nasce DESLIGADO — é a única feature que muda o
    # comportamento padrão de quem já usa a ferramenta, então ligar é decisão
    # consciente do usuário (config ou /modo autonomo), nunca automático.
    "risk_gates": {
        "enabled": False,
        "always_confirm_patterns": ["*.env*", "*secret*", "*credential*", "*/auth*", "*.pem", "*.key"],
    },
}


console = Console()

# Protege prints concorrentes vindos de threads de worker diferentes (ex.: dois
# <DELEGATE> paralelos escrevendo ao mesmo tempo) pra nunca quebrar uma linha
# no meio — sem isso, deltas de texto de dois operários podem se entrelaçar
# caractere a caractere na tela.
_console_lock = threading.RLock()

# Arquivo de log para o "Modo Cursor" (Terminal Limpo)
LOG_FILE_NAME = ".orquestra_logs.md"
_log_lock = threading.RLock()

def _append_log(text: str):
    """Escreve texto no arquivo de log de forma thread-safe."""
    log_path = Path(get_project_dir()) / LOG_FILE_NAME
    with _log_lock:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text)

# Último plano gerado pelo Orquestrador (exibido via /plano)
_last_plan: str = ""


_PLAN_STEP_RE = re.compile(
    r'^\s*(?:\d+[\.\):]?\s*)?([A-Za-zÀ-ÿ_-]+)\s*(?:→|->|-|:)\s*(.+)$'
)

# Rich usa nomes de cor "puros" (cyan, blue...); o prompt_toolkit HTML() aceita
# o prefixo ansi* garantido em qualquer terminal (8 cores padrão), sem depender
# de suporte a truecolor — mantém a paleta idêntica à usada no resto do app
# (_WORKER_TAG_DEFAULTS/config workers) sem risco de cor não reconhecida.
_ANSI_COLOR_MAP = {
    "cyan": "ansicyan", "blue": "ansiblue", "yellow": "ansiyellow",
    "green": "ansigreen", "magenta": "ansimagenta", "red": "ansired",
    "white": "ansiwhite",
}


def _model_label(worker: dict) -> str:
    return worker.get("model") or worker.get("binary") or "padrão"


def show_plan_viewer(plan_text: str, config: dict = None) -> None:
    """Exibe o plano em tela cheia no terminal. Fecha com ESC, Enter ou Q.

    Cada linha do plano ("N. papel → descrição") é interpretada e renderizada
    com o mesmo ícone/cor/rótulo usados no resto do app para aquele papel
    (via _resolve_worker), mais o modelo real configurado pra ele — assim o
    usuário aprende, só de ler o plano, qual motor faz cada etapa antes de
    aprovar. Linha que não bater com o formato esperado cai num fallback
    (texto simples), pra nunca sumir conteúdo se o Orquestrador fugir do
    padrão pedido no prompt."""
    from prompt_toolkit import Application
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.formatted_text import HTML
    import shutil
    import html as html_mod

    config = config or {}

    kb = KeyBindings()

    @kb.add("escape")
    @kb.add("enter")
    @kb.add("q")
    @kb.add("Q")
    def _exit(event):
        event.app.exit()

    term_width = shutil.get_terminal_size((80, 24)).columns
    separator = "─" * min(term_width - 2, 78)

    parts = [
        "\n",
        '  <b><style fg="ansicyan">📋  Plano de Execução</style></b>\n',
        f'  <style fg="ansiwhite">{separator}</style>\n',
        "\n",
    ]

    steps = []
    for raw_line in plan_text.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _PLAN_STEP_RE.match(line)
        if m:
            steps.append(("step", m.group(1), m.group(2).strip()))
        else:
            steps.append(("raw", None, line))

    for idx, (kind, tag, text) in enumerate(steps):
        if idx > 0:
            parts.append('  <style fg="ansiwhite">↓</style>\n')
        if kind == "step":
            worker, _binary, label, icon, color = _resolve_worker(config, tag.lower())
            ansi_color = _ANSI_COLOR_MAP.get(color, "ansiwhite")
            modelo = html_mod.escape(_model_label(worker))
            desc = html_mod.escape(text)
            parts.append(
                f'  {icon}  <b><style fg="{ansi_color}">{label.upper()}</style></b>'
                f'  <style fg="ansiwhite">·  {modelo}</style>\n'
            )
            parts.append(f'      {desc}\n\n')
        else:
            parts.append(f'  <style fg="ansiwhite">{html_mod.escape(text)}</style>\n')

    parts.append(f'  <style fg="ansiwhite">{separator}</style>\n')
    parts.append('  <style fg="ansiwhite">[ Enter ou ESC para fechar e voltar ao chat ]</style>\n')

    content_text = "".join(parts)

    app = Application(
        layout=Layout(
            Window(
                content=FormattedTextControl(text=HTML(content_text)),
                wrap_lines=True,
            )
        ),
        key_bindings=kb,
        full_screen=True,
    )
    app.run()


def load_config() -> dict:
    CHAT_HISTORY_DIR.mkdir(exist_ok=True)
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        return DEFAULT_CONFIG
    
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    needs_save = False

    # ── Sanidade do Orquestrador ────────────────────────────────────────────
    # O Orquestrador roda em MODO PLANO / read-only, via:
    #  - binary "brain"  → chat API (provider: deepseek | ollama-cloud | anthropic | openai | gemini)
    #  - binary "agy" | "claude" | "codex" → a CLI logada no terminal, sem escrita
    # Conserta configs quebradas (ex: provider "copilot", que o brain não fala).
    orq = cfg.get("roles", {}).get("orquestrador", {})
    if orq:
        b = orq.get("binary", "brain")
        m = (orq.get("model") or "").lower()
        p = orq.get("provider", "")
        _fixed = None
        if b == "brain" and p not in ("deepseek", "ollama-cloud", "anthropic", "openai", "gemini"):
            # provider inválido — re-roteia pelo nome do modelo
            if m.startswith("gemini"):
                orq["binary"] = "agy"; orq.pop("provider", None); _fixed = "agy (login do Antigravity)"
            elif m.startswith("claude"):
                orq["binary"] = "claude"; orq.pop("provider", None); _fixed = "claude (login do Claude Code)"
            elif m.startswith(("gpt-", "o1", "o3", "o4")):
                orq["binary"] = "codex"; orq.pop("provider", None); _fixed = "codex (login do Codex)"
            elif m.startswith("deepseek"):
                orq["provider"] = "deepseek"; _fixed = "brain/deepseek"
            else:
                orq["binary"] = "brain"; orq["provider"] = "ollama-cloud"
                orq["model"] = "gpt-oss:120b"; _fixed = "brain/ollama-cloud (gpt-oss:120b)"
        if _fixed:
            console.print(f"[dim]⚙  Orquestrador reconfigurado ({p or b} → {_fixed}) — era uma combinação que não conecta.[/dim]")
            needs_save = True

    # ollama_api_key em texto puro nunca foi usado (o brain lê do provider) — remove
    if "ollama_api_key" in cfg:
        del cfg["ollama_api_key"]
        needs_save = True

    # Open Design: semeia a seção se ausente (só se o clone existir de fato).
    if "open_design" not in cfg and Path(DEFAULT_CONFIG["open_design"]["repo_path"]).is_dir():
        cfg["open_design"] = dict(DEFAULT_CONFIG["open_design"])
        needs_save = True

    # Garante que todos os roles do default existem
    for role_name, role_data in DEFAULT_CONFIG["roles"].items():
        if role_name not in cfg.get("roles", {}):
            cfg.setdefault("roles", {})[role_name] = role_data
            needs_save = True

    # Garante que a seção workers existe e tem operador/revisor/cao_guarda
    if "workers" not in cfg:
        cfg["workers"] = DEFAULT_CONFIG["workers"]
        needs_save = True
    else:
        for w_name, w_data in DEFAULT_CONFIG["workers"].items():
            if w_name not in cfg["workers"]:
                cfg["workers"][w_name] = w_data
                needs_save = True

    # Garante que a seção ui existe e tem 'detail'
    if "ui" not in cfg:
        cfg["ui"] = dict(DEFAULT_CONFIG["ui"])
        needs_save = True
    else:
        for k, v in DEFAULT_CONFIG["ui"].items():
            if k not in cfg["ui"]:
                cfg["ui"][k] = v
                needs_save = True

    # Gate por risco: seção nova, sempre nasce com enabled=False (ver comentário
    # no DEFAULT_CONFIG) — nunca liga sozinha numa migração.
    if "risk_gates" not in cfg:
        cfg["risk_gates"] = dict(DEFAULT_CONFIG["risk_gates"])
        needs_save = True
    else:
        for k, v in DEFAULT_CONFIG["risk_gates"].items():
            if k not in cfg["risk_gates"]:
                cfg["risk_gates"][k] = v
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

# ─────────────────────────────────────────────────────────
# Memória de Aprendizado (SQLite) — diferente do histórico de chat acima
# (bruto, truncado, por conversa): aqui ficam lições, preferências e o
# desempenho de cada operário, que persistem e se ACUMULAM entre sessões —
# é o que faz o Orquestra ficar mais informado com o tempo, não só logar.
# ─────────────────────────────────────────────────────────

import sqlite3

MEMORY_DB_FILE = CHAT_HISTORY_DIR / "memory.db"
_memory_db_lock = threading.Lock()


def get_project_id() -> str:
    """Mesmo hash de get_project_chat_dir() (isola por pasta) — reaproveitado
    aqui como chave de projeto na memória de aprendizado. NULL/None em vez
    disso significa 'lição global', válida em qualquer projeto."""
    cwd = Path.cwd().resolve()
    return hashlib.md5(str(cwd).encode('utf-8')).hexdigest()[:8]


def _init_memory_db():
    CHAT_HISTORY_DIR.mkdir(exist_ok=True)
    with _memory_db_lock:
        conn = sqlite3.connect(MEMORY_DB_FILE)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    title TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Migração: coluna com o snapshot do `git status --porcelain` ANTES
            # do operário rodar. É o que permite /rejeitar isolar o rollback só
            # nos arquivos desta tarefa em vez de arrasar a árvore inteira —
            # ver _git_status_paths()/get_pending_tasks() e os handlers de
            # /aprovar e /rejeitar mais abaixo.
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN pre_snapshot TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # coluna já existe (banco de uma sessão anterior)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    category TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT,
                    weight REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_performance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    agent_tag TEXT NOT NULL,
                    task_kind TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            # Migração: custo/uso por chamada — só o Claude devolve total_cost_usd
            # de verdade (assinatura/API não expõe preço pro Agy/Codex); os demais
            # ficam 0.0 e o que importa pra eles são as colunas de tokens.
            for col_def in ("cost_usd REAL NOT NULL DEFAULT 0",
                            "tokens_in INTEGER NOT NULL DEFAULT 0",
                            "tokens_out INTEGER NOT NULL DEFAULT 0",
                            "duration_ms INTEGER NOT NULL DEFAULT 0"):
                try:
                    conn.execute(f"ALTER TABLE agent_performance ADD COLUMN {col_def}")
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # coluna já existe (banco de uma sessão anterior)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_authorship (
                    project_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    agent_tag TEXT NOT NULL,
                    agent_label TEXT NOT NULL,
                    last_instruction TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, file_path)
                )
            """)
            conn.commit()
        finally:
            conn.close()


_TASK_KIND_KEYWORDS = {
    "seguranca": ("segur", "vulnerab", "exploit", "senha", "credencial", "auth"),
    "ui": ("tela", "layout", "botão", "botao", "cor ", "design", "visual", "componente"),
    "fix": ("bug", "erro", "corrigi", "conserta", "falha", "quebrado"),
    "refactor": ("refator", "limpa", "organiza", "reestrutura"),
    "analise": ("analis", "revis", "explica", "avalia"),
}


def create_task(title: str, agent: str, pre_snapshot: str = None) -> int:
    pid = get_project_id()
    import datetime
    now = datetime.datetime.now().isoformat()
    with _memory_db_lock:
        conn = sqlite3.connect(MEMORY_DB_FILE)
        try:
            cursor = conn.execute(
                "INSERT INTO tasks (project_id, title, agent, status, result, created_at, updated_at, pre_snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, title, agent, "⏳ Em execução", None, now, now, pre_snapshot)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

def update_task_status(task_id: int, status: str, result: str = None):
    import datetime
    now = datetime.datetime.now().isoformat()
    with _memory_db_lock:
        conn = sqlite3.connect(MEMORY_DB_FILE)
        try:
            if result is not None:
                conn.execute(
                    "UPDATE tasks SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                    (status, result, now, task_id)
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, task_id)
                )
            conn.commit()
        finally:
            conn.close()

def get_recent_tasks(limit=10) -> list:
    pid = get_project_id()
    with _memory_db_lock:
        conn = sqlite3.connect(MEMORY_DB_FILE)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                (pid, limit)
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

def get_task(task_id: int) -> dict:
    with _memory_db_lock:
        conn = sqlite3.connect(MEMORY_DB_FILE)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def get_pending_tasks() -> list:
    """Tarefas do projeto atual com status 'Aguardando Aprovação' — usado por
    /rejeitar para detectar tarefas irmãs (ex.: outra etapa de um mesmo
    <PIPELINE>, ou outro <DELEGATE> paralelo) que também tocaram a árvore
    e seriam destruídas por um git reset --hard cego."""
    pid = get_project_id()
    with _memory_db_lock:
        conn = sqlite3.connect(MEMORY_DB_FILE)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE project_id = ? AND status = ? ORDER BY id",
                (pid, "⚠️ Aguardando Aprovação")
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()


def _git_status_paths(cwd: str) -> set:
    """Conjunto de caminhos com mudanças segundo `git status --porcelain`
    (staged, unstaged e untracked). Base do snapshot antes/depois que permite
    /aprovar e /rejeitar agirem só nos arquivos de UMA tarefa, em vez de
    'git add .' / 'git reset --hard' na árvore inteira (que arrastaria junto
    qualquer edição manual do usuário ou de outra tarefa ainda pendente)."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd,
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return set()
    if res.returncode != 0:
        return set()
    paths = set()
    for line in res.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:  # renome: "old -> new"
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"')
        if path and not _is_noise_path(path):
            paths.add(path)
    return paths

def commit_pending(cwd: str, message: str) -> str:
    """Commita os arquivos com mudança não commitada (ignora ruído tipo
    __pycache__). Retorna a lista de arquivos commitados, ou '' se nada."""
    dirty = _git_dirty_files(cwd)
    if not dirty:
        return ""
    subprocess.run(["git", "add", "--"] + sorted(dirty), cwd=cwd, capture_output=True)
    r = subprocess.run(["git", "commit", "-m", message], cwd=cwd, capture_output=True, text=True)
    return ", ".join(sorted(dirty)) if r.returncode == 0 else ""


def revert_pending(cwd: str) -> str:
    """Descarta as mudanças não commitadas: checkout dos rastreados + clean dos
    novos (menos ruído). Retorna a lista revertida."""
    try:
        res = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                              capture_output=True, text=True, timeout=10)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    tracked, untracked = [], []
    for line in res.stdout.splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if _is_noise_path(path):
            continue
        (untracked if code == "??" else tracked).append(path)
    if tracked:
        subprocess.run(["git", "checkout", "HEAD", "--"] + tracked, cwd=cwd, capture_output=True)
    if untracked:
        subprocess.run(["git", "clean", "-fd", "--"] + untracked, cwd=cwd, capture_output=True)
    return ", ".join(sorted(tracked + untracked))


def _infer_task_kind(instruction: str) -> str:
    """Heurística simples por palavra-chave, sem chamada de LLM e sem mexer no
    formato das tags DELEGATE/PIPELINE. 'geral' quando nada bate."""
    low = (instruction or "").lower()
    for kind, keywords in _TASK_KIND_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return kind
    return "geral"


def _record_agent_performance(project_id: str, agent_tag: str, task_kind: str, outcome: str,
                               cost_usd: float = 0.0, tokens_in: int = 0, tokens_out: int = 0,
                               duration_ms: int = 0):
    """Chamada mecânica (sem LLM) logo após um worker/pipeline/Open Design
    terminar. Degrada silenciosamente em qualquer erro — isso é telemetria de
    aprendizado, nunca deve derrubar o turno do usuário por causa dela."""
    try:
        with _memory_db_lock:
            conn = sqlite3.connect(MEMORY_DB_FILE)
            try:
                conn.execute(
                    "INSERT INTO agent_performance "
                    "(project_id, agent_tag, task_kind, outcome, created_at, cost_usd, tokens_in, tokens_out, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (project_id, agent_tag, task_kind, outcome, datetime.datetime.now().isoformat(),
                     cost_usd, tokens_in, tokens_out, duration_ms),
                )
                conn.commit()
            finally:
                conn.close()
    except sqlite3.Error:
        pass


def get_session_usage_totals(project_id: str) -> dict:
    """Soma cost_usd/tokens/chamadas gravados em agent_performance pro projeto
    atual — usado pelo rodapé de uso da TUI. Nunca lança: telemetria opcional."""
    try:
        with _memory_db_lock:
            conn = sqlite3.connect(MEMORY_DB_FILE)
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(tokens_in), 0), "
                    "COALESCE(SUM(tokens_out), 0), COUNT(*) FROM agent_performance WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
            finally:
                conn.close()
        return {"cost_usd": row[0], "tokens_in": row[1], "tokens_out": row[2], "calls": row[3]}
    except sqlite3.Error:
        return {"cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "calls": 0}


def set_file_author(project_id: str, file_path: str, agent_tag: str, agent_label: str,
                     last_instruction: str = ""):
    """Upsert em file_authorship — chamado só depois que a tarefa é aprovada
    (ver commit_pending/_handle_approval na TUI). Se a tarefa for rejeitada,
    quem chama simplesmente não invoca isto — nada fica gravado."""
    try:
        with _memory_db_lock:
            conn = sqlite3.connect(MEMORY_DB_FILE)
            try:
                conn.execute(
                    "INSERT INTO file_authorship (project_id, file_path, agent_tag, agent_label, last_instruction, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(project_id, file_path) DO UPDATE SET "
                    "agent_tag=excluded.agent_tag, agent_label=excluded.agent_label, "
                    "last_instruction=excluded.last_instruction, updated_at=excluded.updated_at",
                    (project_id, file_path, agent_tag, agent_label, (last_instruction or "")[:300],
                     datetime.datetime.now().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
    except sqlite3.Error:
        pass


def get_file_author(project_id: str, file_path: str) -> dict | None:
    """Lê quem editou por último um arquivo — None se nunca foi tocado por um
    agente do Orquestra (ou se o registro nunca chegou a ser aprovado)."""
    try:
        with _memory_db_lock:
            conn = sqlite3.connect(MEMORY_DB_FILE)
            try:
                row = conn.execute(
                    "SELECT agent_tag, agent_label, last_instruction, updated_at FROM file_authorship "
                    "WHERE project_id = ? AND file_path = ?",
                    (project_id, file_path),
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        return {"agent_tag": row[0], "agent_label": row[1], "last_instruction": row[2], "updated_at": row[3]}
    except sqlite3.Error:
        return None


def _store_lesson(project_id: str | None, category: str, text: str, source: str = ""):
    """Grava uma lição curta — dedup por texto exato (se já existe, só reforça
    peso/uso) e poda pras 200 de maior peso/uso recente por projeto/global."""
    text = (text or "").strip()[:300]
    if not text:
        return
    try:
        with _memory_db_lock:
            conn = sqlite3.connect(MEMORY_DB_FILE)
            try:
                now = datetime.datetime.now().isoformat()
                existing = conn.execute(
                    "SELECT id FROM lessons WHERE text = ? AND project_id IS ?",
                    (text, project_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE lessons SET weight = weight + 1, use_count = use_count + 1, last_used_at = ? WHERE id = ?",
                        (now, existing[0]),
                    )
                else:
                    conn.execute(
                        "INSERT INTO lessons (project_id, category, text, source, weight, created_at, last_used_at, use_count) "
                        "VALUES (?, ?, ?, ?, 1.0, ?, ?, 0)",
                        (project_id, category, text, source, now, now),
                    )
                    conn.execute(
                        "DELETE FROM lessons WHERE project_id IS ? AND id NOT IN ("
                        "  SELECT id FROM lessons WHERE project_id IS ? ORDER BY weight DESC, last_used_at DESC LIMIT 200"
                        ")",
                        (project_id, project_id),
                    )
                conn.commit()
            finally:
                conn.close()
    except sqlite3.Error:
        pass


_LESSON_RE = re.compile(r'\n?LI[ÇC][ÃA]O:\s*(.+)', re.IGNORECASE)


def _extract_lesson(comment: str) -> tuple:
    """Separa uma linha final 'LIÇÃO: ...' (se o orquestrador escrever uma) do
    resto do comentário — o que sobra é o que continua sendo mostrado ao
    usuário; a lição em si vai pro banco, não pra tela."""
    m = _LESSON_RE.search(comment or "")
    if not m:
        return comment, ""
    lesson = m.group(1).strip()
    clean = _LESSON_RE.sub('', comment).strip()
    return clean, lesson


def _load_memory_context(project_id: str, max_chars: int = 1500) -> str:
    """Lições + desempenho por agente relevantes (do projeto atual e globais),
    formatados como bloco de texto pra injetar no prompt do orquestrador —
    orçamento de caracteres PRÓPRIO, independente do corte de histórico bruto
    (history[-20:]). Cada lição efetivamente injetada tem last_used_at/
    use_count reforçados — é a estratégia de relevância (LRU simples), sem
    embeddings/vector DB. Degrada pra string vazia em qualquer erro."""
    try:
        with _memory_db_lock:
            conn = sqlite3.connect(MEMORY_DB_FILE)
            try:
                rows = conn.execute(
                    "SELECT id, text FROM lessons WHERE project_id IS ? OR project_id IS NULL "
                    "ORDER BY weight DESC, last_used_at DESC LIMIT 40",
                    (project_id,),
                ).fetchall()

                perf_rows = conn.execute(
                    "SELECT agent_tag, task_kind, outcome, COUNT(*) FROM agent_performance "
                    "WHERE project_id IS ? OR project_id IS NULL "
                    "GROUP BY agent_tag, task_kind, outcome",
                    (project_id,),
                ).fetchall()

                lines, used_ids, budget = [], [], max_chars
                for row_id, text in rows:
                    line = f"- {text}"
                    if budget - len(line) < 0:
                        break
                    lines.append(line)
                    used_ids.append(row_id)
                    budget -= len(line)

                if used_ids:
                    now = datetime.datetime.now().isoformat()
                    conn.executemany(
                        "UPDATE lessons SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
                        [(now, rid) for rid in used_ids],
                    )
                    conn.commit()

                perf_summary = {}
                for agent_tag, task_kind, outcome, count in perf_rows:
                    key = (agent_tag, task_kind)
                    slot = perf_summary.setdefault(key, {"success": 0, "error": 0})
                    slot["success" if outcome == "success" else "error"] += count

                perf_lines = []
                for (agent_tag, task_kind), counts in perf_summary.items():
                    total = counts["success"] + counts["error"]
                    if total < 2:
                        continue
                    perf_lines.append(f"- {agent_tag}: {counts['success']}/{total} sucessos em tarefas de '{task_kind}'")

                parts = []
                if lines:
                    parts.append("Lições aprendidas em sessões anteriores:\n" + "\n".join(lines))
                if perf_lines:
                    parts.append("Desempenho histórico por operário:\n" + "\n".join(perf_lines[:15]))
                return "\n\n".join(parts)
            finally:
                conn.close()
    except sqlite3.Error:
        return ""


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
                
                try:
                    result = subprocess.run(
                        ["agy", "--model", "gemini-3.1-pro-high", "-p", prompt],
                        capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
                    console.print(f"[red]⚠ Falha ao gerar o resumo ({e}).[/red]")
                    result = None

                if result is not None and result.returncode == 0:
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
# Integração: Open Design
# ─────────────────────────────────────────────────────────
# O Open Design (nexu-io/open-design) roda como um daemon+web local que delega
# o trabalho de agente pro `agy`/Antigravity já autenticado no PATH, com acesso
# de leitura E escrita à pasta real do projeto via `linkedDirs` (--add-dir).
# Serve pra gerar uma prévia visual DE VERDADE (renderizada, navegável) antes
# de aprovar qualquer mudança de interface — em vez de descrição em texto.
# Ver VALIDACAO_OPEN_DESIGN.md pro histórico completo da validação.

def _open_design_env(config: dict) -> dict:
    node_bin = config.get("open_design", {}).get("node_bin_dir")
    env = os.environ.copy()
    if node_bin:
        env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def open_design_tools_dev_status(config: dict) -> dict | None:
    """Consulta o status do daemon/web do Open Design via `tools-dev status --json`.
    None se não estiver configurado, o repo não existir, ou o comando falhar."""
    repo_path = config.get("open_design", {}).get("repo_path")
    if not repo_path or not Path(repo_path).is_dir():
        return None
    try:
        result = subprocess.run(
            ["pnpm", "exec", "tools-dev", "status", "--json"],
            cwd=repo_path, env=_open_design_env(config),
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    idx = result.stdout.find("{")
    if result.returncode != 0 or idx < 0:
        return None
    try:
        return json.loads(result.stdout[idx:])
    except json.JSONDecodeError:
        return None


def open_design_cli(config: dict, args: list, daemon_url: str, timeout: int = 30):
    """Roda `od <args>` chamando o node oficial + od.mjs diretamente (nunca o
    binário `od` pelado — colide com /usr/bin/od do coreutils). Retorna
    (ok: bool, dados: dict|None) já parseado de --json.
    Em falha, `dados` vira {"error": "<motivo>"} em vez de None — os chamadores
    que hoje fazem `(data or {}).get(...) if ok else None` continuam funcionando
    do mesmo jeito (ignoram `data` quando ok=False), mas quem quiser diagnosticar
    o motivo real de uma falha (em vez da mensagem genérica de sempre) pode ler
    `data["error"]`."""
    repo_path = config.get("open_design", {}).get("repo_path")
    node_bin = config.get("open_design", {}).get("node_bin_dir")
    if not repo_path:
        return False, {"error": "open_design.repo_path não configurado em ~/.orquestra.json"}
    node_exe = str(Path(node_bin) / "node") if node_bin else "node"
    od_mjs = str(Path(repo_path) / "apps" / "daemon" / "bin" / "od.mjs")
    cmd = [node_exe, od_mjs, *args, "--json", "--daemon-url", daemon_url]
    try:
        result = subprocess.run(cmd, env=_open_design_env(config), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"error": f"`od {' '.join(args)}` estourou o timeout de {timeout}s"}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, {"error": detail[:2000] if detail else f"`od {' '.join(args)}` saiu com código {result.returncode}"}
    try:
        return True, json.loads(result.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return False, {"error": f"resposta não-JSON de `od {' '.join(args)}`: {result.stdout.strip()[:500]}"}


def open_design_ensure_running(config: dict, wait_s: int = 40) -> dict | None:
    """Garante que o daemon/web do Open Design estejam de pé, subindo sozinho em
    background se ainda não estiverem — só daemon+web (`tools-dev start web`),
    sem o Electron/desktop, que a gente não usa (o preview é aberto no
    navegador de verdade via xdg-open, e as tarefas rodam headless pela API).
    Só é chamado quando alguém de fato pediu uma prévia, nunca no arranque do
    orquestra.py — subir isso gasta ~1GB de RAM, não vale pagar à toa em
    sessões que não mexem em nada visual. Retorna o dict de status ou None."""
    def _ready(s):
        apps = (s or {}).get("apps", {})
        d, w = apps.get("daemon", {}), apps.get("web", {})
        return d.get("state") == "running" and d.get("url") and w.get("state") == "running" and w.get("url")

    status = open_design_tools_dev_status(config)
    if _ready(status):
        return status

    repo_path = config.get("open_design", {}).get("repo_path")
    if not repo_path or not Path(repo_path).is_dir():
        console.print(f"[dim]Open Design: `open_design.repo_path` ausente ou inválido ({repo_path!r}) em ~/.orquestra.json.[/dim]")
        return None

    console.print("[dim]🎨 Open Design ainda não estava de pé — subindo agora (leva só alguns segundos)...[/dim]")
    try:
        subprocess.Popen(
            ["pnpm", "exec", "tools-dev", "start", "web"],
            cwd=repo_path, env=_open_design_env(config),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        console.print(f"[dim]Open Design: falha ao rodar `pnpm exec tools-dev start web` — {e}[/dim]")
        return None

    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(3)
        status = open_design_tools_dev_status(config)
        if _ready(status):
            return status
    console.print(f"[dim]Open Design: não ficou pronto em {wait_s}s. Último status: {status}[/dim]")
    return None


def get_open_design_cache_file() -> Path:
    return get_project_chat_dir() / "open_design.json"


def load_open_design_cache() -> dict:
    f = get_open_design_cache_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_open_design_cache(data: dict):
    get_open_design_cache_file().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def open_design_resolve_project(config: dict) -> dict | None:
    """Garante que existe um projeto do Open Design ligado (linkedDirs) à pasta
    atual. Reaproveita o cache por pasta se o projeto ainda existir no daemon;
    cria um novo caso contrário. Sobe o Open Design sozinho se ainda não
    estiver rodando (ver open_design_ensure_running). None se não conseguir.
    Retorna {"daemon_url", "web_url", "project_id"}."""
    status = open_design_ensure_running(config)
    daemon_info = (status or {}).get("apps", {}).get("daemon", {})
    if daemon_info.get("state") != "running" or not daemon_info.get("url"):
        return None
    daemon_url = daemon_info["url"]
    web_url = status.get("apps", {}).get("web", {}).get("url")

    project_dir = get_project_dir()
    cache = load_open_design_cache()
    cached_id = cache.get("project_id")
    if cache.get("project_dir") == project_dir and cached_id:
        ok, info = open_design_cli(config, ["project", "info", cached_id], daemon_url)
        if ok and info and info.get("project"):
            return {"daemon_url": daemon_url, "web_url": web_url, "project_id": cached_id}

    fd, metadata_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"linkedDirs": [project_dir]}, f, ensure_ascii=False)
        ok, data = open_design_cli(
            config,
            ["project", "create", "--name", Path(project_dir).name, "--metadata-json", metadata_path],
            daemon_url,
        )
    finally:
        os.unlink(metadata_path)

    project_id = (data or {}).get("project", {}).get("id") if ok else None
    if not project_id:
        if not ok and isinstance(data, dict) and data.get("error"):
            console.print(f"[dim]Open Design: falha ao criar o projeto — {data['error'][:400]}[/dim]")
        return None

    save_open_design_cache({"project_dir": project_dir, "project_id": project_id})
    return {"daemon_url": daemon_url, "web_url": web_url, "project_id": project_id}


def _is_noise_path(p: str) -> bool:
    """Artefatos que o operário gera de passagem (rodar testes, importar) e que
    não interessam na aprovação — nunca entram no changes_summary/commit."""
    base = p.rsplit("/", 1)[-1].lower()
    return ("__pycache__" in p or p.endswith((".pyc", ".pyo"))
            or p.endswith((".orquestra_logs.md", ".orquestra_preview.html",
                           ".orquestra_preview.png", ".pytest_cache"))
            or "/.pytest_cache/" in p
            # rascunhos de prévia que o Open Design às vezes deixa cair na pasta
            or (base.endswith(".html") and "preview" in base))


def _git_dirty_files(project_dir: str) -> set | None:
    """Lista de arquivos com mudança não commitada, ou None se a pasta não for um repo git."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=project_dir,
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    return {line[3:].strip() for line in result.stdout.splitlines()
            if line.strip() and not _is_noise_path(line[3:].strip())}


def _classify_risk(paths: list[str], config: dict) -> bool:
    """True se algum caminho bate num padrão sensível de risk_gates — usado
    pelos dois pontos de gate da TUI (plan-bar/action-bar) pra decidir se
    segue sem pausar ou pede aprovação como hoje. Só é consultado quando
    risk_gates.enabled é True (ver DEFAULT_CONFIG — nasce desligado)."""
    patterns = config.get("risk_gates", {}).get("always_confirm_patterns", [])
    if not patterns:
        return False
    for path in paths:
        low = path.lower()
        if any(fnmatch.fnmatch(low, pat.lower()) for pat in patterns):
            return True
    return False


def open_design_run_task(config: dict, instruction: str, timeout_s: int = 600,
                          section: "LiveSection | None" = None) -> tuple[bool, str]:
    """Roda uma automação de tiro único no Open Design ligada à pasta atual:
    cria (--disabled) → dispara (`run`) → aguarda terminar → lê a última
    mensagem do assistente → apaga a automação (descartável, uso único).
    Retorna (ok, texto_da_resposta_ou_erro).

    section: quando informada, a seção já deve estar ABERTA por quem chamou
    (mesmo contrato de _run_worker_headless) — aqui só emitimos heartbeats
    durante o polling (que pode levar minutos) e fechamos ao final, pra nunca
    ficar em silêncio total enquanto o Open Design trabalha."""
    def _finish(ok: bool, text: str) -> tuple:
        if section:
            section.close(ok=ok, error_detail="" if ok else text)
        return ok, text

    resolved = open_design_resolve_project(config)
    if not resolved:
        return _finish(False, ("Não consegui subir o Open Design automaticamente (repo não configurado em "
                        "~/.orquestra.json, ou demorou demais pra iniciar). Rode `open-design-dev` "
                        "manualmente em outro terminal e tente de novo."))
    daemon_url = resolved["daemon_url"]
    project_id = resolved["project_id"]
    project_dir = get_project_dir()

    # `linkedDirs` dá ao agente por trás do Open Design acesso de LEITURA E ESCRITA
    # à pasta real (não é um sandbox isolado, apesar do nome sugerir). Mesmo
    # pedindo explicitamente "só prévia" no prompt, ele pode escrever direto
    # aqui — já aconteceu em teste anterior (ver VALIDACAO_OPEN_DESIGN.md, 9.2).
    # Comparamos o `git status` antes/depois pra avisar se isso acontecer de novo.
    files_before = _git_dirty_files(project_dir)

    full_prompt = (
        f"Você está trabalhando na pasta real do projeto: {project_dir}\n"
        "Essa é a pasta de trabalho de verdade — leia os arquivos e o design system "
        "REAIS daqui (paleta, componentes, tipografia, telas existentes) antes de "
        "responder, para que a prévia reaproveite o visual já existente em vez de "
        "inventar um estilo novo do zero.\n\n"
        "REGRA: NÃO edite os arquivos existentes do projeto e NÃO crie arquivos soltos "
        "na pasta. O ÚNICO arquivo que você pode escrever na pasta do projeto é o "
        "`.orquestra_preview.html` explicitamente pedido na tarefa (se pedido). "
        "Qualquer prévia intermediária fica no seu próprio workspace de Design Files.\n\n"
        f"Tarefa pedida pelo usuário:\n{instruction}"
    )

    fd, prompt_path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(full_prompt)

    # Força o agente de execução do Open Design (padrão "claude"/Claude Code, não
    # o "antigravity"/Gemini que fica configurado como default global do Open
    # Design em .od/app-config.json). Validado na prática: numa automação real
    # anterior (ver VALIDACAO_OPEN_DESIGN.md, 9.2-9.3), o Claude Code leu de
    # verdade o design system do projeto (index.css, paleta, screenshots) e
    # replicou fielmente as cores reais — em teste comparado (16/08), a mesma
    # tarefa com o agente default (antigravity/Gemini) gerou 3 vezes seguidas
    # uma paleta genérica inventada, ignorando as cores reais do projeto mesmo
    # com o prompt pedindo explicitamente pra ler os arquivos reais.
    open_design_agent = config.get("open_design", {}).get("agent", "claude")

    automation_id = None
    try:
        ok, data = open_design_cli(
            config,
            ["automation", "create", "--name", f"orquestra-preview-{int(time.time())}",
             "--prompt-file", prompt_path, "--target", f"reuse={project_id}",
             "--schedule", "hourly:0", "--disabled", "--agent", open_design_agent],
            daemon_url,
        )
        automation_id = (data or {}).get("routine", {}).get("id") if ok else None
        if not automation_id:
            detail = (data or {}).get("error") if isinstance(data, dict) else None
            msg = "Não consegui criar a tarefa no Open Design."
            return _finish(False, f"{msg} ({detail})" if detail else msg)

        ok, run_data = open_design_cli(config, ["automation", "run", automation_id], daemon_url)
        conversation_id = (run_data or {}).get("conversationId") if ok else None
        if not conversation_id:
            detail = (run_data or {}).get("error") if isinstance(run_data, dict) else None
            msg = "Não consegui disparar a tarefa no Open Design."
            return _finish(False, f"{msg} ({detail})" if detail else msg)

        deadline = time.time() + timeout_s
        final_status = "running"
        waited_s = 0
        while time.time() < deadline:
            ok, runs_data = open_design_cli(config, ["automation", "runs", automation_id, "--limit", "1"], daemon_url)
            runs = (runs_data or {}).get("runs", []) if ok else []
            if runs:
                final_status = runs[0].get("status", "running")
                if final_status not in ("queued", "running"):
                    break
            if section:
                section.tool_call(f"gerando a prévia... ({waited_s}s)")
            time.sleep(5)
            waited_s += 5
        else:
            return _finish(False, f"Open Design não terminou em {timeout_s // 60} min (ainda '{final_status}').")

        if final_status != "succeeded":
            return _finish(False, f"A tarefa no Open Design terminou com status '{final_status}'.")

        req = urllib.request.Request(
            f"{daemon_url}/api/projects/{project_id}/conversations/{conversation_id}/messages"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            messages = json.loads(resp.read().decode("utf-8")).get("messages", [])

        assistant_msgs = [m for m in messages if m.get("role") == "assistant" and m.get("content")]
        if not assistant_msgs:
            return _finish(False, "Open Design terminou mas não devolveu nenhum texto de resposta.")

        text = assistant_msgs[-1]["content"].strip()

        if files_before is not None:
            files_after = _git_dirty_files(project_dir)
            touched = (files_after or set()) - files_before
            if touched:
                warning = (
                    "⚠️ ATENÇÃO: mesmo em modo prévia, o Open Design escreveu direto nos arquivos "
                    f"REAIS do projeto: {', '.join(sorted(touched))}. Confira com `git diff` antes de aprovar "
                    "ou commitar qualquer coisa.\n\n"
                )
                text = warning + text

        if resolved.get("web_url"):
            text += f"\n\n🔗 Prévia ao vivo: {resolved['web_url']}/projects/{project_id}"
        return _finish(True, text)
    finally:
        os.unlink(prompt_path)
        if automation_id:
            open_design_cli(config, ["automation", "delete", automation_id], daemon_url)


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

def _deepseek_balance(api_key: str) -> float | None:
    """Saldo real (paga por token, API key) via GET .../user/balance — chamado
    sob demanda (/saldo na TUI), nunca por turno. None em qualquer falha:
    telemetria opcional, nunca deve travar a UI."""
    if not api_key:
        return None
    req = urllib.request.Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    infos = data.get("balance_infos") or []
    for info in infos:
        if info.get("currency") == "USD":
            try:
                return float(info.get("total_balance", 0))
            except (TypeError, ValueError):
                return None
    return None


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
    table.add_row("/design",              "Abre o Open Design ligado à pasta atual (prévia visual ao vivo)")
    table.add_row("/modo chat",           "Respostas limpas tipo chat, sem narração técnica de tool calls")
    table.add_row("/modo tecnico",        "Volta a mostrar tudo em tempo real (padrão)")
    table.add_row("/detalhe <nível>",     "minimal | normal | full — quanto do trabalho ao vivo aparece na tela")
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
    # ── Modelos confirmados na conta via feature flags (tengu_velvet_mallet_*: True) ──
    # Primeiro = padrão sugerido quando a API não responde
    ("claude-sonnet-5",            "claude-sonnet-5              (Sonnet 5 — Padrão recomendado) ✦"),
    ("claude-opus-5",              "claude-opus-5                (Opus 5 — Máxima inteligência)"),
    ("claude-fable-5",             "claude-fable-5               (Fable 5 — Tarefas longas/difíceis)"),
    ("claude-opus-4.8",            "claude-opus-4.8              (Opus 4.8 — Alta qualidade)"),
    ("claude-opus-4.7",            "claude-opus-4.7              (Opus 4.7)"),
    ("claude-sonnet-4.6",          "claude-sonnet-4.6            (Sonnet 4.6 — Estável)"),
    ("claude-haiku-4-5-20251001",  "claude-haiku-4-5-20251001   (Haiku 4.5 — Rápido e econômico)"),
]


_DEEPSEEK_FALLBACK = [
    ("deepseek-v4-pro",              "deepseek-v4-pro               (V4 Pro — Mais capaz)"),
    ("deepseek-v4-flash",            "deepseek-v4-flash             (V4 Flash — Rápido e barato)"),
    ("deepseek-v4-flash-vision-exp", "deepseek-v4-flash-vision-exp  (V4 Flash Vision — Experimental)"),
]

# Codex CLI não tem um endpoint de listagem de modelos confiável hoje (ver
# VALIDACAO_OPEN_DESIGN/memória do projeto: "nenhum id de modelo verificado
# ainda") — "" como primeira opção é o padrão seguro (não passa -m, deixa o
# Codex usar o que já está configurado pra conta).
_COPILOT_FALLBACK = [
    ("claude-haiku-4.5",     "claude-haiku-4.5       (Haiku 4.5 — Padrão atual)"),
    ("claude-sonnet-4.6",    "claude-sonnet-4.6      (Sonnet 4.6 — Pesquisa)"),
    ("claude-sonnet-5",      "claude-sonnet-5        (Sonnet 5 — Poder Máximo)"),
    ("claude-opus-5",        "claude-opus-5          (Opus 5 — Ultra Qualidade)"),
    ("gpt-5.6-terra",        "gpt-5.6-terra          (Terra — GPT padrão)"),
    ("gpt-5.6-sol",          "gpt-5.6-sol            (Sol — GPT Alta cap.)"),
    ("gpt-5.5",              "gpt-5.5                (GPT-5.5 — Geral)"),
    ("gpt-5.4-mini",         "gpt-5.4-mini           (Mini — Rápido)"),
]

_CODEX_FALLBACK = [
    ("",              "Padrão (usa gpt-5.6-terra configurado)"),
    ("gpt-5.6-terra", "gpt-5.6-terra    (Terra — Padrão atual da conta)"),
    ("gpt-5.6-sol",   "gpt-5.6-sol      (Sol — Alta capacidade)"),
    ("gpt-5.6-luna",  "gpt-5.6-luna     (Luna — Balanceado)"),
    ("gpt-5.5",       "gpt-5.5          (GPT-5.5 — Uso geral)"),
    ("gpt-5.4",       "gpt-5.4          (GPT-5.4 — Uso geral)"),
    ("gpt-5.4-mini",  "gpt-5.4-mini     (Mini — Rápido)"),
    ("gpt-5-mini",    "gpt-5-mini        (5 Mini — Rápido)"),
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

def _fetch_codex_models() -> list[tuple[str, str]]:
    """Sem endpoint de listagem confiável no Codex CLI — lista fixa, com
    'padrão' como primeira opção segura (ver _CODEX_FALLBACK)."""
    return _CODEX_FALLBACK

# Motor OpenCode (CLI já logada no OpenRouter — sem API key no cofre). Foco em
# LLMs chinesas, que é o que o usuário pediu — o catálogo completo do
# OpenRouter via opencode tem 350+ modelos, filtrar por provedor evita afogar
# o picker.
_OPENCODE_PROVIDERS = ("deepseek", "qwen", "z-ai", "moonshotai", "minimax")

_OPENCODE_FALLBACK = [
    ("openrouter/deepseek/deepseek-v4-flash", "deepseek-v4-flash       (DeepSeek — rápido)"),
    ("openrouter/deepseek/deepseek-v4-pro",   "deepseek-v4-pro         (DeepSeek — mais capaz)"),
    ("openrouter/qwen/qwen3-max",             "qwen3-max               (Qwen — mais capaz)"),
    ("openrouter/qwen/qwen3-coder-plus",      "qwen3-coder-plus        (Qwen — foco em código)"),
    ("openrouter/z-ai/glm-4.6",               "glm-4.6                 (Z-AI/GLM)"),
    ("openrouter/moonshotai/kimi-k2",         "kimi-k2                 (Moonshot AI)"),
    ("openrouter/minimax/minimax-m3",         "minimax-m3              (MiniMax)"),
]

def _fetch_opencode_models() -> list[tuple[str, str]]:
    """Busca os modelos disponíveis via OpenCode/OpenRouter em tempo real,
    filtrados aos provedores de LLM chinesa em _OPENCODE_PROVIDERS."""
    try:
        result = subprocess.run(["opencode", "models"], capture_output=True, text=True, timeout=15)
        models = []
        for line in result.stdout.splitlines():
            model_id = line.strip()
            if not model_id.startswith("openrouter/"):
                continue
            parts = model_id.split("/")
            if len(parts) < 3 or parts[1] not in _OPENCODE_PROVIDERS:
                continue
            models.append((model_id, model_id.removeprefix("openrouter/")))
        return models if models else _OPENCODE_FALLBACK
    except Exception:
        return _OPENCODE_FALLBACK

_OLLAMA_FALLBACK = [
    ("gpt-oss:120b", "GPT-OSS 120b (Ollama Cloud)"),
    ("kimi-k2.7-code", "Kimi 2.7 Code (Ollama Cloud)"),
]

def _fetch_ollama_models() -> list[tuple[str, str]]:
    """Tenta buscar a lista de modelos do Ollama Cloud direto do Kimi CLI."""
    try:
        import json, subprocess
        res = subprocess.run(["kimi", "provider", "list", "--json"], capture_output=True, text=True, timeout=5)
        data = json.loads(res.stdout)
        models = []
        for k, v in data.get("models", {}).items():
            if v.get("provider") == "ollama-cloud":
                models.append((v.get("model", ""), v.get("displayName", "")))
        if models:
            return models
    except Exception:
        pass
    return _OLLAMA_FALLBACK


def _fetch_copilot_models() -> list[tuple[str, str]]:
    """Busca modelos disponíveis no GitHub Copilot via API em tempo real.
    Usa o token do 'gh auth token'. Fallback para lista fixa se falhar."""
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
        token = result.stdout.strip()
        if not token:
            return _COPILOT_FALLBACK
        req = urllib.request.Request(
            "https://api.githubcopilot.com/models",
            headers={
                "Authorization": f"Bearer {token}",
                "Copilot-Integration-Id": "vscode-chat",
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            raw = data.get("data", data if isinstance(data, list) else [])
            # Filter to only useful chat/code models, exclude embeddings, internal agents, exec-agents
            skip_prefixes = ("text-embedding", "copilot-search", "exec-agent", "trajectory", "mai-code-1-flash-picker")
            models = []
            for m in raw:
                mid = m.get("id", "") if isinstance(m, dict) else str(m)
                if mid and not any(mid.startswith(p) for p in skip_prefixes):
                    models.append((mid, f"{mid}"))
            if models:
                # Put claude-haiku-4.5 first (current default)
                models.sort(key=lambda x: (0 if x[0] == "claude-haiku-4.5" else 1, x[0]))
                return [(mid, f"{mid:<45}") for mid, _ in models]
    except Exception:
        pass
    return _COPILOT_FALLBACK

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

# Motores para os OPERÁRIOS (harness de agente completo — leem/escrevem/rodam).
_FAMILIA_CHOICES = [
    ("claude",   "💞 Claude Code            (claude) — Anthropic"),
    ("codex",    "🤖 Codex                  (codex)  — OpenAI"),
    ("deepseek", "💙 Claude-DeepSeek        (claude) — DeepSeek V4"),
    ("gemini",   "💚 Antigravity            (agy)    — Google"),
    ("copilot",  "🐕 Copilot                (copilot)— GitHub"),
    ("opencode", "🌐 OpenCode               (opencode) — OpenRouter"),
]

# Cérebros para o ORQUESTRADOR (chat puro, ZERO ferramentas — só planeja e delega).
_BRAIN_CHOICES = [
    ("brain-deepseek", "🧠 DeepSeek (chat direto)   — rápido e barato, sem ferramentas"),
    ("brain-ollama",   "🧠 Ollama Cloud (Kimi/GPT)  — chat direto, sem ferramentas"),
]

_SLOT_LABELS = {
    "orquestrador": "Orquestrador (Estrategista)",
    "operador": "Operador (Executor)",
    "revisor": "Revisor (sob demanda)",
    "cao_guarda": "Cão de Guarda (automático)",
}


def _apply_familia(target: dict, familia: str, modelo: str):
    """Aplica a família/modelo escolhidos num role/worker.
    Famílias 'brain-*' são só para o Orquestrador (chat puro via
    orchestrator_brain.py). As demais são motores de operário."""
    if familia == "brain-deepseek":
        target["binary"] = "brain"
        target["provider"] = "deepseek"
        target["model"] = modelo
        target.pop("env_file", None)
        return
    if familia == "brain-ollama":
        target["binary"] = "brain"
        target["provider"] = "ollama-cloud"
        target["model"] = modelo
        target.pop("env_file", None)
        return
    target.pop("provider", None)
    if familia == "gemini":
        target["binary"] = "agy"
        target["model"] = modelo
        target.pop("env_file", None)
    elif familia == "claude":
        target["binary"] = "claude"
        target["model"] = modelo
        target.pop("env_file", None)
    elif familia == "deepseek":
        target["binary"] = "claude"
        target["model"] = modelo
        target["env_file"] = str(DEEPSEEK_ENV)
    elif familia == "copilot":
        target["binary"] = "copilot"
        target["model"] = modelo
        target.pop("env_file", None)
    elif familia == "opencode":
        target["binary"] = "opencode"
        target["model"] = modelo
        target.pop("env_file", None)
    else:  # codex
        target["binary"] = "codex"
        target["model"] = modelo  # pode ser "" — _build_worker_cmd só passa -m se não for vazio
        target.pop("env_file", None)


def interactive_agent_selection(config: dict):
    """Menu para trocar o modelo/provedor de QUALQUER agente — Orquestrador
    ou qualquer Operário (Claude, Antigravity, DeepSeek, Codex). Todo agente
    aceita qualquer uma das 4 famílias (Gemini/Claude/DeepSeek/OpenAI), pra
    dar redundância real: se uma assinatura específica falhar, dá pra trocar
    qualquer operário pra outro provedor sem editar JSON na mão.

    Navegação em 3 passos com "voltar" de verdade: cancelar em qualquer passo
    (família ou modelo) volta pra tela inicial (escolha de agente) em vez de
    fechar o menu inteiro — só sai mesmo cancelando NA tela inicial, ou depois
    de confirmar uma troca."""

    def _modelo_label(d: dict) -> str:
        m = d.get("model") or d.get("binary", "?")
        return m[:30] if m else "padrão"

    step = "slot"
    slot = familia = None

    while True:
        if step == "slot":
            workers = config.get("workers", {})
            orch = config.get("roles", {}).get("orquestrador", {})
            slot_choices = [
                ("orquestrador", f"🎼  Orquestrador (Estrategista)   [{_modelo_label(orch)}]"),
                ("operador",     f"🔨  Operador (Executor)           [{_modelo_label(workers.get('operador', {}))}]"),
                ("revisor",      f"🔍  Revisor (sob demanda)         [{_modelo_label(workers.get('revisor', {}))}]"),
                ("cao_guarda",   f"🐕  Cão de Guarda (automático)    [{_modelo_label(workers.get('cao_guarda', {}))}]"),
            ]
            slot = radiolist_dialog(
                title="\n  🎼  Orquestra — Trocar Inteligência",
                text="Passo 1 de 3  ──  O que você quer reconfigurar?",
                values=slot_choices,
                style=_DIALOG_STYLE,
                ok_text="Próximo →",
                cancel_text="X Sair",
            ).run()
            if slot is None:
                console.print("[dim]✓ Nenhuma alteração feita.[/dim]")
                return
            step = "familia"

        elif step == "familia":
            slot_label = _SLOT_LABELS.get(slot, slot)
            # O Orquestrador só aceita "cérebros" de chat puro (zero ferramentas).
            choices = _BRAIN_CHOICES if slot == "orquestrador" else _FAMILIA_CHOICES
            familia = radiolist_dialog(
                title="\n  🎼  Orquestra — Trocar Inteligência",
                text=f"Passo 2 de 3  ──  Qual provedor para o {slot_label}?",
                values=choices,
                style=_DIALOG_STYLE,
                ok_text="Próximo →",
                cancel_text="← Voltar",
            ).run()
            if familia is None:
                step = "slot"
                continue
            step = "modelo"

        elif step == "modelo":
            console.print("[dim]⏳ Buscando modelos...[/dim]", end="")
            if familia in ("gemini",):
                model_choices = _fetch_agy_models()
            elif familia == "claude":
                model_choices = _fetch_claude_models()
            elif familia in ("deepseek", "brain-deepseek"):
                model_choices = _fetch_deepseek_models()
            elif familia == "copilot":
                model_choices = _fetch_copilot_models()
            elif familia == "brain-ollama":
                model_choices = _fetch_ollama_models()
            elif familia == "opencode":
                model_choices = _fetch_opencode_models()
            else:
                model_choices = _fetch_codex_models()
            console.print("\r                                        \r", end="")

            if not model_choices:
                console.print("[bold red]✗ Não foi possível carregar os modelos desse provedor. Voltando...[/bold red]")
                step = "slot"
                continue

            slot_label = _SLOT_LABELS.get(slot, slot)
            modelo = radiolist_dialog(
                title="\n  🎼  Orquestra — Trocar Inteligência",
                text=f"Passo 3 de 3  ──  Qual modelo para o {slot_label}?",
                values=model_choices,
                style=_DIALOG_STYLE,
                ok_text="✔ Confirmar",
                cancel_text="← Voltar",
            ).run()
            if modelo is None:
                step = "slot"
                continue

            # ─── Salva ───
            if slot == "orquestrador":
                target = config.setdefault("roles", {}).setdefault("orquestrador", {})
            else:
                target = config.setdefault("workers", {}).setdefault(slot, {})
            _apply_familia(target, familia, modelo)

            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            console.print(
                f"\n[bold green]✓ {slot_label} configurado![/bold green] "
                f"Modelo: [bold]{modelo or '(padrão do provedor)'}[/bold] via [dim]{familia.capitalize()}[/dim]\n"
            )
            return


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

    if binary == "brain":
        # O Orquestrador não tem sessão interativa (é chat puro). Cai no Operador.
        console.print("[dim]O Orquestrador é só chat — abrindo o Operador interativamente.[/dim]")
        role = config.get("workers", {}).get("operador", {"binary": "claude"})
        role_name = "operador"
        binary = role.get("binary", "claude")

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
# Execução headless de operários — streaming em tempo real
# ─────────────────────────────────────────────────────────
# Roda claude/agy em modo --print + --output-format stream-json em vez de
# abrir um painel tmux interativo e ficar catando pixels da tela. O processo
# emite um evento JSON por linha (texto, chamada de ferramenta, resultado
# final) e o próprio fim do processo é o sinal de conclusão — sem senha
# mágica, sem heurística de idle, sem race condition.

_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _preview(value, limit=160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = _ANSI_RE.sub('', text).replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


_IMG_MD_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
_IMG_PATH_RE = re.compile(r'/\S+\.(?:png|jpe?g|gif|webp|svg)\b', re.IGNORECASE)


def _auto_open_images(text: str):
    """Terminal/tmux não renderiza markdown nem torna caminhos clicáveis — uma resposta
    com ![](/caminho/imagem.jpg) fica como texto morto para o usuário. Abre a imagem de
    verdade no visualizador padrão do sistema em vez de só imprimir o link."""
    if not text or not shutil.which("xdg-open"):
        return
    candidates = set(_IMG_MD_RE.findall(text)) | set(_IMG_PATH_RE.findall(text))
    opened = []
    for raw in candidates:
        cleaned = raw.strip().strip('<>').rstrip('.,;)')
        path = Path(os.path.expanduser(cleaned))
        if path.is_absolute() and path.is_file():
            try:
                subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                opened.append(path.name)
            except OSError:
                pass
    if opened:
        console.print(f"[dim]🖼  Abrindo imagem(ns) no visualizador: {', '.join(opened)}[/dim]")


def _detail_level(config: dict) -> str:
    """minimal | normal | full — quanto do trabalho ao vivo dos operários aparece.
    Lido de config['ui']['detail'] (/detalhe), default 'normal'."""
    lvl = (config or {}).get("ui", {}).get("detail", "normal")
    return lvl if lvl in ("minimal", "normal", "full") else "normal"


def _is_chat_mode(config: dict) -> bool:
    """True = bolhas limpas + Orquestrador comenta o resultado. False (/modo tecnico)
    = narração crua de tool calls. Lido de config['ui']['mode'], default 'chat'."""
    return (config or {}).get("ui", {}).get("mode", "chat") != "tecnico"


# ─────────────────────────────────────────────────────────
# Modo chat — renderização limpa (bolhas), sem narração técnica
# ─────────────────────────────────────────────────────────

_CODE_BLOCK_RE = re.compile(r'```.*?```', re.DOTALL)
_ARTIFACT_RE = re.compile(r'<artifact[^>]*>.*?</artifact>', re.DOTALL | re.IGNORECASE)
_URL_RE = re.compile(r'https?://\S+')


def _chat_clean_text(text: str) -> str:
    """Colapsa blocos grandes de código/artefato pro modo chat (ex: o HTML inteiro
    de uma prévia do Open Design) numa linha curta. Só filtra o que é EXIBIDO —
    o histórico salvo via save_chat_message continua completo, sem essa filtragem."""
    text = _ARTIFACT_RE.sub("📄 (prévia/artefato gerado — veja o link acima)", text)

    def _shrink(m):
        n = m.group(0).count("\n")
        return f"💻 (bloco de código omitido, {n} linhas — use /modo tecnico pra ver tudo)"

    text = _CODE_BLOCK_RE.sub(_shrink, text)
    return text.strip()


def _print_chat_bubble(label: str, icon: str, color: str, text: str):
    """Mensagem 'de chat' sem caixa/borda — cabeçalho colorido + texto liso
    indentado por baixo, no mesmo estilo visual do Claude Code/OpenCode
    (nunca painéis com moldura pra cada mensagem, que fica mais 'app de
    chat de celular' do que terminal de verdade)."""
    clean = _chat_clean_text(text)
    if not clean:
        return
    with _console_lock:
        console.print(f"[{color}]⏺[/{color}] [bold {color}]{icon} {label}[/bold {color}]")
        console.print(Padding(Markdown(clean), (0, 0, 0, 2)))
        console.print()


class LiveSection:
    """Uma seção de trabalho de um operário, impressa de forma incremental
    (append-only) no MESMO terminal — nunca abre pane/janela nova. Simula o
    efeito de 'painel que abre e fecha' com prints estruturados em vez de
    redesenho in-place (rich.live.Live nunca rodou concorrente com o prompt
    neste projeto; console.print de thread de fundo já roda em produção sem
    problema — ver plano da reforma). Cada seção é prefixada por ícone/label
    e protegida por _console_lock, então várias seções em paralelo entrelaçam
    de forma legível em vez de quebrar linhas no meio."""

    def __init__(self, label: str, icon: str, color: str):
        self.label = label
        self.icon = icon
        self.color = color
        self._start = None

    def open(self):
        self._start = time.time()
        with _console_lock:
            console.print(f"[{self.color}]⏳[/{self.color}] [bold {self.color}]{self.icon} {self.label}[/bold {self.color}] (trabalhando...)")
        _append_log(f"\n## {self.icon} {self.label}\n\n")

    def end_line(self):
        pass

    def write(self, text: str, end: str = "\n", prefix: bool = True):
        if not text:
            return
        _append_log(text + end)

    def tool_call(self, text: str):
        with _console_lock:
            console.print(f"  [dim]⎿ {text}[/dim]")
        _append_log(f"\n> ⎿ {text}\n\n")

    def close(self, ok: bool, error_detail: str = "", changes_summary: str = ""):
        elapsed = time.time() - self._start if self._start else 0.0
        with _console_lock:
            if ok:
                console.print(f"  [dim green]⎿ ✓ concluído em {elapsed:.0f}s[/dim green]")
            else:
                console.print(f"  [dim red]⎿ ✗ erro ({elapsed:.0f}s)[/dim red]")
                if error_detail:
                    console.print(f"    [dim]{error_detail[-1500:]}[/dim]")
            if changes_summary:
                console.print(f"  [dim]⎿ 📝 {changes_summary}[/dim]")
        _append_log(f"\n\n**✓ Concluído em {elapsed:.0f}s**\n---\n")



# ── Orquestra Bridge Server ────────────────────────────────────────────────
# Micro servidor HTTP que roda em background dentro do próprio Orquestra.
# Resolve o problema de extensões do Cursor não carregarem por falta de
# assinatura. Agora o Orquestra é o servidor e chama o CLI do Cursor.

_BRIDGE_PORT = 49152
_bridge_server_thread = None
# Token de sessão: só quem está no mesmo processo (via _auto_open_in_cursor) sabe.
# Impede que qualquer outro processo local mande o bridge abrir/diffar arquivos.
_BRIDGE_TOKEN = None


def _bridge_token() -> str:
    global _BRIDGE_TOKEN
    if _BRIDGE_TOKEN is None:
        _BRIDGE_TOKEN = os.urandom(16).hex()
    return _BRIDGE_TOKEN


def _bridge_path_ok(fp: str) -> bool:
    """Aceita só caminhos dentro da pasta do projeto, do HOME ou do tempdir —
    nunca um path arbitrário do sistema."""
    try:
        real = os.path.realpath(fp)
    except (OSError, ValueError):
        return False
    roots = (os.path.realpath(get_project_dir()),
             os.path.realpath(os.path.expanduser("~")),
             os.path.realpath(tempfile.gettempdir()))
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def _start_bridge_server():
    """Inicia o servidor bridge HTTP em uma thread daemon."""
    global _bridge_server_thread
    if _bridge_server_thread and _bridge_server_thread.is_alive():
        return

    import http.server
    import socketserver

    class BridgeHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # Silencia logs HTTP

        def do_OPTIONS(self):
            self.send_response(200)
            self.end_headers()

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'Orquestra Bridge Active')

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length)
            try:
                if self.headers.get('X-Orquestra-Token') != _bridge_token():
                    self.send_response(403)
                    self.end_headers()
                    return
                payload = json.loads(body)
                action = payload.get('action')
                cursor_bin = shutil.which('cursor')
                if not cursor_bin:
                    self.send_response(503)
                    self.end_headers()
                    return

                if action == 'diff':
                    f1 = payload.get('file1', '')
                    f2 = payload.get('file2', '')
                    if not (_bridge_path_ok(f1) and _bridge_path_ok(f2)):
                        self.send_response(400)
                        self.end_headers()
                        return
                    # Grava um arquivo .diff legível e abre no Cursor
                    result = subprocess.run(
                        ['diff', '-u', f1, f2],
                        capture_output=True, text=True
                    )
                    diff_path = Path(tempfile.gettempdir()) / 'orquestra_review.diff'
                    diff_path.write_text(result.stdout or '(sem diferenças)', encoding='utf-8')
                    subprocess.Popen(
                        [cursor_bin, str(diff_path)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                elif action == 'open':
                    fp = payload.get('file', '')
                    line = payload.get('line', 0)
                    if not _bridge_path_ok(fp):
                        self.send_response(400)
                        self.end_headers()
                        return
                    # Atualiza estado (Retrovisor)
                    try:
                        state_file = Path.home() / '.orquestra_cursor_state.json'
                        state_file.write_text(json.dumps({'file': fp, 'line': line}), encoding='utf-8')
                    except Exception:
                        pass
                    if line and line > 0:
                        subprocess.Popen(
                            [cursor_bin, '-g', f'{fp}:{line}'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )
                    else:
                        subprocess.Popen(
                            [cursor_bin, fp],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )

                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'OK')
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode())

    def _run():
        try:
            with socketserver.TCPServer(('127.0.0.1', _BRIDGE_PORT), BridgeHandler) as httpd:
                httpd.serve_forever()
        except OSError:
            pass  # Porta já em uso (outra instância rodando)

    _bridge_server_thread = threading.Thread(target=_run, daemon=True, name='orquestra-bridge')
    _bridge_server_thread.start()


def _auto_open_in_cursor(file_paths: list[str], cwd=None):
    """Abre arquivos no Cursor via Bridge HTTP local (porta 49152), com fallback para CLI."""
    if not file_paths: return

    payload = None
    if file_paths[0] == "-d" and len(file_paths) >= 3:
        payload = {"action": "diff", "file1": file_paths[1], "file2": file_paths[2]}
    else:
        fp = file_paths[0]
        if cwd and not os.path.isabs(fp) and fp != ".":
            fp = os.path.join(cwd, fp)
        if fp == ".":
            fp = cwd or os.getcwd()
        payload = {"action": "open", "file": fp}

    if payload:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{_BRIDGE_PORT}",
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'X-Orquestra-Token': _bridge_token()},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass

    # Fallback: CLI direta
    if not shutil.which("cursor"):
        return
    try:
        subprocess.Popen(["cursor"] + file_paths, cwd=cwd,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _auto_open_in_cursor_at(filepath: str, line: int = 1):
    """Abre um arquivo no Cursor e navega direto para a linha especificada (GPS)."""
    payload = {"action": "open", "file": filepath, "line": line}
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{_BRIDGE_PORT}",
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                return
    except Exception:
        pass
    # Fallback CLI
    cursor_bin = shutil.which('cursor')
    if cursor_bin:
        try:
            subprocess.Popen(
                [cursor_bin, '-g', f'{filepath}:{line}'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError:
            pass


def _get_cursor_context() -> dict:
    """Retrovisor: lê o arquivo e linha que o usuário está olhando no Cursor agora.

    Retorna dict com 'file' e 'line', ou dict vazio se não souber.
    """
    try:
        state_file = Path.home() / '.orquestra_cursor_state.json'
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding='utf-8'))
            return data
    except Exception:
        pass
    return {}


def _auto_open_links(text: str):
    """Modo chat: abre a prévia (última URL do texto — open_design_run_task sempre
    acrescenta '🔗 Prévia ao vivo: <url>' no fim) direto no navegador, sem o usuário
    precisar clicar em nada no terminal."""
    if not text or not shutil.which("xdg-open"):
        return
    urls = _URL_RE.findall(text)
    if not urls:
        return
    url = urls[-1].rstrip('.,;)')
    try:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        console.print(f"[dim]🔗 Abrindo prévia no navegador: {url}[/dim]")
    except OSError:
        pass


def _narrate_claude_line(line: str, section: "LiveSection", state: dict, detail: str = "normal"):
    """Processa uma linha de stream-json do `claude` e narra ao vivo na LiveSection.
    Preenche state['final_text']/state['is_error'] quando o evento 'result' chega.
    minimal: só o texto de fala. normal: + anúncio de 1 linha por tool call.
    full: + preview do resultado de cada tool call (comportamento técnico de sempre)."""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return

    etype = d.get("type")

    if etype == "assistant":
        for block in d.get("message", {}).get("content", []):
            btype = block.get("type")
            if btype == "text" and block.get("text", "").strip():
                text = block["text"].strip()
                if detail != "full":
                    # Colapsa blocos de código grandes (mesma limpeza que já
                    # existia pras bolhas de chat) — sem isso, uma análise que
                    # cita trechos de código real vira uma parede de código
                    # cru no meio da conversa, em vez de ficar legível.
                    text = _chat_clean_text(text)
                if text:
                    section.write(text)
            elif btype == "tool_use" and detail in ("normal", "full"):
                tool_name = block.get("name", "?")
                section.tool_call(f"{tool_name}({_preview(block.get('input', {}))})")

    elif etype == "user":
        if detail == "full":
            for block in d.get("message", {}).get("content", []):
                if block.get("type") == "tool_result":
                    section.write(f"↳ resultado: {_preview(block.get('content', ''))}", prefix=False)

    elif etype == "result":
        state["final_text"] = d.get("result", "") or ""
        state["is_error"] = bool(d.get("is_error"))
        # Único motor que devolve custo em dólar de verdade (confirmado via
        # `claude -p ... --output-format stream-json`, campo "total_cost_usd"
        # no evento "result") — Agy/Codex só dão tokens, sem preço.
        state["cost_usd"] = d.get("total_cost_usd") or 0.0
        usage = d.get("usage") or {}
        state["tokens_in"] = usage.get("input_tokens", 0) or 0
        state["tokens_out"] = usage.get("output_tokens", 0) or 0
        state["duration_ms"] = d.get("duration_ms") or 0


def _narrate_copilot_line(line: str, section: "LiveSection", state: dict, detail: str = "normal"):
    """Processa uma linha de saída do `gh copilot -p`.
    O Copilot retorna texto puro (não JSON), linha a linha.
    Acumula o texto e atualiza a LiveSection ao vivo."""
    # Tenta JSON primeiro (casos especiais do protocolo interno)
    try:
        data = json.loads(line)
        if "type" in data:
            if data["type"] == "assistant.message_delta":
                delta = data.get("data", {}).get("delta", "")
                if delta:
                    state["final_text"] += delta
                    section.write(delta, end="")
            elif data["type"] == "error":
                state["is_error"] = True
        return  # Era JSON válido, processado acima
    except json.JSONDecodeError:
        pass

    # Texto puro — formato real que o `gh copilot -p` envia
    # Ignora linhas de metadados como "AI Credits", "Tokens", "Resume", "Changes"
    skip_prefixes = ("AI Credits", "Tokens", "Resume", "Changes ", "Log:", "\u25cf", "│", "└")
    if line.startswith(skip_prefixes):
        if detail == "full":
            section.tool_call(line[:80])
        return

    # É conteúdo de resposta real
    state["final_text"] += line + "\n"
    if detail in ("normal", "full"):
        section.write(line)


def _narrate_agy_line(line: str, section: "LiveSection", state: dict, detail: str = "normal"):
    """Processa uma linha de stream-json do `agy` e narra ao vivo na LiveSection
    (streaming token-a-token real, mostrado em qualquer nível de detalhe —
    era descartado em modo chat antes desta reforma)."""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return

    event = d.get("event")

    if event == "step_update":
        upd = d.get("step_update", {})
        step_type = upd.get("step_type")
        step_idx = upd.get("step_index")
        delta = upd.get("text_delta")

        if step_type == "agent_response" and delta:
            if detail == "full":
                section.write(delta, end="")
            elif not state.get("_responding_announced"):
                # Em minimal/normal não mostramos o delta cru — o texto vem
                # token a token, sem contexto suficiente pra saber se está no
                # meio de um bloco de código (diferente do claude, que manda
                # blocos de texto já completos). Só avisa que está
                # respondendo; a versão limpa/colapsada aparece no 'result'.
                section.tool_call("respondendo...")
                state["_responding_announced"] = True
            state["_last_stream_idx"] = step_idx
        elif step_type not in ("agent_response", "user_input", "checkpoint") and upd.get("state") == "ACTIVE":
            key = f"_announced_{step_idx}"
            if not state.get(key):
                if detail in ("normal", "full"):
                    section.tool_call(f"{step_type}...")
                state[key] = True

    elif event == "result":
        res = d.get("result", {})
        state["final_text"] = res.get("response", "") or ""
        state["is_error"] = res.get("status") != "SUCCESS"
        # Confirmado via `agy -p ... --output-format stream-json`: "usage" traz
        # tokens (input_tokens/output_tokens), mas sem custo em dólar — login
        # por assinatura não expõe preço, igual ao Codex.
        usage = res.get("usage") or {}
        state["tokens_in"] = usage.get("input_tokens", 0) or 0
        state["tokens_out"] = usage.get("output_tokens", 0) or 0
        duration_s = res.get("duration_seconds")
        state["duration_ms"] = int(duration_s * 1000) if duration_s else 0
        if detail == "full":
            section.end_line()
        elif state["final_text"] and not state["is_error"]:
            section.write(_chat_clean_text(state["final_text"]))


def _codex_clean_error_msg(raw) -> str:
    """O `message` de erro do Codex às vezes vem como uma string JSON aninhada
    (ex.: '{"type":"error","status":400,"error":{"message":"..."}}') em vez de
    texto puro. Desembrulha isso pra chegar na mensagem legível de verdade."""
    if not raw:
        return ""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
        raw = parsed
    if isinstance(raw, dict):
        inner = raw.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
        if raw.get("message"):
            return str(raw["message"])
    return str(raw)


def _narrate_codex_line(line: str, section: "LiveSection", state: dict, detail: str = "normal"):
    """Parser (best-effort) do --json do Codex CLI — schema próprio (thread.started/
    turn.started/item.completed/turn.completed), diferente do stream-json do Claude
    e do formato do Agy. Tool calls só narram em normal/full; o texto final de
    sucesso não depende disso — vem do arquivo passado via -o/--output-last-message,
    lido depois em _run_worker_headless (mais confiável que garimpar o item certo
    aqui). Em caso de erro, porém, o Codex nunca escreve esse arquivo — a única
    fonte da causa real é este stream (o erro aparece no stdout como JSON, não no
    stderr), então aqui SIM capturamos a mensagem de erro em state["final_text"]
    pra ela chegar no resumo salvo no histórico / repassado ao orquestrador. Erros
    são sempre narrados ao vivo, em qualquer nível de detalhe — nunca escondidos."""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return

    ev_type = d.get("type", "")
    if ev_type == "item.completed":
        item = d.get("item") or {}
        item_type = item.get("type", "")
        if item_type == "agent_message":
            state["final_text"] = item.get("text", "") or state["final_text"]
        elif item_type == "error":
            msg = _codex_clean_error_msg(item.get("message"))
            if msg:
                state["final_text"] = f"[Codex] {msg}"
            section.tool_call(f"⚠ {msg or 'erro'}")
        elif detail in ("normal", "full"):
            section.tool_call(f"{item_type or 'executando'}...")
    elif ev_type == "error":
        state["is_error"] = True
        msg = _codex_clean_error_msg(d.get("message"))
        if msg:
            state["final_text"] = f"[Codex] {msg}"
        section.tool_call(f"⚠ {msg or 'erro'}")
    elif ev_type == "turn.failed":
        state["is_error"] = True
        msg = _codex_clean_error_msg((d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else d.get("error"))
        if msg and not state.get("final_text"):
            state["final_text"] = f"[Codex] {msg}"
        section.tool_call("turno falhou.")
    elif ev_type == "turn.completed":
        # Confirmado via `codex exec ... --json`: "usage" traz tokens
        # (input_tokens/cached_input_tokens/output_tokens), sem custo em
        # dólar — ChatGPT-auth não expõe preço, igual ao Agy.
        usage = d.get("usage") or {}
        state["tokens_in"] = usage.get("input_tokens", 0) or 0
        state["tokens_out"] = usage.get("output_tokens", 0) or 0


def _narrate_opencode_line(line: str, section: "LiveSection", state: dict, detail: str = "normal"):
    """Parser do --format json do opencode: eventos step_start/tool_use/text/
    step_finish/error. Cada evento 'text' já vem como bloco de texto completo
    (testado com respostas de 1 e vários passos), então acumula com += em vez
    de sobrescrever — cobre tanto resposta única quanto várias etapas."""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return

    ev_type = d.get("type", "")
    if ev_type == "text":
        text = (d.get("part") or {}).get("text", "")
        if text:
            state["final_text"] += text
    elif ev_type == "tool_use":
        tool = (d.get("part") or {}).get("tool", "")
        if detail in ("normal", "full"):
            section.tool_call(f"{tool or 'executando'}...")
    elif ev_type == "error":
        state["is_error"] = True
        msg = (d.get("error") or {}).get("message", "")
        if msg:
            state["final_text"] = f"[OpenCode] {msg}"
        section.tool_call(f"⚠ {msg or 'erro'}")


_BRAIN_DIR = Path(__file__).resolve().parent


def _build_orchestrator_cmd(role: dict, prompt: str) -> tuple:
    """Monta o comando pro PRÓPRIO orquestrador (decisão do turno e
    _orchestrator_react). Retorna (cmd, binary, output_file).

    Dois caminhos, os dois SEM permissão de escrita (o estrategista lê/conversa/
    planeja, mas NÃO implementa — isso é sempre delegado):

    - binary="brain"  → orchestrator_brain.py: chat completions puro (DeepSeek,
      Ollama Cloud, Anthropic/OpenAI/Gemini via API key). Zero ferramentas.
    - binary="agy"/"claude"/"codex" → a CLI JÁ LOGADA no terminal, em modo
      PLANO / read-only (--mode plan / --permission-mode plan / --sandbox
      read-only). Pode ler pra dar contexto melhor, mas não escreve arquivo
      nem roda comando que altera nada. É assim que se conversa usando a conta
      logada (agy/claude/codex) sem precisar de API key separada."""
    binary = role.get("binary", "brain")
    model = role.get("model")

    if binary in ("brain", "ollama-cloud"):
        provider = role.get("provider") or ("ollama-cloud" if binary == "ollama-cloud" else "deepseek")
        cmd = ["python3", str(_BRAIN_DIR / "orchestrator_brain.py"), "-p", prompt, "--provider", provider]
        if model:
            cmd.extend(["--model", model])
        return cmd, "brain", None

    if binary == "agy":
        cmd = ["agy", "-p", prompt, "--output-format", "stream-json",
               "--mode", "plan", "--add-dir", get_project_dir()]
        if model:
            cmd.extend(["--model", model])
        return cmd, "agy", None

    if binary == "codex":
        fd, output_file = tempfile.mkstemp(prefix="orquestra_orch_", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", prompt, "--json", "--sandbox", "read-only",
               "--skip-git-repo-check", "-o", output_file]
        if model:
            cmd.extend(["-m", model])
        return cmd, "codex", output_file

    # claude — modo plano = read-only, usa o login do claude
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "plan"]
    if model:
        cmd.extend(["--model", model])
    return cmd, "claude", None


_ORCH_SYSTEM = """Você é o Orquestra — o ESTRATEGISTA que coordena o projeto '{projeto}'.
Hoje é {agora}. O projeto está na pasta '{cwd}' (sua equipe já tem acesso total a ela).

QUEM VOCÊ É
Você é a mente que pensa a solução, discute arquitetura e coordena a equipe. Você NÃO
tem ferramentas: não lê arquivos, não escreve código, não roda comandos — é incapaz
disso por construção. Todo trabalho braçal é feito pela sua equipe.

SUA EQUIPE (você aciona com a tag <DELEGATE>)
- binary="operador"    → EXECUTA: lê o código, escreve, altera, roda comandos, cria
                          arquivos. É ele quem "analisa o projeto" quando o usuário pede.
- binary="revisor"     → REVISA sob demanda: caça bugs, avalia arquitetura e segurança
                          de um trecho de código ou de um diff.
- binary="open-design" → gera uma PRÉVIA VISUAL real (renderizada e navegável) de uma
                          mudança de interface.
O Cão de Guarda roda sozinho depois de cada edição — você não precisa acioná-lo.

COMO VOCÊ TRABALHA
1. Dúvida conceitual ("o que é X?", "qual a diferença entre Y e Z?", "vale a pena usar Z
   aqui?") → responda você mesmo, direto e objetivo. Sem plano, sem delegação.
2. Qualquer coisa que exija ver ou mexer no código (analisar, corrigir, implementar,
   refatorar, revisar):
   a. Escreva 1-2 frases com sua leitura do problema e o caminho que você propõe.
   b. Emita um bloco <PLANO> com os passos (quem faz o quê).
   c. PARE nessa resposta. Espere o usuário aprovar ("pode ir", "sim", "manda ver").
   d. Só emita <DELEGATE> junto do <PLANO> se o usuário JÁ tiver dito claramente para
      executar ("faz isso", "corrige agora", "analisa aí").
3. Depois do "sim": emita o(s) <DELEGATE>. Antes de cada bloco, 1-2 frases de intenção —
   é o que o usuário lê enquanto a equipe trabalha. Nunca delegue em silêncio.

FORMATO
<PLANO>
1. operador → o que vai investigar / implementar
2. revisor → o que vai revisar
</PLANO>

<DELEGATE binary="operador">
Instruções completas e autossuficientes (o operador NÃO vê esta conversa — descreva o
objetivo, os arquivos/pastas relevantes e o resultado esperado).
</DELEGATE>

CADEIA (um passo alimenta o próximo) → um único <PIPELINE> com <DELEGATE> na ordem:
<PIPELINE>
<DELEGATE binary="operador">(1ª etapa)</DELEGATE>
<DELEGATE binary="revisor">(revisa o que a 1ª etapa produziu)</DELEGATE>
</PIPELINE>
Cada etapa recebe o resultado da anterior automaticamente — não copie esse resultado na
instrução. Vários <DELEGATE> soltos (fora de <PIPELINE>) rodam em PARALELO — use só para
tarefas realmente independentes.

ANÁLISE LONGA: se o usuário pede um relatório ou análise extensa, mande o operador
ESCREVER E SALVAR um arquivo Markdown (ex: ANALISE.md) em vez de despejar tudo no chat —
o painel Editor abre o arquivo sozinho.

PRÉVIA VISUAL: se o pedido cria/altera algo de interface (tela, componente, layout, cor,
animação, redesenho), primeiro <DELEGATE binary="open-design"> pedindo a prévia, mostre
ao usuário, e só peça a implementação real depois que ele aprovar. Pule só se ele disser
que quer pular, ou se não houver nada visual.

MOSTRAR: para exibir um arquivo real do projeto enquanto conversa (ensinar/mostrar
código), escreva <MOSTRAR arquivo="caminho/relativo/ao/projeto.py"> no meio da sua
resposta — o painel Editor abre esse arquivo sozinho, sem o usuário precisar clicar em
nada. Pode escrever texto antes E depois da tag na mesma resposta, ex: "vou te mostrar
o arquivo X <MOSTRAR arquivo="X"> repara que a função Y faz Z". Use SÓ caminhos que
aparecem na lista "Arquivos do projeto" abaixo — você não lê arquivo nenhum pra
confirmar que existe, e um caminho fora da lista simplesmente falha em silêncio. Se essa
lista não aparecer abaixo (projeto sem git), você não tem essa capacidade agora — não
invente a tag.

COFRE: se o usuário precisar configurar uma API key ou senha, lembre-o de usar
/senha NOME valor (fica criptografado, nunca aparece no chat).
"""


def _project_file_tree(cwd: str, max_files: int = 300) -> str:
    """Lista leve (só caminhos) dos arquivos versionados do projeto — é o que o
    Orquestrador usa pra saber o que existe e poder citar em <MOSTRAR arquivo="...">.
    git ls-files já resolve .gitignore de graça e só lista o que está versionado,
    então não precisa reimplementar filtro nenhum. Projeto sem git (ou git falha) →
    retorna vazio; o Orquestrador simplesmente não ganha a capacidade de MOSTRAR."""
    try:
        result = subprocess.run(
            ["git", "ls-files"], cwd=cwd, capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    if result.returncode != 0:
        return ""
    files = sorted(f for f in result.stdout.splitlines() if f.strip() and not _is_noise_path(f))
    if not files:
        return ""
    shown, total = files[:max_files], len(files)
    body = "\n".join(f"- {f}" for f in shown)
    if total > max_files:
        body += f"\n... (+{total - max_files} arquivos, lista truncada)"
    return body


def _mostrar_resolve_path(rel_path: str) -> str | None:
    """Resolve um caminho vindo de <MOSTRAR arquivo="..."> contra a raiz do projeto.
    Mais restrito que _bridge_path_ok (que também aceita HOME/tempdir): aqui só a
    pasta do projeto vale, porque a lista injetada em _project_file_tree só contém
    caminhos de lá. Retorna o caminho absoluto se for seguro e existir, ou None."""
    cwd = get_project_dir()
    project_root = os.path.realpath(cwd)
    abs_path = os.path.realpath(os.path.join(cwd, rel_path))
    if abs_path != project_root and not abs_path.startswith(project_root + os.sep):
        return None
    if not os.path.isfile(abs_path):
        return None
    return abs_path


def build_orchestrator_prompt(config: dict, history: list, memory_context: str = "") -> str:
    """Monta o prompt completo do Orquestrador (system + memória + histórico).
    Usado pelo REPL e pela TUI — fonte única do texto. O marcador literal
    'Histórico da Conversa:\\n' é o ponto onde orchestrator_brain.py corta
    system/user, então não mude essa string."""
    import datetime as _dt
    system = _ORCH_SYSTEM.format(
        projeto=Path(get_project_dir()).name,
        agora=_dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        cwd=get_project_dir(),
    )
    memory_block = (
        f"\nFatos e lições conhecidos (memória de longo prazo, não é o histórico da conversa):\n{memory_context}\n"
        if memory_context else ""
    )
    file_tree = _project_file_tree(get_project_dir())
    files_block = (
        f'\nArquivos do projeto (use como referência pra <MOSTRAR arquivo="...">; '
        f"só existem estes, não invente caminhos):\n{file_tree}\n"
        if file_tree else ""
    )
    parts = [system, memory_block, files_block, "\nHistórico da Conversa:\n"]
    for msg in history[-20:]:
        prefix = "Usuário" if msg.get("role") == "user" else "Orquestra"
        parts.append(f"{prefix}: {msg.get('content', '')}\n")
    parts.append("\n\nResponda ao usuário agora:")
    return "".join(parts)


def _capture_orchestrator_result(line: str, kind: str, state: dict):
    """Extrai só final_text/is_error do stream de uma chamada do orquestrador.
    kind: 'agy' (agy/brain — envelope {"event":"result"}), 'codex' (schema
    thread/turn/item), ou 'claude' ({"type":"result"})."""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return
    if kind == "agy":
        if d.get("event") == "result":
            res = d.get("result", {})
            state["final_text"] = res.get("response", "") or ""
            state["is_error"] = res.get("status") != "SUCCESS"
    elif kind == "codex":
        t = d.get("type", "")
        if t == "item.completed":
            item = d.get("item") or {}
            if item.get("type") == "agent_message":
                state["final_text"] = item.get("text", "") or state["final_text"]
        elif t in ("turn.failed", "error"):
            state["is_error"] = True
    else:
        if d.get("type") == "result":
            state["final_text"] = d.get("result", "") or ""
            state["is_error"] = bool(d.get("is_error"))


def _run_orchestrator_stream(cmd: list, env: dict, binary: str, section: "LiveSection",
                             timeout_s: int, output_file: str | None = None) -> tuple:
    """Roda uma chamada do PRÓPRIO orquestrador (decisão inicial do turno ou
    _orchestrator_react) com heartbeat ao vivo na LiveSection, em vez do
    silêncio total + spinner genérico de antes da reforma — e com um timeout
    de verdade (mata o processo se passar de timeout_s), a mesma garantia que
    subprocess.run(timeout=...) dava antes. Retorna (final_text, ok)."""
    state = {"final_text": "", "is_error": False}
    # brain/ollama-cloud emitem o mesmo envelope {"event":"result",...} que o agy
    kind = "agy" if binary in ("agy", "ollama-cloud", "brain") else ("codex" if binary == "codex" else "claude")

    try:
        proc = subprocess.Popen(
            cmd, env=env,
            cwd=get_project_dir(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except FileNotFoundError:
        section.close(ok=False, error_detail=f"binário '{binary}' não encontrado")
        return "", False

    stop_heartbeat = threading.Event()

    def _heartbeat():
        waited = 0
        while not stop_heartbeat.wait(5):
            waited += 5
            section.tool_call(f"pensando... ({waited}s)")

    threading.Thread(target=_heartbeat, daemon=True).start()

    timed_out = {"flag": False}

    def _kill_on_timeout():
        timed_out["flag"] = True
        proc.kill()

    timer = threading.Timer(timeout_s, _kill_on_timeout)
    timer.start()

    stderr_lines = []

    def _drain_stderr():
        for l in proc.stderr:
            stderr_lines.append(l)

    threading.Thread(target=_drain_stderr, daemon=True).start()

    try:
        for line in proc.stdout:
            line = line.strip()
            if line:
                _capture_orchestrator_result(line, kind, state)
    finally:
        timer.cancel()
        stop_heartbeat.set()

    proc.wait()

    # Codex: a resposta final confiável vem do arquivo -o, não do stream.
    if output_file:
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                ftext = f.read().strip()
            if ftext:
                state["final_text"] = ftext
        except OSError:
            pass
        finally:
            try:
                os.unlink(output_file)
            except OSError:
                pass

    if timed_out["flag"]:
        section.close(ok=False, error_detail=f"não respondeu em {timeout_s}s (timeout)")
        return "", False

    if not state["final_text"] and proc.returncode != 0:
        detail = "".join(stderr_lines[-20:]).strip() or "processo terminou sem resposta"
        section.close(ok=False, error_detail=detail)
        return "", False

    if state["is_error"]:
        # O processo pode sair com código 0 mesmo reportando erro no envelope
        # JSON (is_error/status) — sem checar isso, um erro "educado" do
        # modelo seria tratado como resposta válida e tentaria ser escaneado
        # por <DELEGATE>/<PIPELINE> como se fosse texto normal.
        detail = state["final_text"] or "".join(stderr_lines[-20:]).strip() or "o modelo reportou erro"
        section.close(ok=False, error_detail=detail)
        return "", False

    section.close(ok=True)
    return state["final_text"].strip(), True


def _orchestrator_react(config: dict, event_label: str, raw_result: str) -> str:
    """Modo chat: depois que um operário termina, pede pro próprio orquestrador
    comentar o resultado em linguagem simples e dar continuidade à conversa,
    em vez de só despejar a resposta crua + uma linha fixa. Retorna "" (string
    vazia) em qualquer falha/timeout — quem chama trata isso como "sem reação"
    e cai de volta no comportamento antigo (bolha crua)."""
    role = config.get("roles", {}).get("orquestrador", {})

    projeto = Path(get_project_dir()).name
    prompt = (
        f"Você é o Orquestra, assistente do projeto '{projeto}'. Você pediu para "
        f"{event_label} executar uma tarefa e ele acabou de responder com isto:\n\n"
        f"{raw_result[:4000]}\n\n"
        "Comente esse resultado para o usuário, em português, em linguagem simples "
        "e natural — como se você estivesse continuando a conversa, não repetindo "
        "o resultado inteiro. Resuma o que foi feito e destaque só o que importa. "
        "Termine perguntando se o usuário quer ajustar algo ou seguir para outra coisa. "
        "Responda só com o texto da sua fala, sem tags e sem formatação especial.\n\n"
        "Se (e SÓ se) esse resultado revelar algo genuinamente reutilizável no futuro — uma "
        "preferência clara do usuário, um fato específico do projeto, ou uma lição sobre o que "
        "funcionou/não funcionou — termine sua resposta numa linha própria, à parte, no formato "
        "'LIÇÃO: <frase curta>'. Não force isso: a maioria das respostas não precisa de nenhuma "
        "lição. Essa linha não aparece pro usuário, só fica guardada pra você mesmo no futuro."
    )

    cmd, binary, ofile = _build_orchestrator_cmd(role, prompt)

    env = os.environ.copy()
    env_file = role.get("env_file")
    if env_file:
        env.update(load_env_file(env_file))

    section = LiveSection("Orquestra", role.get("icon", "🎼"), role.get("color", "cyan"))
    section.open()
    text, ok = _run_orchestrator_stream(cmd, env, binary, section, timeout_s=100, output_file=ofile)
    if not ok:
        return ""

    clean = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text or '')
    return clean.strip()


@dataclass
class WorkerResult:
    """Retorno de _run_worker_headless. Dataclass em vez de tupla posicional
    pra crescer (custo/tokens, adicionados na feature de gasto/uso) sem
    empilhar mais posições — cada call site lê pelo nome do campo."""
    final_text: str
    is_error: bool
    error_detail: str
    changes_summary: str
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    changed_files: list[str] = field(default_factory=list)


def _run_worker_headless(cmd: list, env: dict, cwd: str, section: "LiveSection", is_agy: bool,
                          detail: str = "normal", is_codex: bool = False, is_copilot: bool = False,
                          is_opencode: bool = False, output_file: str | None = None) -> WorkerResult:
    """Roda um operário headless, narrando eventos ao vivo na LiveSection — em
    NENHUM nível de detalhe fica em silêncio total (ver _detail_level). Abre e
    fecha a seção aqui mesmo, já com o resumo de erro embutido no fechamento.
    Retorna um WorkerResult (final_text, is_error, error_detail, changes_summary,
    cost_usd, tokens_in, tokens_out, duration_ms).

    output_file: quando informado (Codex), depois que o processo termina o
    conteúdo desse arquivo (-o/--output-last-message) sobrescreve o final_text
    capturado via streaming — é a fonte mais confiável, já que o parser do
    --json do Codex é best-effort."""
    state = {"final_text": "", "is_error": False, "cost_usd": 0.0, "tokens_in": 0,
             "tokens_out": 0, "duration_ms": 0}
    stderr_lines = []
    # Snapshot pra avisar, ao fechar a seção, quais arquivos reais foram tocados —
    # mesma técnica já usada no Open Design (_git_dirty_files), estendida aqui pra
    # claude/agy/codex/deepseek. None se `cwd` não for um repo git.
    files_before = _git_dirty_files(cwd)

    # A seção já deve estar aberta (section.open()) por quem chamou — isso permite
    # abrir várias seções juntas, como um bloco, antes de disparar as threads dos
    # workers (ver Requisito de paralelismo empilhado no plano da reforma).
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except FileNotFoundError as e:
        section.close(ok=False, error_detail=f"binário não encontrado: {e}")
        return "", True, f"binário não encontrado: {e}"

    def _drain_stderr():
        for l in proc.stderr:
            stderr_lines.append(l)

    threading.Thread(target=_drain_stderr, daemon=True).start()

    if is_codex:
        parser = _narrate_codex_line
    elif is_copilot:
        parser = _narrate_copilot_line
    elif is_agy:
        parser = _narrate_agy_line
    elif is_opencode:
        parser = _narrate_opencode_line
    else:
        parser = _narrate_claude_line

    for line in proc.stdout:
        line = line.strip()
        if line:
            parser(line, section, state, detail=detail)

    proc.wait()
    section.end_line()

    if output_file:
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                file_text = f.read().strip()
            if file_text:
                state["final_text"] = file_text
        except OSError:
            pass

    if not state["final_text"] and proc.returncode != 0:
        state["is_error"] = True

    if state["is_error"]:
        stderr_detail = "".join(stderr_lines[-40:]).strip()
        # Codex reporta o erro de verdade no stdout (JSON), não no stderr —
        # sem isso, o erro chegaria em tela como "(sem detalhes de stderr)"
        # mesmo já sabendo a causa real (capturada em final_text pelo parser).
        if is_codex and state["final_text"]:
            error_detail = state["final_text"] if not stderr_detail else f"{state['final_text']}\n{stderr_detail}"
        else:
            error_detail = stderr_detail
    else:
        error_detail = ""

    changes_summary = ""
    changed_files: list[str] = []
    if files_before is not None:
        files_after = _git_dirty_files(cwd)
        touched = (files_after or set()) - files_before
        if touched:
            changed_files = sorted(touched)
            changes_summary = f"Arquivos alterados: {', '.join(changed_files)}"
            try:
                diff_stat = subprocess.run(
                    ["git", "diff", "--stat"], cwd=cwd, capture_output=True, text=True, timeout=10,
                )
                if diff_stat.returncode == 0 and diff_stat.stdout.strip():
                    changes_summary += f"\n{diff_stat.stdout.strip()[:800]}"
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    section.close(ok=not state["is_error"], error_detail=error_detail, changes_summary=changes_summary)

    return WorkerResult(
        final_text=state["final_text"], is_error=state["is_error"], error_detail=error_detail,
        changes_summary=changes_summary, cost_usd=state["cost_usd"], tokens_in=state["tokens_in"],
        tokens_out=state["tokens_out"], duration_ms=state["duration_ms"], changed_files=changed_files,
    )


DELEGATE_RE = re.compile(r'<DELEGATE\s+binary="([^"]+)">(.*?)</DELEGATE>', re.DOTALL | re.IGNORECASE)
PIPELINE_RE = re.compile(r'<PIPELINE>(.*?)</PIPELINE>', re.DOTALL | re.IGNORECASE)
PLANO_RE   = re.compile(r'<PLANO>(.*?)</PLANO>', re.DOTALL | re.IGNORECASE)
MOSTRAR_RE = re.compile(r'<MOSTRAR\s+arquivo="([^"]+)"\s*/?>', re.IGNORECASE)
SUGESTAO_RE = re.compile(
    r'<SUGESTAO\s+arquivo="([^"]+)"\s+linha_inicio="(\d+)"\s+linha_fim="(\d+)"\s+motivo="([^"]*)"\s*>(.*?)</SUGESTAO>',
    re.DOTALL | re.IGNORECASE,
)


# Defaults de label/ícone/cor/binary por tag — usados só quando o config não
# sobrescreve nada. Tag desconhecida cai no default do "operador".
_WORKER_TAG_DEFAULTS = {
    "operador":   ("claude",  "Operador",       "🔨", "cyan"),
    "revisor":    ("claude",  "Revisor",        "🔍", "blue"),
    "cao_guarda": ("copilot", "Cão de Guarda",  "🐕", "yellow"),
    "claude":     ("claude",  "Motor Claude",   "💞", "cyan"),
    "agy":        ("agy",     "Motor Antigravity", "💚", "green"),
    "deepseek":   ("claude",  "Motor DeepSeek", "💙", "blue"),
    "codex":      ("codex",   "Motor Codex",    "🤖", "magenta"),
    "opencode":   ("opencode", "Motor OpenCode", "🌐", "white"),
}

# Tags legadas (prompts/históricos antigos) → papel canônico da arquitetura de 3.
_WORKER_ALIASES = {
    "implementador": "operador", "designer": "operador", "pesquisador": "operador",
    "arquiteto": "revisor", "analista": "revisor", "fiscal": "revisor",
    "seguranca": "revisor", "segurança": "revisor",
    "guarda": "cao_guarda", "watchdog": "cao_guarda", "cão_de_guarda": "cao_guarda",
}


def _resolve_worker(config: dict, target_binary_tag: str) -> tuple:
    """Resolve qual operário real usar a partir da tag do <DELEGATE>/<PIPELINE>.
    Retorna (worker_config, actual_binary, label, icon, color).

    Tags legadas (implementador, arquiteto, ...) são traduzidas para os papéis
    canônicos (operador/revisor/cao_guarda) via _WORKER_ALIASES, então históricos
    e prompts antigos continuam funcionando. O que estiver salvo em
    config["workers"][tag] SEMPRE tem prioridade sobre o default da tag — é o que
    faz o menu `/agentes trocar` conseguir trocar o motor de qualquer papel."""
    workers = config.get("workers", {})
    tag = target_binary_tag.lower().strip()
    tag = _WORKER_ALIASES.get(tag, tag)

    worker = workers.get(tag) or workers.get("operador", {})
    default_binary, default_label, default_icon, default_color = _WORKER_TAG_DEFAULTS.get(
        tag, ("claude", tag.capitalize(), "🤖", "white")
    )

    return (
        worker,
        worker.get("binary", default_binary),
        worker.get("label", default_label),
        worker.get("icon", default_icon),
        worker.get("color", default_color),
    )


def _build_worker_cmd(worker: dict, actual_binary: str, instruction: str, read_only: bool = False) -> tuple:
    """Monta o comando headless e o env pra rodar um operário — mesma lógica pra
    chamada solta (<DELEGATE>) ou dentro de um <PIPELINE>. Retorna
    (cmd, worker_env, output_file) — output_file é None pra claude/agy (o texto
    final vem do streaming) e um caminho de arquivo temporário pro codex (a CLI
    dele não segue o padrão -p/--print + stream-json dos outros dois; usa
    subcomando `exec` + -o/--output-last-message pra escrever a resposta final
    direto num arquivo, mais confiável que casar o evento certo no --json dele).

    read_only=True troca as flags de permissão pelo modo plano/read-only já
    testado em _build_orchestrator_cmd (claude --permission-mode plan,
    agy --mode plan, codex --sandbox read-only) — usado pelo Modo Aprendizado,
    onde o Revisor só pode ler e comentar, nunca escrever de verdade (texto no
    prompt sozinho não segura isso, confirmado com o Orquestrador)."""
    worker_model = worker.get("model", "")
    worker_env_file = worker.get("env_file", "")
    output_file = None

    if actual_binary == "agy":
        # SEM --project: com --project, o agy opera no workspace-brain dele
        # (~/.gemini/antigravity-cli/brain/<id>), não na pasta real. E --add-dir
        # PRECISA ser caminho absoluto — "." é resolvido relativo ao scratch do
        # agy, não ao cwd do processo. Com --add-dir <abs> o agy roda os comandos
        # na pasta real do projeto (testado).
        cmd = ["agy", "-p", instruction, "--output-format", "stream-json"]
        cmd += ["--mode", "plan"] if read_only else ["--dangerously-skip-permissions"]
        cmd += ["--add-dir", get_project_dir()]
        if worker_model:
            cmd.extend(["--model", worker_model])
    elif actual_binary == "codex":
        fd, output_file = tempfile.mkstemp(prefix="orquestra_codex_", suffix=".txt")
        os.close(fd)
        sandbox = "read-only" if read_only else "workspace-write"
        cmd = ["codex", "exec", instruction,
               "--json", "--sandbox", sandbox,
               "--skip-git-repo-check",
               "-o", output_file]
        if worker_model:
            cmd.extend(["-m", worker_model])
    elif actual_binary == "copilot":
        # gh copilot -p retorna texto puro por padrão.
        # Não suporta --output-format json (flag só existe no Copilot agent CLI nativo,
        # não no wrapper `gh copilot`). Usar sem essa flag e parsear como texto.
        cmd = ["gh", "copilot", "-p", instruction,
               "--no-ask-user"]
        if worker_model:
            cmd.extend(["--model", worker_model])
    elif actual_binary == "opencode":
        # opencode já vem com a conta OpenRouter logada (sem API key no cofre).
        # --agent plan = agente com permissão de edição negada de verdade
        # (testado: bloqueia escrita sem travar em modo headless); --auto =
        # aprova as permissões que não forem explicitamente negadas.
        cmd = ["opencode", "run", instruction,
               "--format", "json", "--dir", get_project_dir()]
        cmd += ["--agent", "plan"] if read_only else ["--auto"]
        if worker_model:
            cmd.extend(["-m", worker_model])
    else:
        # claude (inclui a tag "deepseek", que é claude + env_file) e qualquer
        # binary inesperado. Só passa --model se parecer um modelo que o claude
        # aceita (claude-* ou deepseek-* via env) — senão o claude rejeita e o
        # operário "não funciona" sem erro claro.
        perm_mode = "plan" if read_only else "bypassPermissions"
        cmd = ["claude", "-p", instruction,
               "--output-format", "stream-json", "--verbose",
               "--permission-mode", perm_mode]
        _m = (worker_model or "").lower()
        if worker_model and (_m.startswith("claude") or _m.startswith("deepseek") or actual_binary == "claude"):
            cmd.extend(["--model", worker_model])

    worker_env = os.environ.copy()
    if worker_env_file and Path(os.path.expanduser(worker_env_file)).exists():
        worker_env.update(load_env_file(worker_env_file))

    return cmd, worker_env, output_file

_WATCHDOG_BUG_WORDS = (
    "bug", "erro", "falha", "crash", "exception", "vulnerab", "quebr",
    "incorret", "race condition", "regress", "null", "undefined", "leak",
)
_WATCHDOG_SKIP_PREFIXES = ("AI Credits", "Tokens", "Resume", "Changes ", "Log:", "●", "│", "└")
_watchdog_lock = threading.Lock()
_watchdog_last = [0.0]


def _git_diff_snapshot(cwd: str, max_chars: int = 6000) -> str:
    """Diff das mudanças não commitadas (tracked + conteúdo dos untracked), já
    pronto pra colar no prompt do Cão de Guarda. '' se não houver nada."""
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=cwd,
                                capture_output=True, text=True, timeout=10)
        if status.returncode != 0 or not status.stdout.strip():
            return ""
        parts = []
        tracked = subprocess.run(["git", "diff", "--unified=3"], cwd=cwd,
                                 capture_output=True, text=True, timeout=15)
        if tracked.stdout.strip():
            parts.append(tracked.stdout)
        for line in status.stdout.splitlines():
            if line.startswith("??"):
                path = line[3:].strip().strip('"')
                fp = Path(cwd) / path
                try:
                    if fp.is_file() and fp.stat().st_size < 40000:
                        parts.append(f"\n--- NOVO ARQUIVO: {path} ---\n" + fp.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
        blob = "\n".join(parts).strip()
        return blob[:max_chars] + ("\n…(diff truncado)" if len(blob) > max_chars else "")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def watchdog_verdict(config: dict, cwd: str = None) -> tuple:
    """Roda o Cão de Guarda (SÍNCRONO) sobre o diff atual. Passa o diff pronto no
    prompt em vez de pedir pro agente rodar git — mais rápido e determinístico.
    Retorna (texto, achou_bug). ('', False) quando não há mudança, o binário
    falha, ou o veredito é 'tudo certo'."""
    cwd = cwd or get_project_dir()
    diff = _git_diff_snapshot(cwd)
    if not diff:
        return "", False

    worker, binary, *_ = _resolve_worker(config, "cao_guarda")
    model = worker.get("model", "auto")
    prompt = (
        "Você é o Cão de Guarda. Analise APENAS o diff abaixo em busca de BUGS ou lógica "
        "errada (ignore estilo e formatação). Se achar algo, seja curto e direto: arquivo, "
        "o problema e o impacto. Se estiver tudo certo, responda somente 'Tudo certo'.\n\n"
        f"{diff}"
    )

    if binary == "copilot":
        cmd = ["gh", "copilot", "-p", prompt, "--no-ask-user"]
        if model and model != "auto":
            cmd += ["--model", model]
    elif binary == "agy":
        cmd = ["agy", "-p", prompt]
        if model:
            cmd += ["--model", model]
    elif binary == "codex":
        cmd = ["codex", "exec", prompt, "--sandbox", "read-only", "--skip-git-repo-check"]
    else:
        cmd = ["claude", "-p", prompt]
        if model:
            cmd += ["--model", model]

    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                             timeout=90, stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "", False

    out = _ANSI_RE.sub("", res.stdout or "")
    out = "\n".join(l for l in out.splitlines() if not l.startswith(_WATCHDOG_SKIP_PREFIXES)).strip()
    if not out or ("tudo certo" in out.lower() and len(out) < 60):
        return "", False
    has_bug = any(w in out.lower() for w in _WATCHDOG_BUG_WORDS)
    return out, has_bug


def _run_copilot_watchdog(config: dict = None):
    """Dispara o Cão de Guarda numa thread daemon (não bloqueia o operário) e
    imprime no terminal SÓ se houver algo a dizer. Cooldown de 15s pra não rodar
    N vezes quando vários operários paralelos terminam quase juntos."""
    with _watchdog_lock:
        if time.time() - _watchdog_last[0] < 15:
            return
        _watchdog_last[0] = time.time()
    cfg = config or load_config()

    def _run():
        text, has_bug = watchdog_verdict(cfg)
        if not text:
            return
        marker = "⚠ [bold red]" if has_bug else "[bold yellow]"
        with _console_lock:
            console.print(f"\n{marker}🐕 Cão de Guarda[/]\n{text}\n")

    threading.Thread(target=_run, daemon=True, name="cao-guarda").start()

def _run_pipeline(config: dict, steps: list, chat_mode: bool, detail: str):
    """Executa uma cadeia de operários em sequência (um <PIPELINE> emitido pelo
    orquestrador): a resposta de cada etapa vira contexto pra próxima instrução,
    e ao final o orquestrador sintetiza tudo pro usuário revisar/implementar.
    Roda inteira numa única thread de fundo — o REPL principal fica livre o
    tempo todo, mesmo com várias etapas em sequência."""
    total = len(steps)
    previous_result = ""
    step_summaries = []

    for idx, (target_binary_tag, instruction) in enumerate(steps, start=1):
        if target_binary_tag in ("open-design", "opendesign", "open_design"):
            msg = (f"[Pipeline interrompido na etapa {idx}]: binary=\"open-design\" ainda não é "
                   "suportado dentro de um <PIPELINE> (só funciona como <DELEGATE> solto).")
            save_chat_message("orquestrador", "assistant", msg)
            console.print(f"[bold red]⚠ Pipeline: etapa {idx} pediu Open Design, que ainda não roda em cadeia.[/bold red]")
            return

        worker, actual_binary, label, icon, color = _resolve_worker(config, target_binary_tag)

        full_instruction = instruction
        if previous_result:
            full_instruction = (
                f"Contexto — resultado da etapa anterior do pipeline ({step_summaries[-1][0]}):\n\n"
                f"{previous_result[:4000]}\n\n"
                f"Sua tarefa agora:\n{instruction}"
            )

        console.print(f"[dim]🔗 Pipeline — etapa {idx}/{total}: {label}[/dim]")

        cmd, worker_env, output_file = _build_worker_cmd(worker, actual_binary, full_instruction)
        section = LiveSection(label, icon, color)
        section.open()
        
        pre_snapshot = "\n".join(sorted(_git_status_paths(get_project_dir())))
        task_id = create_task(instruction, label, pre_snapshot=pre_snapshot)
        result = _run_worker_headless(
            cmd, worker_env, get_project_dir(), section,
            actual_binary == "agy", detail=detail,
            is_codex=(actual_binary == "codex"), is_copilot=(actual_binary == "copilot"),
            is_opencode=(actual_binary == "opencode"), output_file=output_file,
        )
        final_text, is_error, error_detail, changes_summary = (
            result.final_text, result.is_error, result.error_detail, result.changes_summary,
        )
        if output_file:
            try:
                os.unlink(output_file)
            except OSError:
                pass

        # Failover: erro de limite/quota → tenta uma vez com o motor de reserva,
        # numa LiveSection NOVA (a anterior já foi fechada por _run_worker_headless).
        low_err = (error_detail or "").lower()
        if is_error and ("429" in low_err or "credit" in low_err or "quota" in low_err or "rate limit" in low_err):
            fb_map = {"claude": "copilot", "deepseek": "claude", "codex": "copilot", "agy": "copilot", "copilot": "claude", "opencode": "copilot"}
            fb_bin = fb_map.get(actual_binary, "copilot")
            console.print(f"[bold yellow]⚠️ {label} atingiu limite — failover para {fb_bin}...[/bold yellow]")
            fb_cmd, fb_env, fb_out = _build_worker_cmd({"binary": fb_bin, "model": ""}, fb_bin, full_instruction)
            fb_section = LiveSection(f"{label} → {fb_bin}", icon, color)
            fb_section.open()
            result = _run_worker_headless(
                fb_cmd, fb_env, get_project_dir(), fb_section, fb_bin == "agy", detail=detail,
                is_codex=(fb_bin == "codex"), is_copilot=(fb_bin == "copilot"),
                is_opencode=(fb_bin == "opencode"), output_file=fb_out,
            )
            final_text, is_error, error_detail, changes_summary = (
                result.final_text, result.is_error, result.error_detail, result.changes_summary,
            )
            if fb_out:
                try:
                    os.unlink(fb_out)
                except OSError:
                    pass

        if is_error:
            console.print(f"[bold red]⚠ Pipeline parou na etapa {idx} ({label}) por erro.[/bold red]")
            detail_text = error_detail or "(sem detalhes de stderr)"
            summary = f"[Pipeline interrompido na etapa {idx}/{total} — erro do {label}]:\n{detail_text[-1500:]}"
            save_chat_message("orquestrador", "assistant", summary)
            _record_agent_performance(get_project_id(), label, _infer_task_kind(instruction), "error",
                                       result.cost_usd, result.tokens_in, result.tokens_out, result.duration_ms)
            update_task_status(task_id, "❌ Erro", final_text[:4000] if final_text else None)
            return

        if not is_error and changes_summary:
            new_status = "⚠️ Aguardando Aprovação"
            # Dispara o cão de guarda na última etapa se houver mudanças
            if idx == total:
                _run_copilot_watchdog(config)
        else:
            new_status = "✅ Concluído"
        update_task_status(task_id, new_status, final_text[:4000] if final_text else None)

        captured = final_text.strip()
        step_summaries.append((label, captured))
        save_chat_message("orquestrador", "assistant", f"[Pipeline etapa {idx}/{total} — {label}]:\n{captured[:4000]}")
        _record_agent_performance(get_project_id(), label, _infer_task_kind(instruction), "success",
                                   result.cost_usd, result.tokens_in, result.tokens_out, result.duration_ms)
        previous_result = captured

    combined = "\n\n".join(f"--- {lbl} ---\n{txt[:3000]}" for lbl, txt in step_summaries)
    chain_desc = " → ".join(lbl for lbl, _ in step_summaries)

    if chat_mode:
        comment = _orchestrator_react(config, f"o pipeline {chain_desc}", combined)
        comment, lesson = _extract_lesson(comment)
        if lesson:
            _store_lesson(get_project_id(), "lesson", lesson, source=f"pipeline {chain_desc}")
        if comment:
            save_chat_message("orquestrador", "assistant", comment)
            orch_role = config.get("roles", {}).get("orquestrador", {})
            _print_chat_bubble(
                "Orquestra", orch_role.get("icon", "🎼"),
                orch_role.get("color", "cyan"), comment,
            )
        else:
            _print_chat_bubble(f"Pipeline ({chain_desc})", "🔗", "yellow", combined)
    else:
        console.print(f"[bold green]✓ Pipeline concluído ({chain_desc}) — resultado final salvo no contexto do Orquestra[/bold green]")
        console.print(combined)


# ─────────────────────────────────────────────────────────
# REPL principal
# ─────────────────────────────────────────────────────────


class AtFileCompleter(Completer):
    def get_completions(self, document, complete_event):
        word = document.get_word_before_cursor(WORD=True)
        if word.startswith('@'):
            prefix = word[1:]
            try:
                for root, dirs, files in os.walk('.'):
                    if '.git' in dirs: dirs.remove('.git')
                    if '__pycache__' in dirs: dirs.remove('__pycache__')
                    if 'node_modules' in dirs: dirs.remove('node_modules')
                    
                    for f in files:
                        rel_path = os.path.relpath(os.path.join(root, f), '.')
                        if rel_path.startswith('./'):
                            rel_path = rel_path[2:]
                        if rel_path.lower().startswith(prefix.lower()):
                            yield Completion(rel_path, start_position=-len(prefix))
            except Exception:
                pass

def main():
    global _last_plan
    config = load_config()
    _init_memory_db()

    # Sobe o servidor bridge ANTES de qualquer chamada a _auto_open_in_cursor
    _start_bridge_server()

    # Cria ou limpa o arquivo de log do projeto
    log_path = Path(get_project_dir()) / LOG_FILE_NAME
    log_path.write_text("# Orquestra — Logs de Execução ao Vivo\n\nAbra este arquivo ao lado do seu terminal para ver os agentes trabalhando.\n\n", encoding="utf-8")
    
    # Abre o log automaticamente no Cursor no boot (silencioso se não tiver cursor)
    _auto_open_in_cursor([str(log_path)])

    print_banner()

    # ── Cofre de Segurança ──────────────────────────────────
    # VaultManager() levanta RuntimeError se 'cryptography' não estiver
    # instalado — sem este try/except o Orquestra nem chegava a abrir
    # (traceback cru) num ambiente que só não tinha essa dependência opcional.
    _vault = None
    try:
        _vault = VaultManager(get_project_dir())
        if _vault.needs_setup():
            _vault.setup_wizard()
        else:
            _vault.unlock_interactive()
        # Injeta credenciais salvas como variáveis de ambiente da sessão
        _vault.inject_env()
    except RuntimeError as e:
        console.print(f"[bold red]⚠ Cofre de credenciais indisponível: {e}[/bold red]")
        console.print("[dim]Continuando sem o cofre nesta sessão (comandos /senha* ficam desativados).[/dim]\n")
        _vault = None
    # ────────────────────────────────────────────────────────

    orch = config.get("roles", {}).get("orquestrador", {})
    modelo_atual = orch.get("model", orch.get("binary", "?"))
    console.print(f"  modelo: [dim]{modelo_atual}[/dim]  ·  [dim]/agentes trocar[/dim] para mudar  ·  [dim]/ajuda[/dim] para comandos\n")
    
    # Exibe contexto recente silenciosamente (sem chamada de rede)
    show_recent_context()

    
    kb = KeyBindings()
    
    @kb.add("f1")
    def _(event):
        # Type /ajuda and submit
        event.app.current_buffer.text = "/ajuda"
        event.app.current_buffer.validate_and_handle()

    @kb.add("f2")
    def _(event):
        # Type /agentes trocar and submit
        event.app.current_buffer.text = "/agentes trocar"
        event.app.current_buffer.validate_and_handle()
        
    @kb.add("f3")
    def _(event):
        event.app.current_buffer.text = "/tarefas"
        event.app.current_buffer.validate_and_handle()

    def bottom_toolbar():
        c = load_config()
        orch = c.get("roles", {}).get("orquestrador", {})
        m = orch.get("model", orch.get("binary", "?"))
        return f" Orquestra: {m}  |  [F1] Comandos  |  [F2] Trocar Combustível  |  [F3] Fila de Tarefas "

    session = PromptSession(
        history=FileHistory(str(HISTORY_FILE)),
        style=Style.from_dict({
            "prompt": "bold ansicyan",
            "bottom-toolbar": "bg:#333333 #ffffff",
        }),
        completer=AtFileCompleter(),
        complete_while_typing=True,
        key_bindings=kb,
        bottom_toolbar=bottom_toolbar,
    )

    while True:
        try:
            # reload config a cada loop (caso tenha editado o json por fora)
            config = load_config()
            
            prompt_text = HTML(
                '<ansigreen><b>orquestra</b></ansigreen>'
                '<ansiwhite> → </ansiwhite>'
            )
            # raw=True é essencial aqui: o padrão (raw=False) REMOVE/ESCAPA
            # sequências de escape vt100 de qualquer coisa impressa por uma
            # thread de fundo enquanto o prompt está ativo — é isso que fazia
            # todo output colorido do Rich (Rule, Panel, Markdown) vazar como
            # texto literal tipo "?[36m" na tela, em vez de virar cor de verdade.
            with patch_stdout(raw=True):
                raw = session.prompt(prompt_text).strip()
        except (KeyboardInterrupt, EOFError):
            handle_safe_exit(config)

        if not raw:
            continue

        if raw.lower().startswith("/goal "):
            goal_text = raw[6:].strip()
            raw = (
                f"O usuário enviou um /goal: '{goal_text}'. "
                "Sua missão é atuar como Estrategista. Quebre esse objetivo macro "
                "em até 5 tarefas, utilizando obrigatoriamente um único bloco <PIPELINE>. "
                "Planeje as etapas, por exemplo: primeiro um operário analisa, depois outro "
                "escreve código, e outro revisa. Retorne apenas sua fala de introdução e o bloco <PIPELINE>."
            )

        # Inject @files
        file_matches = set(re.findall(r'@([\w./-]+)', raw))
        injected_context = ""
        for fpath in file_matches:
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        file_text = f.read()
                    injected_context += f"\n\n--- Contexto Injetado: {fpath} ---\n{file_text}\n"
                    console.print(f"[dim]📄 Arquivo injetado: {fpath}[/dim]")
                except Exception as e:
                    console.print(f"[dim red]Erro ao injetar {fpath}: {e}[/dim red]")
        
        if injected_context:
            raw = raw + injected_context

        # ── Câmbio Automático: injeta contexto do arquivo aberto no Cursor ────────
        # Se o usuário não referenciou @arquivo nenhum, mas tem um arquivo aberto
        # no Cursor, injetamos silenciosamente como contexto de fundo.
        # Isso permite perguntas como "o que essa função faz?" sem precisar digitar @arquivo.
        if not file_matches:
            cursor_ctx = _get_cursor_context()
            if cursor_ctx.get('file') and os.path.isfile(cursor_ctx['file']):
                try:
                    with open(cursor_ctx['file'], 'r', encoding='utf-8', errors='ignore') as _cf:
                        _ctx_text = _cf.read()
                    _ctx_fname = os.path.basename(cursor_ctx['file'])
                    _ctx_line = cursor_ctx.get('line', 0)
                    _ctx_note = f" (cursor na linha {_ctx_line})" if _ctx_line else ""
                    raw = (
                        raw
                        + f"\n\n--- Arquivo aberto no Cursor{_ctx_note}: {cursor_ctx['file']} ---\n"
                        + _ctx_text
                        + "\n--- fim do contexto automático ---"
                    )
                    console.print(f"[dim]🔭 Contexto automático: {_ctx_fname}{_ctx_note}[/dim]")
                except Exception:
                    pass

        low = raw.lower()

        # ── Cofre de Segurança: intercepta /senha antes de qualquer agente ──
        vault_resp = _vault.handle_command(raw) if _vault else None
        if vault_resp is not None:
            # Apaga o comando do histórico do terminal (contém valor sensível)
            try:
                hist_file = HISTORY_FILE
                if hist_file.exists():
                    lines = hist_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    # Remove a última linha que contém /senha com valor
                    if lines and raw.strip() in lines[-1]:
                        hist_file.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
            except Exception:
                pass
            console.print(f"🔐 [bold green]{vault_resp}[/bold green]")
            continue
        # ────────────────────────────────────────────────────

        if low in ("/sair", "/exit", "/quit", "exit", "quit"):
            handle_safe_exit(config)

        elif low in ("/ajuda", "/help", "/?"):
            print_help()

        elif low == "/plano":
            if _last_plan:
                show_plan_viewer(_last_plan, config)
            else:
                console.print("[dim]Nenhum plano gerado ainda. Peça ao Orquestrador para planejar uma tarefa.[/dim]")

        elif low == "/tarefas":


            recent = get_recent_tasks()
            if not recent:
                console.print("[dim]Nenhuma tarefa recente.[/dim]")
            else:
                from rich.table import Table
                t = Table(title="Tarefas Recentes")
                t.add_column("ID", style="cyan", justify="right")
                t.add_column("Operário", style="green")
                t.add_column("Status", style="yellow")
                t.add_column("Instrução")
                for task in recent:
                    t.add_row(str(task["id"]), task["agent"], task["status"], _preview(task["title"], 60))
                console.print(t)
                
        elif low.startswith("/aprovar"):
            parts = low.split()
            if len(parts) > 1:
                try:
                    tid = int(parts[1])
                    task = get_task(tid)
                    if task and task['status'] == "⚠️ Aguardando Aprovação":
                        # Comita só os arquivos que mudaram DEPOIS do snapshot desta
                        # tarefa — nunca "git add .", que arrastaria junto qualquer
                        # edição manual do usuário ou de outra tarefa ainda pendente
                        # no mesmo repositório (ex.: outra etapa de PIPELINE, ou um
                        # <DELEGATE> paralelo) pro mesmo commit.
                        pre_paths = set((task.get('pre_snapshot') or "").splitlines())
                        pre_paths.discard("")
                        touched = _git_status_paths(get_project_dir()) - pre_paths
                        if touched:
                            subprocess.run(["git", "add", "--"] + sorted(touched), cwd=get_project_dir(), capture_output=True)
                            subprocess.run(["git", "commit", "-m", f"Orquestra: Tarefa #{tid} aprovada"], cwd=get_project_dir(), capture_output=True)
                            update_task_status(tid, "✅ Aprovado (Commitado)", task['result'])
                            console.print(f"[bold green]Tarefa #{tid} aprovada e salva no Git![/bold green]")
                        else:
                            update_task_status(tid, "✅ Aprovado (sem mudanças novas)", task['result'])
                            console.print(f"[yellow]Tarefa #{tid}: nenhuma mudança nova detectada desde o início da tarefa (pode já ter sido commitada).[/yellow]")
                    else:
                        console.print(f"[red]Tarefa #{tid} não está aguardando aprovação.[/red]")
                except ValueError:
                    console.print("[red]ID inválido.[/red]")
            else:
                console.print("[red]Uso: /aprovar [numero][/red]")

        elif low.startswith("/rejeitar"):
            parts = low.split()
            if len(parts) > 1:
                try:
                    tid = int(parts[1])
                    task = get_task(tid)
                    if task and task['status'] == "⚠️ Aguardando Aprovação":
                        # Rede de segurança: git reset --hard/clean -fd atinge a árvore
                        # INTEIRA, não só os arquivos desta tarefa. Antes disso existia,
                        # rejeitar uma tarefa também apagava qualquer edição manual do
                        # usuário e qualquer outra tarefa/etapa de pipeline ainda
                        # pendente no mesmo repositório — perda de trabalho silenciosa.
                        pre_paths = set((task.get('pre_snapshot') or "").splitlines())
                        pre_paths.discard("")
                        other_pending = [t for t in get_pending_tasks() if t['id'] != tid]
                        if pre_paths:
                            console.print(
                                f"[bold red]✗ Não posso reverter a Tarefa #{tid} com segurança: já havia "
                                f"{len(pre_paths)} arquivo(s) modificado(s) ANTES dela rodar (edição manual sua ou "
                                f"de outra tarefa). Um reset apagaria esse trabalho também. Reverta manualmente "
                                f"com git (ex.: git diff / git checkout -- <arquivo>) escolhendo só o que quer descartar.[/bold red]"
                            )
                        elif other_pending:
                            ids = ", ".join(f"#{t['id']}" for t in other_pending)
                            console.print(
                                f"[bold red]✗ Existe(m) outra(s) tarefa(s) aguardando aprovação no mesmo repositório "
                                f"({ids}) — um reset apagaria o trabalho delas também. Resolva-as primeiro "
                                f"(/aprovar ou /rejeitar) e só então rejeite a #{tid}.[/bold red]"
                            )
                        else:
                            subprocess.run(["git", "reset", "--hard"], cwd=get_project_dir(), capture_output=True)
                            subprocess.run(["git", "clean", "-fd"], cwd=get_project_dir(), capture_output=True)
                            update_task_status(tid, "❌ Rejeitado (Rollback)", task['result'])
                            console.print(f"[bold red]Mudanças da Tarefa #{tid} revertidas com sucesso![/bold red]")
                    else:
                        console.print(f"[red]Tarefa #{tid} não está aguardando aprovação.[/red]")
                except ValueError:
                    console.print("[red]ID inválido.[/red]")
            else:
                console.print("[red]Uso: /rejeitar [numero][/red]")

        elif low.startswith("/tarefa "):
            try:
                tid = int(low.split()[1])
                task = get_task(tid)
                if not task:
                    console.print(f"[red]Tarefa {tid} não encontrada.[/red]")
                else:
                    console.print(f"[bold cyan]Tarefa #{task['id']}[/bold cyan] — {task['agent']} ({task['status']})")
                    console.print(f"[dim]Criada em: {task['created_at']}[/dim]")
                    console.print(f"\n[bold]Instrução:[/bold]\n{task['title']}")
                    if task['result']:
                        console.print(f"\n[bold]Resultado:[/bold]\n{task['result']}")
            except ValueError:
                console.print("[red]ID inválido. Use /tarefa [numero][/red]")


        elif low in ("/agentes", "/agents", "/lista", "/roles"):
            print_roles(config)
            console.print("[dim]Dica: use [bold]/agentes trocar[/bold] para abrir o menu interativo de modelos.[/dim]")

        elif low in ("/agentes trocar", "/agents swap", "/trocar-agente", "/swap"):
            interactive_agent_selection(config)
            config = load_config()  # recarrega após a mudança
            
        elif low in ("/config", "/editar-config", "/edit"):
            edit_config()

        elif low.startswith("/modo") or low.startswith("/mode"):
            parts = raw.split(maxsplit=1)
            if len(parts) == 1:
                atual = config.get("ui", {}).get("mode", "tecnico")
                console.print(f"[yellow]Modo atual: {atual}. Uso: /modo chat  ou  /modo tecnico[/yellow]")
                continue

            requested = parts[1].strip().lower()
            if requested in ("chat", "clean", "limpo"):
                config.setdefault("ui", {})["mode"] = "chat"
            elif requested in ("tecnico", "técnico", "technical", "dev"):
                config.setdefault("ui", {})["mode"] = "tecnico"
            else:
                console.print(f"[bold red]Modo desconhecido: '{requested}'. Use 'chat' ou 'tecnico'.[/bold red]")
                continue

            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            if config["ui"]["mode"] == "chat":
                console.print("[bold green]💬 Modo chat ativado — respostas limpas, sem narração técnica.[/bold green]")
            else:
                console.print("[bold green]🔧 Modo técnico ativado — narração completa de tool calls.[/bold green]")

        elif low.startswith("/detalhe") or low.startswith("/detail"):
            parts = raw.split(maxsplit=1)
            if len(parts) == 1:
                atual = _detail_level(config)
                console.print(f"[yellow]Nível de detalhe atual: {atual}. Uso: /detalhe minimal | normal | full[/yellow]")
                continue

            requested = parts[1].strip().lower()
            if requested not in ("minimal", "normal", "full"):
                console.print(f"[bold red]Nível desconhecido: '{requested}'. Use minimal, normal ou full.[/bold red]")
                continue

            config.setdefault("ui", {})["detail"] = requested
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            console.print(f"[bold green]🔎 Nível de detalhe ajustado para: {requested}[/bold green]")

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



        elif low in ("/cursor", "/editor"):
            console.print("[dim]Abrindo projeto no Cursor...[/dim]")
            _auto_open_in_cursor(["."])
            
        elif low.startswith("/diff"):
            parts = low.split()
            if len(parts) > 1:
                try:
                    tid = int(parts[1])
                    task = get_task(tid)
                    if task and task['status'] == "⚠️ Aguardando Aprovação":
                        pre_paths = set((task.get('pre_snapshot') or "").splitlines())
                        pre_paths.discard("")
                        touched = list(_git_status_paths(get_project_dir()) - pre_paths)
                        if touched:
                            console.print(f"[dim]Abrindo diff de {len(touched)} arquivo(s)...[/dim]")
                            for f in touched[:3]:
                                f_path = Path(get_project_dir()) / f
                                if not f_path.exists(): continue
                                tmp_old = Path(tempfile.gettempdir()) / f"orq_old_{f_path.name}"
                                res = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=get_project_dir(), capture_output=True)
                                if res.returncode == 0:
                                    tmp_old.write_bytes(res.stdout)
                                    _auto_open_in_cursor(["-d", str(tmp_old), str(f_path)], cwd=get_project_dir())
                                else:
                                    _auto_open_in_cursor([f], cwd=get_project_dir())
                        else:
                            console.print("[dim]Nenhum arquivo modificado nesta tarefa.[/dim]")
                    else:
                        console.print(f"[red]Tarefa #{tid} não encontrada ou não está pendente.[/red]")
                except ValueError:
                    console.print("[red]ID inválido.[/red]")
            else:
                console.print("[red]Uso: /diff [numero_tarefa][/red]")
                

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

        elif low in ("/design", "/open-design"):
            with console.status("[bold magenta]⏳ Verificando o Open Design (sobe sozinho se ainda não estiver de pé)...[/bold magenta]", spinner="dots"):
                resolved = open_design_resolve_project(config)
            if not resolved:
                console.print("[yellow]⚠ Não consegui subir o Open Design automaticamente.[/yellow]")
                console.print("[dim]Confira se `open_design.repo_path` está certo em ~/.orquestra.json, ou rode [bold]open-design-dev[/bold] manualmente num terminal e tente [bold]/design[/bold] de novo.[/dim]")
                continue
            url = f"{resolved['web_url']}/projects/{resolved['project_id']}" if resolved.get("web_url") else None
            if url:
                console.print(f"[bold magenta]🎨 Open Design aberto:[/bold magenta] {url}")
                if shutil.which("xdg-open"):
                    subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                save_chat_message("orquestrador", "assistant", f"[Open Design aberto para este projeto]: {url}")
            else:
                console.print(f"[yellow]Projeto do Open Design pronto (id {resolved['project_id']}), mas não achei a URL do painel web.[/yellow]")

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
            memory_context = _load_memory_context(get_project_id())
            prompt = build_orchestrator_prompt(config, history, memory_context)

            env = os.environ.copy()
            env_file = role.get("env_file")
            if env_file:
                env.update(load_env_file(env_file))

            # Até 2 tentativas: o `agy`/modelo às vezes volta com stdout vazio por
            # instabilidade momentânea (não é timeout nem erro de processo) — nesse
            # caso específico vale a pena tentar mais uma vez antes de desistir.
            # Diferente de timeout/erro de processo (aí não vale a pena retentar,
            # aborta direto — a LiveSection já mostrou o motivo ao fechar).
            clean_output = ""
            hard_fail = False
            for attempt in range(2):
                cmd, cmd_binary, cmd_ofile = _build_orchestrator_cmd(role, prompt)
                section = LiveSection(role_name.capitalize(), icon, color)
                section.open()
                text, ok = _run_orchestrator_stream(cmd, env, cmd_binary, section, timeout_s=150, output_file=cmd_ofile)

                if not ok:
                    hard_fail = True
                    break

                clean_output = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text or '').strip()
                if clean_output:
                    break

                if attempt == 0:
                    console.print(f"[dim]⚠ {icon} {role_name.capitalize()} voltou vazio na 1ª tentativa — tentando de novo...[/dim]")

            if hard_fail:
                console.print("[dim]Nada foi salvo no histórico. Tente perguntar de novo.[/dim]\n")
                continue

            if not clean_output:
                console.print(f"[bold red]⚠ {icon} {role_name.capitalize()} voltou sem resposta (2 tentativas) — provável instabilidade momentânea do modelo '{model}'.[/bold red]")
                console.print("[dim]Nada foi salvo no histórico. Tente perguntar de novo.[/dim]\n")
                continue

            # O streaming JSON já entrega texto limpo (campo estruturado, não stdout
            # bruto de terminal) — sem o "lixo visual" de ANSI que o `-p` síncrono
            # antigo podia trazer. `output` (usado no ramo sem delegação, abaixo)
            # e `clean_output` (usado no scan de <DELEGATE>/<PIPELINE>) são o mesmo
            # texto agora; mantidos como dois nomes só por compatibilidade com o
            # resto do bloco.
            output = clean_output

            # ── Extrai e exibe plano (tag <PLANO>) ─────────────────────────
            plano_match = PLANO_RE.search(clean_output)
            if plano_match:
                _last_plan = plano_match.group(1).strip()
                # Remove a tag <PLANO> do texto que será exibido no chat
                clean_output = PLANO_RE.sub('', clean_output).strip()
                output = clean_output
                # Mostra notificação limpa no chat e abre o visualizador automaticamente
                console.print("\n[bold cyan]\U0001f4cb Plano criado — abrindo visualizador...[/bold cyan]")
                console.print("[dim]  (use /plano para rever a qualquer momento)[/dim]\n")
                show_plan_viewer(_last_plan, config)
            # ───────────────────────────────────────────────────────

            # Busca blocos <PIPELINE> primeiro (cadeias sequenciais) e tira eles
            # do texto antes de procurar <DELEGATE> soltos — senão os DELEGATE
            # de dentro de um pipeline seriam contados de novo como paralelos.
            pipeline_steps_list = []
            for pm in PIPELINE_RE.finditer(clean_output):
                steps = [(m.group(1).lower(), m.group(2).strip()) for m in DELEGATE_RE.finditer(pm.group(1))]
                if steps:
                    pipeline_steps_list.append(steps)

            clean_output_no_pipeline = PIPELINE_RE.sub('', clean_output)
            output_no_pipeline = PIPELINE_RE.sub('', output)

            # Busca TODAS as tags de delegação soltas (paralelas)
            delegate_matches = list(DELEGATE_RE.finditer(clean_output_no_pipeline))
            chat_mode = _is_chat_mode(config)
            detail_level = _detail_level(config)

            if delegate_matches or pipeline_steps_list:
                # Só o texto ANTES da 1ª tag — o que vier depois de um
                # <DELEGATE>/<PIPELINE>/<PLANO> é alucinação de resultado (quem
                # produz resultado é o operário), então é descartado.
                _tag_m = re.search(r'<(?:PLANO|PIPELINE|DELEGATE)\b', output, re.IGNORECASE)
                user_output = (output[:_tag_m.start()] if _tag_m else output).strip()

                if not user_output:
                    # Fallback: garante uma frase de intenção mesmo se o LLM não escrever
                    # nada fora das tags — nunca dispara delegação em silêncio total.
                    intent_parts = []
                    if delegate_matches:
                        labels = []
                        for m in delegate_matches:
                            tag = m.group(1).lower()
                            if tag in ("open-design", "opendesign", "open_design"):
                                labels.append("Open Design")
                            else:
                                labels.append(_resolve_worker(config, tag)[2])
                        if len(labels) == 1:
                            intent_parts.append(f"Vou pedir para {labels[0]} cuidar disso.")
                        else:
                            intent_parts.append(f"Vou acionar {len(labels)} operários em paralelo: {', '.join(labels)}.")
                    for steps in pipeline_steps_list:
                        chain = " → ".join(_resolve_worker(config, tag)[2] for tag, _ in steps)
                        intent_parts.append(f"Vou rodar isso em pipeline: {chain}.")
                    user_output = " ".join(intent_parts)

                save_chat_message(role_name, "assistant", clean_output)
                if user_output:
                    if chat_mode:
                        _print_chat_bubble(role_name.capitalize(), icon, color, user_output)
                    else:
                        console.print(f"\n[bold {color}]{icon} {role_name.capitalize()}:[/bold {color}]\n{user_output}\n")
                    _auto_open_images(user_output)

                # Duas passadas: primeiro resolve e ABRE todas as LiveSection desta
                # rodada, uma atrás da outra, na thread principal — os cabeçalhos
                # aparecem juntos, como um bloco (efeito de "pilha" pedido no
                # paralelismo). Só depois disparamos as threads que efetivamente
                # rodam os workers. Open Design fica de fora deste agrupamento
                # (lifecycle próprio, tratado na Fase 3 da reforma).
                pending_workers = []
                for idx, match in enumerate(delegate_matches):
                    target_binary_tag = match.group(1).lower()
                    delegate_instruction = match.group(2).strip()

                    if target_binary_tag in ("open-design", "opendesign", "open_design"):
                        # open_design_run_task não passa por _run_worker_headless (é
                        # polling HTTP, não subprocess streaming) — mas usa a mesma
                        # LiveSection, com heartbeats durante o polling, pra nunca
                        # ficar em silêncio total enquanto gera a prévia.
                        od_section = LiveSection("Open Design", "🎨", "magenta")
                        od_section.open()

                        def _open_design_lifecycle(instruction, cfg, chat_mode, section):
                            task_id = create_task(instruction, "Open Design")
                            ok, text = open_design_run_task(cfg, instruction, section=section)
                            update_task_status(task_id, "✅ Concluído" if ok else "❌ Erro", text[:4000] if text else None)
                            _record_agent_performance(get_project_id(), "Open Design", _infer_task_kind(instruction), "success" if ok else "error")
                            if ok:
                                summary = f"[Resposta do Open Design]:\n{text[:4000]}"
                                save_chat_message("orquestrador", "assistant", summary)
                                if chat_mode:
                                    comment = _orchestrator_react(cfg, "Open Design", text) if text else ""
                                    comment, lesson = _extract_lesson(comment)
                                    if lesson:
                                        _store_lesson(get_project_id(), "lesson", lesson, source="Open Design")
                                    if comment:
                                        save_chat_message("orquestrador", "assistant", comment)
                                        orch_role = cfg.get("roles", {}).get("orquestrador", {})
                                        _print_chat_bubble(
                                            "Orquestra", orch_role.get("icon", "🎼"),
                                            orch_role.get("color", "cyan"), comment,
                                        )
                                    else:
                                        _print_chat_bubble("Open Design", "🎨", "magenta", text)
                                    _auto_open_links(text)
                                else:
                                    console.print(f"[magenta]{text}[/magenta]")
                                _auto_open_images(text)
                            else:
                                # A LiveSection já mostrou o erro ao fechar — só falta
                                # salvar o resumo no histórico do orquestrador.
                                save_chat_message("orquestrador", "assistant", f"[Erro do Open Design]: {text}")

                        threading.Thread(
                            target=_open_design_lifecycle,
                            args=(delegate_instruction, config, chat_mode, od_section),
                            daemon=True
                        ).start()
                        continue

                    # Resolve qual worker usar e monta o comando headless — headless
                    # (--print/-p), com streaming estruturado em tempo real e permissões
                    # liberadas. Sem TTY pra confirmar nada em modo headless — sem bypass,
                    # o processo travaria esperando uma aprovação que nunca chega.
                    worker, actual_binary, label, icon, color = _resolve_worker(config, target_binary_tag)
                    cmd, worker_env, output_file = _build_worker_cmd(worker, actual_binary, delegate_instruction)

                    section = LiveSection(label, icon, color)
                    section.open()
                    pending_workers.append((cmd, worker_env, output_file, label, icon, color, actual_binary, section, delegate_instruction, target_binary_tag))

                def _worker_lifecycle(w_cmd, w_env, section, lbl, ic, col, actual_binary, chat_mode, detail, task_kind, tag, config, output_file=None, delegate_instruction=""):
                    pre_snapshot = "\n".join(sorted(_git_status_paths(get_project_dir())))
                    task_id = create_task(delegate_instruction, lbl, pre_snapshot=pre_snapshot)
                    is_agy = actual_binary == "agy"
                    is_codex = actual_binary == "codex"
                    is_copilot = actual_binary == "copilot"
                    is_opencode = actual_binary == "opencode"
                    result = _run_worker_headless(
                        w_cmd, w_env, get_project_dir(), section, is_agy, detail=detail,
                        is_codex=is_codex, is_copilot=is_copilot, is_opencode=is_opencode, output_file=output_file,
                    )
                    final_text, is_error, error_detail, changes_summary = (
                        result.final_text, result.is_error, result.error_detail, result.changes_summary,
                    )

                    if is_error and ("429" in (error_detail or "") or "credit" in (error_detail or "").lower() or "quota" in (error_detail or "").lower()):
                        console.print(f"[bold yellow]⚠️ Failover Automático: {lbl} atingiu limite/erro. Tentando fallback...[/bold yellow]")
                        # Simple fallback: if claude->copilot, deepseek->claude, codex->copilot, agy->copilot, copilot->claude, opencode->copilot
                        fb_map = {"claude": "copilot", "deepseek": "claude", "codex": "copilot", "agy": "copilot", "copilot": "claude", "opencode": "copilot"}
                        fb_bin = fb_map.get(actual_binary, "copilot")
                        fb_worker = {"binary": fb_bin, "model": ""}
                        fb_cmd, fb_env, fb_out = _build_worker_cmd(fb_worker, fb_bin, delegate_instruction)
                        # LiveSection NOVA — a original já foi fechada por _run_worker_headless
                        fb_section = LiveSection(f"{lbl} → {fb_bin}", ic, col)
                        fb_section.open()
                        result = _run_worker_headless(
                            fb_cmd, fb_env, get_project_dir(), fb_section, fb_bin == "agy", detail=detail,
                            is_codex=(fb_bin=="codex"), is_copilot=(fb_bin=="copilot"),
                            is_opencode=(fb_bin=="opencode"), output_file=fb_out,
                        )
                        final_text, is_error, error_detail, changes_summary = (
                            result.final_text, result.is_error, result.error_detail, result.changes_summary,
                        )
                        if fb_out:
                            try:
                                os.unlink(fb_out)
                            except OSError:
                                pass

                    if output_file:
                        try:
                            os.unlink(output_file)
                        except OSError:
                            pass
                    
                    post_snapshot_set = _git_status_paths(get_project_dir())
                    pre_snapshot_set = set(pre_snapshot.split('\n')) if pre_snapshot else set()
                    modified_files = list(post_snapshot_set - pre_snapshot_set)
                    if modified_files and not is_error:
                        # GPS: descobre a primeira linha modificada de cada arquivo
                        for mf in modified_files[:3]:
                            mf_abs = str(Path(get_project_dir()) / mf)
                            first_line = 1
                            try:
                                diff_out = subprocess.run(
                                    ['git', 'diff', 'HEAD', '--', mf],
                                    cwd=get_project_dir(), capture_output=True, text=True
                                ).stdout
                                for dl in diff_out.splitlines():
                                    # Linhas de hunk: @@ -a,b +c,d @@ -> c é a linha nova
                                    if dl.startswith('@@'):
                                        import re as _re
                                        m = _re.search(r'\+(\d+)', dl)
                                        if m:
                                            first_line = int(m.group(1))
                                            break
                            except Exception:
                                pass
                            _auto_open_in_cursor_at(mf_abs, first_line)


                    if not is_error and changes_summary:
                        new_status = "⚠️ Aguardando Aprovação"
                        _run_copilot_watchdog(config)
                    else:
                        new_status = "❌ Erro" if is_error else "✅ Concluído"
                    update_task_status(task_id, new_status, final_text[:4000] if final_text else None)
                    _record_agent_performance(get_project_id(), lbl, task_kind, "error" if is_error else "success",
                                               result.cost_usd, result.tokens_in, result.tokens_out, result.duration_ms)

                    if is_error:
                        # A LiveSection já mostrou o erro e o detalhe ao fechar —
                        # só falta salvar o resumo no histórico do orquestrador.
                        detail_text = error_detail or "(sem detalhes de stderr)"
                        summary = f"[Erro do {lbl}]:\n{(final_text or '').strip()}\n{detail_text[-1500:]}".strip()
                        save_chat_message("orquestrador", "assistant", summary)
                        return

                    captured = final_text.strip()
                    if captured:
                        summary = f"[Resposta do {lbl}]:\n{captured[:4000]}"
                        save_chat_message("orquestrador", "assistant", summary)
                    if chat_mode:
                        comment = _orchestrator_react(config, lbl, captured) if captured else ""
                        comment, lesson = _extract_lesson(comment)
                        if lesson:
                            _store_lesson(get_project_id(), "lesson", lesson, source=lbl)
                        if comment:
                            save_chat_message("orquestrador", "assistant", comment)
                            orch_role = config.get("roles", {}).get("orquestrador", {})
                            _print_chat_bubble(
                                "Orquestra", orch_role.get("icon", "🎼"),
                                orch_role.get("color", "cyan"), comment,
                            )
                        else:
                            _print_chat_bubble(lbl, ic, col, captured or "(sem resposta de texto)")
                            console.print("[dim]Quer que eu ajuste mais alguma coisa?[/dim]")
                    else:
                        console.print(f"[bold green]✓ {lbl} terminou — resposta salva no contexto do Orquestra[/bold green]")

                # Só agora, com todos os cabeçalhos das seções já abertos juntos
                # (passo acima), disparamos as threads que efetivamente rodam
                # os workers — efeito de "pilha declarada de uma vez".
                for w_cmd, w_env, w_output_file, w_label, w_icon, w_color, w_binary, w_section, w_instruction, w_tag in pending_workers:
                    threading.Thread(
                        target=_worker_lifecycle,
                        args=(w_cmd, w_env, w_section, w_label, w_icon, w_color, w_binary, chat_mode, detail_level, _infer_task_kind(w_instruction), w_tag, config),
                        kwargs={"output_file": w_output_file, "delegate_instruction": w_instruction},
                        daemon=True
                    ).start()

                for p_idx, steps in enumerate(pipeline_steps_list, start=1):
                    chain_desc = " → ".join(_resolve_worker(config, tag)[2] for tag, _ in steps)
                    console.print(f"[dim]🔗 Pipeline {p_idx} iniciado: {chain_desc}[/dim]")

                    threading.Thread(
                        target=_run_pipeline,
                        args=(config, steps, chat_mode, detail_level),
                        daemon=True
                    ).start()

                console.print()
            else:
                save_chat_message(role_name, "assistant", clean_output)
                if chat_mode:
                    _print_chat_bubble(role_name.capitalize(), icon, color, output)
                else:
                    console.print(f"\n[bold {color}]{icon} {role_name.capitalize()}:[/bold {color}]\n{output}\n")
                _auto_open_images(output)

if __name__ == "__main__":
    main()
