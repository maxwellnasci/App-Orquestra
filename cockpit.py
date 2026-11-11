#!/usr/bin/env python3
"""cockpit — uma tela (tmux), uma aba por agente, cada um com o login dele.

    cockpit            escolhe um projeto em ~/Documentos/Kali Linux e abre
    cockpit <pasta>    abre direto nessa pasta (ex.: cockpit .)
    cockpit passar ... (interno) menu do prefixo+P: passa contexto entre abas

Abas: 1 claude · 2 antigravity · 3 testes · 4 codex · 5 deepseek · 6 opencode
(aba de agente cujo binário não existe é pulada e avisada). Alt+N troca de aba.
prefixo+P abre o menu "passar contexto": cola o diff do git ou a tela da aba
atual noutro agente, SEM apertar Enter — você revisa e envia.

Sem intermediário: você fala direto com cada CLI, como nos apps separados.
Pra mudar as abas, crie ~/.cockpit.json: {"janelas": [{"nome": "...",
"binario": "...", "cmd": "..."}]}. Só usa a biblioteca padrão.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
PROJECTS_ROOT = Path(os.environ.get("COCKPIT_PROJECTS_ROOT", HOME / "Documentos" / "Kali Linux"))
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "cockpit"
DEEPSEEK_ENV = HOME / ".claude-deepseek-env"
MAX_INLINE_LINES = 300
SCREEN_LINES = 200

DEFAULT_WINDOWS = [
    {"nome": "claude", "binario": "claude", "cmd": "claude"},
    {"nome": "antigravity", "binario": "agy", "cmd": "agy"},
    {"nome": "testes", "binario": None, "cmd": ""},
    {"nome": "codex", "binario": "codex", "cmd": "codex"},
    # O env file já define base URL, chave e modelo; nunca é impresso.
    {"nome": "deepseek", "binario": "claude", "precisa": str(DEEPSEEK_ENV),
     "cmd": f"source {shlex.quote(str(DEEPSEEK_ENV))} && claude"},
    {"nome": "opencode", "binario": "opencode", "cmd": "opencode"},
]


# ---------- partes puras (testadas em test_cockpit.py) ----------

def session_name(project: str) -> str:
    """Mesmo nome do cockpit antigo (tr '.: ' '___')."""
    return project.replace(".", "_").replace(":", "_").replace(" ", "_")


def load_windows() -> list[dict]:
    cfg = Path(os.environ.get("COCKPIT_CONFIG", HOME / ".cockpit.json"))
    if cfg.exists():
        return json.loads(cfg.read_text())["janelas"]
    return DEFAULT_WINDOWS


def plan_windows(windows, which=shutil.which, exists=os.path.exists):
    """Separa as abas que dá pra abrir das que faltam algo (com o motivo)."""
    ativas, puladas = [], []
    for w in windows:
        if w.get("binario") and not which(w["binario"]):
            puladas.append((w["nome"], f"'{w['binario']}' não está no PATH"))
        elif w.get("precisa") and not exists(os.path.expanduser(w["precisa"])):
            puladas.append((w["nome"], f"falta {w['precisa']}"))
        else:
            ativas.append(w)
    return ativas, puladas


def launch_line(window: dict, venv: bool) -> str:
    parts = (["source .venv/bin/activate"] if venv else []) + ([window["cmd"]] if window.get("cmd") else [])
    return " && ".join(parts)


def wrap_context(kind: str, origem: str, body: str, lang: str = "") -> str:
    head = f"[Contexto passado pelo cockpit — {kind}, vindo da aba '{origem}']"
    return f"{head}\n```{lang}\n{body.rstrip()}\n```"


def pointer_context(kind: str, origem: str, n_lines: int, path: Path) -> str:
    return (f"[Contexto passado pelo cockpit — {kind}, vindo da aba '{origem}', "
            f"{n_lines} linhas — grande demais pra colar]\nLeia o arquivo: {path}")


def git_context(path: str) -> tuple[str, str]:
    """(texto, erro). Falha alto: sem repo ou sem mudança = erro, nada é colado."""
    def git(*a):
        return subprocess.run(["git", "-C", path, *a], capture_output=True, text=True)
    if git("rev-parse", "--show-toplevel").returncode != 0:
        return "", f"'{path}' não é um repositório git — nada foi colado."
    status = git("status", "--short").stdout.strip()
    if not status:
        return "", "Nenhuma mudança no git (status limpo) — nada foi colado."
    has_head = git("rev-parse", "--verify", "-q", "HEAD").returncode == 0
    diff = git("diff", "HEAD").stdout if has_head else git("diff").stdout
    text = f"$ git status --short\n{status}\n\n$ git diff {'HEAD' if has_head else ''}\n{diff}".rstrip()
    return text, ""


# ---------- tmux ----------

def tmux(*args, capture=False, check=True, input_text=None):
    base = ["tmux"]
    sock = os.environ.get("COCKPIT_TMUX_SOCKET")  # só pros testes (servidor isolado)
    if sock:
        base += ["-L", sock, "-f", "/dev/null"]
    r = subprocess.run(base + list(args), capture_output=capture, text=True, input=input_text)
    if check and r.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)} falhou: {(r.stderr or '').strip()}")
    return r.stdout if capture else r


def has_session(s: str) -> bool:
    base = ["tmux"] + (["-L", os.environ["COCKPIT_TMUX_SOCKET"]] if os.environ.get("COCKPIT_TMUX_SOCKET") else [])
    return subprocess.run(base + ["has-session", "-t", f"={s}"], capture_output=True).returncode == 0


def create_session(project_dir: Path, s: str) -> list:
    """Cria a sessão com uma aba por agente. Devolve as abas puladas."""
    ativas, puladas = plan_windows(load_windows())
    venv = (project_dir / ".venv" / "bin" / "activate").exists()
    for i, w in enumerate(ativas):
        if i == 0:
            tmux("new-session", "-d", "-s", s, "-n", w["nome"], "-c", str(project_dir))
        else:
            tmux("new-window", "-t", f"={s}:", "-n", w["nome"], "-c", str(project_dir))
        line = launch_line(w, venv)
        if line:
            tmux("send-keys", "-t", f"={s}:{w['nome']}", line, "Enter")
    tmux("select-window", "-t", f"={s}:{ativas[0]['nome']}")
    configure(s)
    return puladas


def configure(s: str) -> None:
    script = shlex.quote(str(Path(__file__).resolve()))
    py = shlex.quote(sys.executable)
    tmux("set-option", "-t", f"={s}:", "status-right", " prefixo+P: passar contexto · Alt+N: aba ")
    # Binds são globais no servidor tmux (reaplicados a cada cockpit, não tocam o ~/.tmux.conf).
    for n in range(4, 10):
        tmux("bind-key", "-n", f"M-{n}", "select-window", "-t", f":={n}")
    # display-popup NÃO expande #{...} no comando (testado no tmux 3.7c); run-shell expande.
    # Então o atalho chama o próprio cockpit, que abre o popup sem aspas aninhadas.
    tmux("bind-key", "P", "run-shell", "-b",
         f"{py} {script} popup '#{{socket_path}}' '#{{client_name}}' '#{{session_name}}' '#{{pane_id}}'")


def open_popup(sock: str, client: str, session: str, pane: str) -> int:
    inner = " ".join(shlex.quote(a) for a in [sys.executable, str(Path(__file__).resolve()), "passar", session, pane])
    return subprocess.run(["tmux", "-S", sock, "display-popup", "-c", client, "-t", pane,
                           "-E", "-w", "80%", "-h", "60%", inner]).returncode


# ---------- passar contexto (roda dentro do popup) ----------

def build_payload(kind: str, origem: str, body: str, lang: str, session: str) -> str:
    """Texto a colar. Grande demais vira arquivo em ~/.cache/cockpit (nunca na pasta do projeto)."""
    payload = wrap_context(kind, origem, body, lang)
    n = payload.count("\n") + 1
    if n <= MAX_INLINE_LINES:
        return payload
    d = CACHE_DIR / session
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"contexto-{time.strftime('%Y%m%d-%H%M%S')}.md"
    f.write_text(payload + "\n")
    return pointer_context(kind, origem, n, f)


def deliver(payload: str, target_pane: str) -> None:
    """Cola com bracketed paste (-p) e SEM Enter: o agente não envia sozinho."""
    tmux("load-buffer", "-b", "cockpit-ctx", "-", input_text=payload)
    tmux("paste-buffer", "-p", "-d", "-b", "cockpit-ctx", "-t", target_pane)


def passar(session: str, src_pane: str, ask=input) -> int:
    src_name, src_path = tmux("display-message", "-p", "-t", src_pane,
                              "#{window_name}\t#{pane_current_path}", capture=True).strip().split("\t")
    rows = [r.split("\t") for r in tmux("list-windows", "-t", f"={session}", "-F",
                                        "#{window_name}\t#{pane_id}", capture=True).splitlines()]
    alvos = [(n, p) for n, p in rows if p != src_pane]
    print(f"Passar contexto da aba '{src_name}'\n  1) diff do git ({src_path})\n"
          f"  2) últimas {SCREEN_LINES} linhas da tela desta aba\n  q) cancelar")
    o = ask("> ").strip()
    if o == "1":
        body, err = git_context(src_path)
        kind, lang = "diff do git", "diff"
    elif o == "2":
        body = tmux("capture-pane", "-p", "-J", "-S", f"-{SCREEN_LINES}", "-t", src_pane, capture=True)
        err = "" if body.strip() else "A tela desta aba está vazia — nada foi colado."
        kind, lang = "tela da aba", ""
    else:
        return 0
    if err:
        print(f"\n⚠ {err}")
        ask("Enter pra fechar")
        return 1
    print("\nPra qual aba?")
    for i, (n, _) in enumerate(alvos, 1):
        print(f"  {i}) {n}")
    o = ask("> ").strip()
    if not o.isdigit() or not 1 <= int(o) <= len(alvos):
        return 0
    nome, pane = alvos[int(o) - 1]
    final = build_payload(kind, src_name, body, lang, session)
    deliver(final, pane)
    tmux("select-window", "-t", pane)
    print(f"\n✓ Colado em '{nome}' ({final.count(chr(10)) + 1} linhas), SEM Enter — revise e envie.")
    time.sleep(1.5)
    return 0


# ---------- entrada ----------

def choose_project() -> Path | None:
    projetos = sorted((p.name for p in PROJECTS_ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")),
                      key=str.lower)
    print(f"Projetos em {PROJECTS_ROOT}:")
    for i, n in enumerate(projetos, 1):
        print(f"  {i:2}) {'●' if has_session(session_name(n)) else ' '} {n}")
    print("   n) novo projeto    q) sair      (● = já aberto)")
    o = input("> ").strip()
    if o in ("", "q"):
        return None
    if o.lower() == "n":
        nome = input("Nome do novo projeto: ").strip()
        if not nome:
            return None
        d = PROJECTS_ROOT / nome
        d.mkdir(parents=True, exist_ok=True)
        print("Criando .venv...")
        subprocess.run([sys.executable, "-m", "venv", str(d / ".venv")], check=False)
        return d
    if o.isdigit() and 1 <= int(o) <= len(projetos):
        return PROJECTS_ROOT / projetos[int(o) - 1]
    print(f"Opção inválida: {o}")
    return None


def main(argv: list[str]) -> int:
    if argv[:1] == ["passar"] and len(argv) == 3:
        return passar(argv[1], argv[2])
    if argv[:1] == ["popup"] and len(argv) == 5:
        return open_popup(*argv[1:])
    if argv[:1] in (["-h"], ["--help"]):
        print(__doc__)
        return 0
    project = Path(argv[0]).expanduser().resolve() if argv else choose_project()
    if project is None:
        return 0
    if not project.is_dir():
        print(f"Pasta não existe: {project}")
        return 1
    s = session_name(project.name)
    if not has_session(s):
        for nome, motivo in create_session(project, s):
            print(f"⚠ aba '{nome}' não aberta: {motivo}")
    else:
        configure(s)
    if os.environ.get("TMUX"):
        tmux("switch-client", "-t", f"={s}")
    else:
        os.execvp("tmux", ["tmux", "attach-session", "-t", f"={s}"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
