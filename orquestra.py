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
import datetime
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
        },
        "codex": {
            "binary": "codex",
            "model": "",
            "icon": "🤖",
            "label": "Operário Codex",
            "description": "Abertura no painel lateral via Codex CLI (OpenAI)"
        }
    },
    "ui": {
        "mode": "tecnico"
    }
}

console = Console()

# Protege prints concorrentes vindos de threads de worker diferentes (ex.: dois
# <DELEGATE> paralelos escrevendo ao mesmo tempo) pra nunca quebrar uma linha
# no meio — sem isso, deltas de texto de dois operários podem se entrelaçar
# caractere a caractere na tela.
_console_lock = threading.RLock()


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

    # Garante que a seção ui existe (modo técnico/chat)
    if "ui" not in cfg:
        cfg["ui"] = DEFAULT_CONFIG["ui"]
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


def create_task(title: str, agent: str) -> int:
    pid = get_project_id()
    import datetime
    now = datetime.datetime.now().isoformat()
    with _memory_db_lock:
        conn = sqlite3.connect(MEMORY_DB_FILE)
        try:
            cursor = conn.execute(
                "INSERT INTO tasks (project_id, title, agent, status, result, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, title, agent, "⏳ Em execução", None, now, now)
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

def _infer_task_kind(instruction: str) -> str:
    """Heurística simples por palavra-chave, sem chamada de LLM e sem mexer no
    formato das tags DELEGATE/PIPELINE. 'geral' quando nada bate."""
    low = (instruction or "").lower()
    for kind, keywords in _TASK_KIND_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            return kind
    return "geral"


def _record_agent_performance(project_id: str, agent_tag: str, task_kind: str, outcome: str):
    """Chamada mecânica (sem LLM) logo após um worker/pipeline/Open Design
    terminar. Degrada silenciosamente em qualquer erro — isso é telemetria de
    aprendizado, nunca deve derrubar o turno do usuário por causa dela."""
    try:
        with _memory_db_lock:
            conn = sqlite3.connect(MEMORY_DB_FILE)
            try:
                conn.execute(
                    "INSERT INTO agent_performance (project_id, agent_tag, task_kind, outcome, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (project_id, agent_tag, task_kind, outcome, datetime.datetime.now().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
    except sqlite3.Error:
        pass


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
    return {line[3:].strip() for line in result.stdout.splitlines() if line.strip()}


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

_FAMILIA_CHOICES = [
    ("claude",   "💞 Claude Code            (claude) — Anthropic"),
    ("codex",    "🤖 Codex                  (codex)  — OpenAI"),
    ("deepseek", "💙 Claude-DeepSeek        (claude) — DeepSeek V4"),
    ("gemini",   "💚 Antigravity            (agy)    — Google"),
    ("copilot",  "🤖 Copilot                (copilot)— GitHub"),
]

_SLOT_LABELS = {
    "orquestrador": "Orquestrador",
    "implementador": "Implementador (Claude)",
    "arquiteto": "Arquiteto (DeepSeek)",
    "pesquisador": "Pesquisador (Copilot/Agy)",
    "designer": "Designer (Agy)",
    "analista": "Analista (Codex/GPT)",
}


def _apply_familia(target: dict, familia: str, modelo: str):
    """Aplica a família/modelo escolhidos num role/worker — qualquer um dos
    dois formatos (config['roles']['orquestrador'] ou config['workers'][tag])
    aceita qualquer uma das 4 famílias, não só a combinação original. Isso é
    o que permite trocar a 'gasolina' de qualquer operário se uma assinatura
    específica der problema, sem depender de reescrever config na mão."""
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
                ("orquestrador", f"🎼  Orquestrador (Chefe)          [{_modelo_label(orch)}]"),
                ("implementador",f"🔨  Implementador (Código)        [{_modelo_label(workers.get('implementador', {}))}]"),
                ("arquiteto",    f"🏛️  Arquiteto (Revisão/Segurança) [{_modelo_label(workers.get('arquiteto', {}))}]"),
                ("pesquisador",  f"🔎  Pesquisador (Busca/Docs)      [{_modelo_label(workers.get('pesquisador', {}))}]"),
                ("designer",     f"🎨  Designer (Visual/UI)          [{_modelo_label(workers.get('designer', {}))}]"),
                ("analista",     f"🧠  Analista (Solucionador/Dados) [{_modelo_label(workers.get('analista', {}))}]"),
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
            familia = radiolist_dialog(
                title="\n  🎼  Orquestra — Trocar Inteligência",
                text=f"Passo 2 de 3  ──  Qual provedor para o {slot_label}?",
                values=_FAMILIA_CHOICES,
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
            if familia == "gemini":
                model_choices = _fetch_agy_models()
            elif familia == "claude":
                model_choices = _fetch_claude_models()
            elif familia == "deepseek":
                model_choices = _fetch_deepseek_models()
            elif familia == "copilot":
                model_choices = _fetch_copilot_models()
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


def _detail_level(config: dict) -> str:
    """minimal | normal | full — nível de detalhe do streaming ao vivo de um
    operário. Substitui o antigo binário quiet/chat_mode: em NENHUM nível o
    operário fica em silêncio total (minimal ainda mostra o texto sendo
    gerado ao vivo, só omite tool calls). `ui.detail`, se presente, manda;
    senão mapeia o `ui.mode` legado pra manter compatibilidade com configs
    já existentes (chat→minimal, tecnico→full)."""
    ui = config.get("ui", {})
    detail = ui.get("detail")
    if detail in ("minimal", "normal", "full"):
        return detail
    mode = ui.get("mode", "tecnico")
    if mode == "chat":
        return "minimal"
    if mode == "tecnico":
        return "full"
    return "normal"


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
        self._line_open = False
        self._start = None

    def open(self):
        self._start = time.time()
        with _console_lock:
            console.print(f"[{self.color}]⏺[/{self.color}] [bold {self.color}]{self.icon} {self.label}[/bold {self.color}]")

    def end_line(self):
        """Fecha uma linha de streaming em andamento (delta sem \\n no final),
        se houver uma aberta. Idempotente."""
        with _console_lock:
            if self._line_open:
                console.print()
                self._line_open = False

    def write(self, text: str, end: str = "\n", prefix: bool = True):
        """Escreve texto vindo do operário (fala/delta), indentado sob o
        marcador ⏺ da seção — sem repetir ícone/label a cada parágrafo (estilo
        Claude Code/OpenCode: um cabeçalho, depois o texto flui liso, sem
        caixa). Com end="", acumula na mesma linha (streaming token-a-token)."""
        if not text:
            return
        with _console_lock:
            if prefix and not self._line_open:
                console.print("  ", end="")
            console.print(text, end=end)
            self._line_open = (end == "")

    def tool_call(self, text: str):
        """Anúncio de 1 linha pra uma chamada de ferramenta/passo (nível
        normal/full) ou erro (sempre, em qualquer nível — erro nunca é
        escondido). Formato "⎿" combina com o marcador ⏺ do open()."""
        self.end_line()
        with _console_lock:
            console.print(f"  [dim]⎿ {text}[/dim]")

    def close(self, ok: bool, error_detail: str = "", changes_summary: str = ""):
        elapsed = time.time() - self._start if self._start else 0.0
        self.end_line()
        with _console_lock:
            if ok:
                console.print(f"  [dim green]⎿ ✓ concluído em {elapsed:.0f}s[/dim green]")
            else:
                console.print(f"  [dim red]⎿ ✗ erro[/dim red]")
                if error_detail:
                    console.print(f"    [dim]{error_detail[-1500:]}[/dim]")
            if changes_summary:
                console.print(f"  [dim]⎿ 📝 {changes_summary}[/dim]")
        console.print()


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


def _narrate_copilot_line(line: str, section: "LiveSection", state: dict, detail: str = "normal"):
    try:
        data = json.loads(line)
        if "type" in data:
            if data["type"] == "assistant.message_delta":
                delta = data.get("data", {}).get("delta", "")
                if delta:
                    state["final_text"] += delta
                    section.append_output(delta)
            elif data["type"] == "assistant.message":
                # Fallback if there is no delta but full text
                pass
            elif data["type"] == "error":
                state["is_error"] = True
    except json.JSONDecodeError:
        pass

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


def _build_orchestrator_cmd(role: dict, prompt: str) -> tuple:
    """Monta o comando de streaming pro PRÓPRIO orquestrador (decisão inicial do
    turno e _orchestrator_react) — mesmo padrão de _build_worker_cmd, mas SEM
    flags de escrita/permissão: o orquestrador só decide e conversa, nunca mexe
    em arquivos (isso é sempre delegado a um operário via <DELEGATE>/<PIPELINE>).
    Retorna (cmd, binary)."""
    binary = role.get("binary", "agy")
    model = role.get("model")

    if binary == "agy":
        cmd = ["agy", "-p", prompt, "--output-format", "stream-json"]
        if model:
            cmd.extend(["--model", model])
    else:
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]

    return cmd, binary


def _capture_orchestrator_result(line: str, is_agy: bool, state: dict):
    """Extrai só final_text/is_error do stream-json de uma chamada do próprio
    orquestrador — sem narrar nada ao vivo (o heartbeat de
    _run_orchestrator_stream já cobre a visibilidade; o texto completo é
    mostrado depois, formatado, por quem chamou — narrar aqui repetiria a
    mesma fala duas vezes na tela)."""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return
    if is_agy:
        if d.get("event") == "result":
            res = d.get("result", {})
            state["final_text"] = res.get("response", "") or ""
            state["is_error"] = res.get("status") != "SUCCESS"
    else:
        if d.get("type") == "result":
            state["final_text"] = d.get("result", "") or ""
            state["is_error"] = bool(d.get("is_error"))


def _run_orchestrator_stream(cmd: list, env: dict, binary: str, section: "LiveSection", timeout_s: int) -> tuple:
    """Roda uma chamada do PRÓPRIO orquestrador (decisão inicial do turno ou
    _orchestrator_react) com heartbeat ao vivo na LiveSection, em vez do
    silêncio total + spinner genérico de antes da reforma — e com um timeout
    de verdade (mata o processo se passar de timeout_s), a mesma garantia que
    subprocess.run(timeout=...) dava antes. Retorna (final_text, ok)."""
    state = {"final_text": "", "is_error": False}
    is_agy = (binary == "agy")

    try:
        proc = subprocess.Popen(
            cmd, env=env,
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
                _capture_orchestrator_result(line, is_agy, state)
    finally:
        timer.cancel()
        stop_heartbeat.set()

    proc.wait()

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

    cmd, binary = _build_orchestrator_cmd(role, prompt)

    env = os.environ.copy()
    env_file = role.get("env_file")
    if env_file:
        env.update(load_env_file(env_file))

    section = LiveSection("Orquestra", role.get("icon", "🎼"), role.get("color", "cyan"))
    section.open()
    text, ok = _run_orchestrator_stream(cmd, env, binary, section, timeout_s=100)
    if not ok:
        return ""

    clean = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text or '')
    return clean.strip()


def _run_worker_headless(cmd: list, env: dict, cwd: str, section: "LiveSection", is_agy: bool,
                          detail: str = "normal", is_codex: bool = False, is_copilot: bool = False, output_file: str | None = None):
    """Roda um operário headless, narrando eventos ao vivo na LiveSection — em
    NENHUM nível de detalhe fica em silêncio total (ver _detail_level). Abre e
    fecha a seção aqui mesmo, já com o resumo de erro embutido no fechamento.
    Retorna (final_text, is_error, error_detail).

    output_file: quando informado (Codex), depois que o processo termina o
    conteúdo desse arquivo (-o/--output-last-message) sobrescreve o final_text
    capturado via streaming — é a fonte mais confiável, já que o parser do
    --json do Codex é best-effort."""
    state = {"final_text": "", "is_error": False}
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
    if files_before is not None:
        files_after = _git_dirty_files(cwd)
        touched = (files_after or set()) - files_before
        if touched:
            changes_summary = f"Arquivos alterados: {', '.join(sorted(touched))}"
            try:
                diff_stat = subprocess.run(
                    ["git", "diff", "--stat"], cwd=cwd, capture_output=True, text=True, timeout=10,
                )
                if diff_stat.returncode == 0 and diff_stat.stdout.strip():
                    changes_summary += f"\n{diff_stat.stdout.strip()[:800]}"
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

    section.close(ok=not state["is_error"], error_detail=error_detail, changes_summary=changes_summary)

    return state["final_text"], state["is_error"], error_detail, changes_summary


DELEGATE_RE = re.compile(r'<DELEGATE\s+binary="([^"]+)">(.*?)</DELEGATE>', re.DOTALL | re.IGNORECASE)
PIPELINE_RE = re.compile(r'<PIPELINE>(.*?)</PIPELINE>', re.DOTALL | re.IGNORECASE)


# Defaults de label/ícone/cor/binary por tag histórica — usados só quando o
# config não sobrescreve nada. Tag desconhecida cai no default do "claude".
_WORKER_TAG_DEFAULTS = {
    "implementador": ("claude", "Implementador", "🔨", "cyan"),
    "arquiteto":   ("claude", "Arquiteto", "🏛️", "blue"),
    "pesquisador": ("copilot", "Pesquisador", "🔎", "yellow"),
    "designer":    ("agy", "Designer", "🎨", "green"),
    "analista":    ("codex", "Analista", "🧠", "magenta"),
}


def _resolve_worker(config: dict, target_binary_tag: str) -> tuple:
    """Resolve qual operário real usar a partir da tag do <DELEGATE>/<PIPELINE>.
    Retorna (worker_config, actual_binary, label, icon, color).

    IMPORTANTE: o `binary`/`label`/`icon`/`color` salvos em config["workers"][tag]
    SEMPRE têm prioridade sobre o default da tag — isso é o que faz o menu
    `/agentes trocar` conseguir trocar a "família" (Gemini/Claude/DeepSeek/
    Codex) de QUALQUER operário, inclusive os 3 históricos (claude/agy/
    deepseek). Antes desta correção, esses 3 ignoravam completamente o que
    estava salvo em config e sempre usavam o binary/label fixo no código —
    trocar a família desses 3 no menu não tinha efeito nenhum na prática."""
    workers = config.get("workers", {})
    tag = target_binary_tag.lower()

    worker = workers[tag] if tag in workers else workers.get("implementador", {})
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


def _build_worker_cmd(worker: dict, actual_binary: str, instruction: str) -> tuple:
    """Monta o comando headless e o env pra rodar um operário — mesma lógica pra
    chamada solta (<DELEGATE>) ou dentro de um <PIPELINE>. Retorna
    (cmd, worker_env, output_file) — output_file é None pra claude/agy (o texto
    final vem do streaming) e um caminho de arquivo temporário pro codex (a CLI
    dele não segue o padrão -p/--print + stream-json dos outros dois; usa
    subcomando `exec` + -o/--output-last-message pra escrever a resposta final
    direto num arquivo, mais confiável que casar o evento certo no --json dele)."""
    worker_model = worker.get("model", "")
    worker_env_file = worker.get("env_file", "")
    output_file = None

    if actual_binary == "agy":
        cmd = ["agy", "-p", instruction,
               "--output-format", "stream-json",
               "--dangerously-skip-permissions"]
        if worker_model:
            cmd.extend(["--model", worker_model])
    elif actual_binary == "codex":
        fd, output_file = tempfile.mkstemp(prefix="orquestra_codex_", suffix=".txt")
        os.close(fd)
        cmd = ["codex", "exec", instruction,
               "--json", "--sandbox", "workspace-write",
               "--skip-git-repo-check",
               "-o", output_file]
        if worker_model:
            cmd.extend(["-m", worker_model])
    elif actual_binary == "copilot":
        cmd = ["gh", "copilot", "-p", instruction,
               "--output-format", "json",
               "--no-ask-user"]
        if worker_model:
            cmd.extend(["--model", worker_model])
    else:
        cmd = ["claude", "-p", instruction,
               "--output-format", "stream-json", "--verbose",
               "--permission-mode", "bypassPermissions"]
        if worker_model:
            cmd.extend(["--model", worker_model])

    worker_env = os.environ.copy()
    if worker_env_file and Path(os.path.expanduser(worker_env_file)).exists():
        worker_env.update(load_env_file(worker_env_file))

    return cmd, worker_env, output_file


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
        
        task_id = create_task(instruction, label)
        final_text, is_error, error_detail, changes_summary = _run_worker_headless(
            cmd, worker_env, get_project_dir(), section,
            actual_binary == "agy", detail=detail,
            is_codex=(actual_binary == "codex"), is_copilot=(actual_binary == "copilot"), output_file=output_file,
        )
        if output_file:
            try:
                os.unlink(output_file)
            except OSError:
                pass

        if is_error:
            console.print(f"[bold red]⚠ Pipeline parou na etapa {idx} ({label}) por erro.[/bold red]")
            detail_text = error_detail or "(sem detalhes de stderr)"
            summary = f"[Pipeline interrompido na etapa {idx}/{total} — erro do {label}]:\n{detail_text[-1500:]}"
            save_chat_message("orquestrador", "assistant", summary)
            _record_agent_performance(get_project_id(), label, _infer_task_kind(instruction), "error")
            update_task_status(task_id, "❌ Erro", final_text[:4000] if final_text else None)
            return

        if not is_error and changes_summary:
            new_status = "⚠️ Aguardando Aprovação"
        else:
            new_status = "✅ Concluído"
        update_task_status(task_id, new_status, final_text[:4000] if final_text else None)

        captured = final_text.strip()
        step_summaries.append((label, captured))
        save_chat_message("orquestrador", "assistant", f"[Pipeline etapa {idx}/{total} — {label}]:\n{captured[:4000]}")
        _record_agent_performance(get_project_id(), label, _infer_task_kind(instruction), "success")
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
    config = load_config()
    _init_memory_db()

    print_banner()

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

        low = raw.lower()

        if low in ("/sair", "/exit", "/quit", "exit", "quit"):
            handle_safe_exit(config)

        elif low in ("/ajuda", "/help", "/?"):
            print_help()
            
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
                        subprocess.run(["git", "add", "."], cwd=get_project_dir(), capture_output=True)
                        subprocess.run(["git", "commit", "-m", f"Orquestra: Tarefa #{tid} aprovada"], cwd=get_project_dir(), capture_output=True)
                        update_task_status(tid, "✅ Aprovado (Commitado)", task['result'])
                        console.print(f"[bold green]Tarefa #{tid} aprovada e salva no Git![/bold green]")
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
            memory_block = f"Fatos e lições conhecidos (memória de longo prazo, não é o histórico da conversa):\n{memory_context}\n\n" if memory_context else ""

            context_str = (
                f"Você é o Orquestra, o assistente inteligente do projeto '{projeto}'.\n"
                f"Hoje é {agora}. Você está no terminal do usuário, na pasta '{get_project_dir()}'.\n"
                "Seu objetivo é conversar naturalmente, lembrar o contexto, tirar dúvidas e gerenciar o projeto.\n"
                "REGRA DE OURO: Você NÃO DEVE inventar código, alucinar ou chutar o que não sabe com certeza.\n"
                "Quando precisar executar uma tarefa prática (ler/escrever código, rodar comandos, acessar web), DELEGUE para um operário.\n"
                "IMPORTANTE: antes de qualquer bloco <DELEGATE>/<PIPELINE>, escreva sempre 1-2 frases em "
                "português explicando o que você vai fazer e por quê. Nunca emita blocos de delegação sem "
                "esse texto de intenção antes deles — é isso que o usuário vê enquanto o operário trabalha.\n"
                "Para delegar, inclua ao final da resposta um ou mais blocos:\n"
                "<DELEGATE binary=\"implementador\">\n(Instruções completas para o operário)\n</DELEGATE>\n"
                "Use binary=\"implementador\" para codificação e refatoração pesada, "
                "binary=\"pesquisador\" para pesquisa rápida e varredura de diretórios, "
                "binary=\"arquiteto\" para revisão, arquitetura e segurança da informação, "
                "binary=\"analista\" para problemas lógicos profundos e troubleshooting complexo.\n"
                "Use binary=\"open-design\" para gerar uma PRÉVIA VISUAL de verdade — renderizada e navegável, "
                "não uma descrição em texto nem uma imagem estática. Ele lê o projeto de verdade na pasta atual "
                "(design system, componentes, paleta reais) e devolve uma prévia fiel ao visual já existente.\n\n"
                "REGRA OBRIGATÓRIA DE PRÉVIA: sempre que o pedido envolver criar ou alterar QUALQUER coisa "
                "visual/de interface (tela nova, componente, layout, botão, cor, animação, redesenho, etc.), "
                "você NÃO PODE delegar a implementação real (binary=\"claude\" ou binary=\"agy\" escrevendo/"
                "alterando arquivos de verdade) diretamente. Primeiro delegue para binary=\"open-design\" pedindo "
                "a prévia dessa mudança específica, mostre o resultado ao usuário, e só peça a implementação real "
                "depois que o usuário aprovar explicitamente essa prévia numa mensagem seguinte. Só pule essa "
                "etapa se o usuário disser claramente que já aprovou / quer pular a prévia, ou se a tarefa não "
                "tiver nenhum componente visual (ex: lógica de backend pura, bug de dado, configuração).\n\n"
                "Você pode convocar múltiplos operários de uma vez gerando múltiplos blocos <DELEGATE> — eles "
                "rodam TODOS AO MESMO TEMPO, em paralelo, cada um recebendo só a instrução que você escreveu "
                "pra ele. Use isso quando as tarefas forem independentes entre si.\n\n"
                "Quando o usuário pedir uma CADEIA/PIPELINE — um operário analisa algo, o resultado dele vai "
                "para o próximo analisar, e assim por diante até um resultado final (ex.: \"analisa essa ideia "
                "com o Antigravity, depois manda pro DeepSeek dar a visão dele, depois o Claude Code faz a "
                "análise final\") — NÃO use vários <DELEGATE> soltos (eles rodariam em paralelo, sem um ver o "
                "resultado do outro). Em vez disso, envolva os blocos <DELEGATE>, na ORDEM em que devem rodar, "
                "dentro de um único bloco <PIPELINE>:\n"
                "<PIPELINE>\n"
                "<DELEGATE binary=\"pesquisador\">(o que a 1ª etapa deve analisar)</DELEGATE>\n"
                "<DELEGATE binary=\"arquiteto\">(o que a 2ª etapa deve revisar)</DELEGATE>\n"
                "<DELEGATE binary=\"implementador\">(o que a 3ª etapa deve implementar)</DELEGATE>\n"
                "</PIPELINE>\n"
                "Cada etapa roda só depois que a anterior termina, e recebe automaticamente o resultado da "
                "etapa anterior como contexto — você não precisa (nem deve) copiar esse resultado na instrução, "
                "só descreva o que aquela etapa específica deve fazer com o que recebeu. Ao final da última "
                "etapa você mesmo vai comentar o resultado consolidado pro usuário revisar e decidir se "
                "implementa. binary=\"open-design\" ainda não é suportado dentro de <PIPELINE>. Você pode "
                "emitir <PIPELINE> e <DELEGATE> soltos na mesma resposta, se fizer sentido.\n\n"
                f"{memory_block}"
                "Histórico da Conversa:\n"
            )
            for msg in history[-20:]:
                prefix = "Usuário" if msg["role"] == "user" else "Orquestra"
                context_str += f"{prefix}: {msg['content']}\n"
            
            prompt = f"{context_str}\n\nResponda ao usuário agora:"

            cmd, cmd_binary = _build_orchestrator_cmd(role, prompt)

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
                section = LiveSection(role_name.capitalize(), icon, color)
                section.open()
                text, ok = _run_orchestrator_stream(cmd, env, cmd_binary, section, timeout_s=150)

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
            chat_mode = config.get("ui", {}).get("mode") == "chat"
            detail_level = _detail_level(config)

            if delegate_matches or pipeline_steps_list:
                # Exibe a resposta removendo TODAS as tags para o usuário ver
                user_output = re.sub(r'<DELEGATE.*?</DELEGATE>', '', output_no_pipeline, flags=re.DOTALL | re.IGNORECASE).strip()

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
                    task_id = create_task(delegate_instruction, lbl)
                    is_agy = actual_binary == "agy"
                    is_codex = actual_binary == "codex"
                    is_copilot = actual_binary == "copilot"
                    final_text, is_error, error_detail, changes_summary = _run_worker_headless(
                        w_cmd, w_env, get_project_dir(), section, is_agy, detail=detail,
                        is_codex=is_codex, is_copilot=is_copilot, output_file=output_file,
                    )
                    
                    if is_error and ("429" in (error_detail or "") or "credit" in (error_detail or "").lower() or "quota" in (error_detail or "").lower()):
                        console.print(f"[bold yellow]⚠️ Failover Automático: {lbl} atingiu limite/erro. Tentando fallback...[/bold yellow]")
                        # Simple fallback: if claude->copilot, deepseek->claude, codex->copilot, agy->copilot, copilot->claude
                        fb_map = {"claude": "copilot", "deepseek": "claude", "codex": "copilot", "agy": "copilot", "copilot": "claude"}
                        fb_bin = fb_map.get(actual_binary, "copilot")
                        fb_worker = {"binary": fb_bin, "model": ""}
                        fb_cmd, fb_env, fb_out = _build_worker_cmd(fb_worker, fb_bin, delegate_instruction)
                        final_text, is_error, error_detail, changes_summary = _run_worker_headless(
                            fb_cmd, fb_env, get_project_dir(), section, fb_bin == "agy", detail=detail,
                            is_codex=(fb_bin=="codex"), is_copilot=(fb_bin=="copilot"), output_file=fb_out,
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
                    
                    
                    if not is_error and changes_summary:
                        new_status = "⚠️ Aguardando Aprovação"
                    else:
                        new_status = "❌ Erro" if is_error else "✅ Concluído"
                    update_task_status(task_id, new_status, final_text[:4000] if final_text else None)
                    _record_agent_performance(get_project_id(), lbl, task_kind, "error" if is_error else "success")

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
                for w_cmd, w_env, w_output_file, w_label, w_icon, w_color, w_binary, w_section, w_instruction in pending_workers:
                    threading.Thread(
                        target=_worker_lifecycle,
                        args=(w_cmd, w_env, w_section, w_label, w_icon, w_color, w_binary == "agy", chat_mode, detail_level, _infer_task_kind(w_instruction)),
                        kwargs={"is_codex": w_binary == "codex", "is_copilot": w_binary == "copilot", "output_file": w_output_file, "delegate_instruction": w_instruction},
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
