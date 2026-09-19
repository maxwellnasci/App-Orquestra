#!/usr/bin/env python3
"""
Testes do terminal embutido (orquestra_term.py) e da sua integração na TUI.
Sem rede; sobe shells de verdade num pty (bash/sh) e a OrquestraTUI headless.
Rode:  python3 test_orquestra_term.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

FAILS = []


def check(cond, msg):
    print(f"  {'✓' if cond else '✗'} {msg}")
    if not cond:
        FAILS.append(msg)


async def wait_for(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


def pid_gone(pid):
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


# ── key_to_bytes ─────────────────────────────────────────────────────────────
print("key_to_bytes")
from orquestra_term import key_to_bytes as kb

check(kb("enter", None) == b"\r", "enter → \\r (mesmo sem event.character)")
check(kb("backspace", "\x08") == b"\x7f", "backspace → \\x7f (ignora \\x08 do character)")
check(kb("ctrl+c", None) == b"\x03", "ctrl+c → \\x03")
check(kb("ctrl+d", None) == b"\x04", "ctrl+d → \\x04")
check(kb("a", "a") == b"a" and kb("ç", "ç") == "ç".encode(), "letras/UTF-8 passam pelo character")
check(kb("up", None) == b"\x1b[A" and kb("up", None, app_cursor=True) == b"\x1bOA",
      "seta respeita o modo application cursor")
check(kb("ctrl+left", None) == b"\x1b[1;5D", "ctrl+left → CSI 1;5 D")
check(kb("delete", "\x7f") == b"\x1b[3~", "delete → CSI 3 ~")
check(kb("f1", None) == b"\x1bOP" and kb("f5", None) == b"\x1b[15~", "F1 (SS3) e F5 (tilde)")
check(kb("shift+tab", None) == b"\x1b[Z", "shift+tab → CSI Z")
check(kb("alt+x", None) == b"\x1bx", "alt+x → ESC x")

# ── parser: sequências que derrubavam o pyte ─────────────────────────────────
print("_Screen (parser)")
import pyte
from orquestra_term import _Screen, _color

_scr = _Screen(40, 5)
_st = pyte.ByteStream(_scr)
try:
    # o que o vim manda ao abrir: CSI ? 6 n (posição), CSI ? ... m, CSI > 4;2 m
    _st.feed(b"\x1b[?6n\x1b[?1;2m\x1b[>4;2m\x1b[?7H depois-do-vim")
    _ok = True
except Exception as e:
    _ok = False
check(_ok, "sequências privadas (CSI ? ... n/m/H) não lançam exceção")
check("depois-do-vim" in "\n".join(_scr.display), "parser segue vivo e desenha depois delas")
_st.feed(b"\x1b[?1049hTELA-ALT")
check(_scr.in_alt_screen and "TELA-ALT" in "\n".join(_scr.display), "CSI ? 1049 h entra na tela alternativa")
_st.feed(b"\x1b[?1049l")
check(not _scr.in_alt_screen and "depois-do-vim" in "\n".join(_scr.display),
      "CSI ? 1049 l restaura a tela principal")
check(_color("default") is None and _color("red") == "red" and _color("brown") == "yellow"
      and _color("brightblue") == "bright_blue" and _color("ff8800") == "#ff8800",
      "mapa de cores pyte → rich")

# ── PtyTerminal isolado ──────────────────────────────────────────────────────
print("PtyTerminal (widget isolado)")
from textual.app import App
from textual.binding import Binding
from orquestra_term import PtyTerminal


class _Host(App):
    BINDINGS = [Binding("ctrl+b", "mark('b')"), Binding("ctrl+t", "mark('t')")]

    def __init__(self, cwd):
        super().__init__()
        self.marks = []
        self._cwd = cwd

    def compose(self):
        yield PtyTerminal(cwd=self._cwd)

    def action_mark(self, k):
        self.marks.append(k)


async def widget_tests():
    cwd = tempfile.mkdtemp(prefix="orq_term_")
    app = _Host(cwd)
    async with app.run_test(size=(100, 30)) as pilot:
        term = app.query_one(PtyTerminal)
        check(not term.alive, "não sobe shell antes do start()")
        term.focus()
        term.start()
        await pilot.pause()
        check(term.alive, "start() sobe o shell")
        pid = term.pid

        await pilot.press(*"echo oi-$((20+22))", "enter")
        check(await wait_for(lambda: "oi-42" in term.screen_text()), "digitar + Enter executa no shell")

        await pilot.press(*"pwd", "enter")
        check(await wait_for(lambda: Path(cwd).name in term.screen_text()), "shell abre no cwd pedido")

        await pilot.press(*"printf '\\033[31mVERMELHO\\033[0m\\n'", "enter")
        await wait_for(lambda: "VERMELHO" in term.screen_text())
        _y = next((i for i, l in enumerate(term.screen_text().splitlines())
                   if l.startswith("VERMELHO")), None)
        _segs = list(term.render_line(_y)) if _y is not None else []
        check(any(s.text.startswith("VERMELHO") and s.style and s.style.color
                  and s.style.color.name == "red" for s in _segs),
              "cor ANSI (vermelho) chega ao render_line")

        await pilot.press("ctrl+b", "ctrl+t")
        await pilot.pause()
        check(app.marks == [], "atalhos do app NÃO vazam enquanto o terminal tem foco")

        await pilot.press(*"sleep 30", "enter")
        await asyncio.sleep(0.3)
        await pilot.press("ctrl+c")
        await asyncio.sleep(0.3)
        await pilot.press(*"echo apos-ctrlc", "enter")
        check(await wait_for(lambda: "apos-ctrlc" in term.screen_text(), 4), "Ctrl+C chega ao shell (interrompe sleep)")
        check(app.is_running, "Ctrl+C não derruba o app")

        await pilot.press(*"printf 'ANTES\\n'; less /etc/hostname", "enter")
        check(await wait_for(lambda: term._screen.in_alt_screen), "less entra na tela alternativa")
        await pilot.press("q")
        check(await wait_for(lambda: not term._screen.in_alt_screen), "sair do less restaura a tela principal")
        check("ANTES" in term.screen_text(), "conteúdo anterior ao less foi preservado")

        await pilot.resize_terminal(80, 20)
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.press(*"stty size", "enter")
        want = f"{term.size.height} {term.size.width}"
        check(await wait_for(lambda: want in term.screen_text()), f"tamanho do pty acompanha o widget ({want})")

        await pilot.press(*"exit", "enter")
        check(await wait_for(lambda: not term.alive), "exit encerra o shell")
        check("encerrado" in term.screen_text(), "mostra aviso de shell encerrado")
        check(pid_gone(pid), "processo do shell foi coletado (sem zumbi)")
        await pilot.press("enter")
        check(await wait_for(lambda: term.alive and term.pid != pid), "Enter reinicia um shell novo")
        last = term.pid
    await asyncio.sleep(0.3)
    check(pid_gone(last), "sair do app mata o shell filho")


asyncio.run(widget_tests())

# ── Integração na OrquestraTUI ───────────────────────────────────────────────
print("OrquestraTUI + terminal embutido")
import orquestra_tui
from orquestra_tui import OrquestraTUI


async def tui_tests():
    calls = []

    async def fake_open_shell(self):
        calls.append("open_shell")

    with patch.object(OrquestraTUI, "action_open_shell", fake_open_shell):
        app = OrquestraTUI()
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            term = app.query_one("#embedded-term", PtyTerminal)
            check(not term.alive, "shell embutido não sobe no boot")

            await pilot.click("#btn-shell")
            await pilot.pause()
            check(calls == ["open_shell"], "botão 💻 Shell agora AGUARDA action_open_shell")

            await pilot.press("ctrl+e")
            check(await wait_for(lambda: term.alive), "Ctrl+E abre a aba e sobe o shell")
            check(app.query_one("#stage-tabs").active == "stage-terminal", "aba Terminal ativa")
            await pilot.pause()
            check(app.focused is term, "foco vai pro terminal")

            await pilot.press(*"echo integrado-$((6*7))", "enter")
            check(await wait_for(lambda: "integrado-42" in term.screen_text()), "digitar na aba executa no shell")

            files_before = app.query_one("#file-panel").has_class("hidden")
            await pilot.press("ctrl+b")
            await pilot.pause()
            check(app.query_one("#file-panel").has_class("hidden") == files_before,
                  "Ctrl+B (atalho do app) vai pro shell, não alterna o painel")

            app._re_enable_input()
            await pilot.pause()
            check(app.focused is term, "fim de tarefa não rouba o foco do terminal")

            await pilot.press("ctrl+right_square_bracket")
            await pilot.pause()
            check(app.focused is app.query_one("#chat-input"), "Ctrl+] devolve o foco ao chat")
            check(term.alive, "shell continua vivo depois de sair do foco")
            pid = term.pid
        await asyncio.sleep(0.3)
        check(pid_gone(pid), "fechar o Orquestra mata o shell embutido")


asyncio.run(tui_tests())

print()
if FAILS:
    print(f"\033[31m✗ {len(FAILS)} falha(s):\033[0m")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("\033[32m✓ Todos os testes do terminal passaram!\033[0m")
