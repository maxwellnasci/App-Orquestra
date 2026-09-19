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

            panel = app.query_one("#terminal-panel")
            check(not panel.has_class("visible"), "painel lateral começa fechado")

            await pilot.click("#btn-shell")
            check(await wait_for(lambda: term.alive), "botão 💻 Shell sobe o shell no painel lateral")
            await pilot.pause()
            check(panel.has_class("visible"), "botão abre o painel lateral")
            check(app.query_one("#side-tabs").active == "side-shell", "aba 🖥 Shell ativa no painel")
            check(app.focused is term, "foco vai pro terminal")
            check(calls == [], "botão NÃO suspende a TUI (nada de terminal externo)")

            # shell e o resto da tela visíveis AO MESMO TEMPO
            _t, _a, _c = term.region, app.query_one("#activity-log").region, app.query_one("#chat-input").region
            check(_t.width >= 40 and _t.height >= 10, f"terminal com tamanho útil ({_t.width}x{_t.height})")
            check(_a.width > 0 and _a.right <= _t.x, "Atividade visível à esquerda do terminal")
            check(_c.height > 0 and _c.y >= _t.bottom, "chat visível abaixo do terminal")
            check(app.query_one("#stage-tabs").active == "stage-activity",
                  "palco continua na Atividade (não foi trocado)")

            # RichLog adia escrita enquanto oculto: os logs aparecem ao voltar pra aba 📊 Logs
            app._terminal_log("linha-de-log")
            app.query_one("#side-tabs").active = "side-logs"
            await pilot.pause()
            check(any("linha-de-log" in str(l.text) for l in app.query_one("#terminal-log").lines),
                  "aba Logs recebe o texto escrito enquanto o Shell estava em foco")
            await pilot.press("ctrl+e")                     # de volta ao Shell (foco em nenhum campo de texto)
            await wait_for(lambda: app.focused is term)

            await pilot.press("ctrl+right_square_bracket")
            await pilot.pause()
            check(app.focused is app.query_one("#chat-input"), "Ctrl+] devolve o foco ao chat")
            await pilot.press("ctrl+t")
            await pilot.pause()
            check(not panel.has_class("visible") and term.alive, "Ctrl+T fecha o painel e o shell segue vivo")
            # foco no TextArea do chat: ele liga Ctrl+E a "fim da linha" — o atalho precisa vencer
            await pilot.press("ctrl+e")
            check(await wait_for(lambda: panel.has_class("visible") and app.focused is term),
                  "Ctrl+E (com o foco no chat) reabre o painel direto no shell")
            await pilot.press("ctrl+right_square_bracket")
            await pilot.pause()
            await pilot.press("ctrl+o")
            await pilot.pause()
            check(calls == ["open_shell"], "Ctrl+O continua abrindo o shell externo (suspende)")
            await pilot.press("ctrl+e")
            await wait_for(lambda: app.focused is term)

            # Guarda: nunca digitar no chat por engano (isso dispararia chamada real ao Orquestrador)
            if app.focused is not term:
                check(False, "foco no terminal antes de digitar (abortando o resto pra não falar com o Orquestrador)")
                return
            await pilot.press(*"echo integrado-$((6*7))", "enter")
            check(await wait_for(lambda: "integrado-42" in term.screen_text()), "digitar no painel executa no shell")

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

            # ✕ do painel: fecha com o clique, mesmo com o foco DENTRO do terminal
            await pilot.press("ctrl+e")
            await wait_for(lambda: app.focused is term)
            close = app.query_one("#btn-close-side")
            check(close.region.width > 0 and close.region.right == panel.region.right,
                  "✕ visível no canto superior direito do painel")
            await pilot.click("#btn-close-side")
            check(await wait_for(lambda: not panel.has_class("visible")), "clicar no ✕ fecha o painel")
            check(term.alive and term.pid == pid, "✕ não mata o shell (só esconde o painel)")
            check(app.focused is app.query_one("#chat-input"), "✕ devolve o foco ao chat")
            await pilot.press("ctrl+e")
            check(await wait_for(lambda: panel.has_class("visible") and app.focused is term),
                  "Ctrl+E reabre o painel e o mesmo shell volta")
            check(term.pid == pid, "é o mesmo shell (não reiniciou)")
            await pilot.press("ctrl+right_square_bracket")
            await pilot.pause()
        await asyncio.sleep(0.3)
        check(pid_gone(pid), "fechar o Orquestra mata o shell embutido")


asyncio.run(tui_tests())


# ── shell na pasta de lançamento + UX do Cão de Guarda ───────────────────────
print("Shell na pasta de lançamento / Cão de Guarda proativo")


async def launch_dir_and_watchdog_tests():
    dir_a = tempfile.mkdtemp(prefix="orq_launch_")
    dir_b = tempfile.mkdtemp(prefix="orq_other_")
    prev = os.getcwd()
    os.chdir(dir_a)
    try:
        followups = []
        with patch.object(OrquestraTUI, "_run_orchestrator", lambda self, prompt: followups.append(prompt)), \
             patch("orquestra.save_chat_message") as chat:
            app = OrquestraTUI()
            async with app.run_test(size=(140, 40)) as pilot:
                await pilot.pause()
                os.chdir(dir_b)   # algo muda o cwd do processo depois do boot
                term = app.query_one("#embedded-term", PtyTerminal)
                check(app._launch_dir == os.path.realpath(dir_a) or app._launch_dir == dir_a,
                      "_launch_dir guarda a pasta de onde o app foi aberto")
                app.action_focus_shell()
                check(await wait_for(lambda: term.alive), "shell embutido sobe")
                await pilot.press(*"pwd", "enter")
                check(await wait_for(lambda: Path(dir_a).name in term.screen_text()),
                      "shell abre na pasta de lançamento mesmo após os.chdir")
                check(Path(dir_b).name not in term.screen_text().split("pwd", 1)[-1],
                      "shell NÃO abre na pasta que o cwd virou depois")

                # ── alerta persistente ───────────────────────────────────────
                footer = app.query_one("#watchdog-footer")
                bells = []
                app.bell = lambda: bells.append(1)
                await asyncio.to_thread(app._report_watchdog, "achei [bold]problema[/bold] X", True, False)
                await pilot.pause()
                check(footer.has_class("alert"), "achado real → rodapé vermelho (alert)")
                check(app._watchdog_timer is None, "achado real NÃO agenda auto-limpeza do rodapé")
                check(bells == [1], "achado real toca o bell")
                feed = "\n".join(str(l.text) for l in app.query_one("#activity-log").lines)
                check("problema" in feed, "achado completo aparece no feed (markup escapado, sem quebrar)")
                long_txt = "linha longa " * 400     # ~4.8k chars, passava de 1500 e cortava no meio
                await asyncio.to_thread(app._report_watchdog, long_txt + "FIM-DO-TEXTO", True, False)
                await pilot.pause()
                feed = "\n".join(str(l.text) for l in app.query_one("#activity-log").lines)
                check("FIM-DO-TEXTO" in feed, "texto do achado NÃO é mais truncado em 1500 chars")
                check(chat.call_count == 0, "to_chat=False não grava no histórico")

                await asyncio.to_thread(app._report_watchdog, "", False, False)
                await pilot.pause()
                check(not footer.has_class("alert"), "sem achado → alerta some")
                await asyncio.to_thread(app._report_watchdog, "achado y", True, True)
                check(chat.call_count == 1 and "achado y" in chat.call_args[0][2],
                      "to_chat=True grava o veredito no histórico do Orquestrador")

                # ── follow-up automático (1x por pedido) ─────────────────────
                await asyncio.to_thread(app._watchdog_followup, "achado y", False)
                await asyncio.to_thread(app._watchdog_followup, "", True)
                await pilot.pause()
                check(followups == [], "sem has_bug/sem texto → Orquestrador NÃO é chamado")
                await asyncio.to_thread(app._watchdog_followup, "achado y", True)
                await pilot.pause()
                check(len(followups) == 1 and "achado y" in followups[0] and "NÃO emita" in followups[0],
                      "has_bug → Orquestrador é chamado com o achado")
                await asyncio.to_thread(app._watchdog_followup, "achado z", True)
                await pilot.pause()
                check(len(followups) == 1, "2º achado no mesmo pedido NÃO dispara outro turno (sem loop)")
                app._watchdog_followup_done = False   # o que on_chat_input_submitted faz
                await asyncio.to_thread(app._watchdog_followup, "achado z", True)
                await pilot.pause()
                check(len(followups) == 2, "novo pedido do usuário libera um novo follow-up")

                # ── fechamento de tarefa ─────────────────────────────────────
                chat.reset_mock()
                await asyncio.to_thread(app._close_task, "docs/A.md", False)
                await pilot.pause()
                feed = "\n".join(str(l.text) for l in app.query_one("#activity-log").lines)
                check("Concluído" in feed and "docs/A.md" in feed, "fechamento aparece no feed")
                check(chat.call_count == 1 and "Conclusão da tarefa" in chat.call_args[0][2],
                      "fechamento é gravado no histórico do chat")

                # ── ponta a ponta: Aprovar → Cão acha algo → commit → fecha → Orquestrador fala ──
                order = []
                app._watchdog_followup_done = False
                app._pipeline_rest = None
                with patch("orquestra.watchdog_verdict", return_value=("3 inconsistências na doc", True)), \
                     patch("orquestra.commit_pending", side_effect=lambda *a: order.append("commit") or "docs/X.md"), \
                     patch("orquestra.save_chat_message",
                           side_effect=lambda r, ro, c: order.append("chat:" + c.split("]")[0].lstrip("["))), \
                     patch.object(OrquestraTUI, "_run_orchestrator",
                                  lambda self, prompt: order.append("orquestrador")):
                    await app._run_watchdog(commit_after=True).wait()
                    await pilot.pause()
                check(order == ["chat:Cão de Guarda — revisão automática", "commit",
                                "chat:Conclusão da tarefa", "orquestrador"],
                      f"Aprovar com achado: veredito → commit → fechamento → Orquestrador fala ({order})")
                check(app.query_one("#watchdog-footer").has_class("alert"),
                      "alerta continua aceso depois do fluxo inteiro")

                # save manual no editor (file_path): não polui chat, não fecha tarefa, não chama Orquestrador
                order.clear()
                with patch("orquestra.watchdog_verdict", return_value=("achado no editor", True)), \
                     patch("orquestra.save_chat_message", side_effect=lambda *a: order.append("chat")), \
                     patch.object(OrquestraTUI, "_run_orchestrator", lambda self, prompt: order.append("orquestrador")):
                    app._watchdog_followup_done = False
                    await app._run_watchdog(commit_after=False, file_path="/tmp/x.py").wait()
                    await pilot.pause()
                check(order == [], f"Ctrl+S manual no editor continua silencioso pro chat/Orquestrador ({order})")
    finally:
        os.chdir(prev)


asyncio.run(launch_dir_and_watchdog_tests())

print()
if FAILS:
    print(f"\033[31m✗ {len(FAILS)} falha(s):\033[0m")
    for f in FAILS:
        print("   -", f)
    sys.exit(1)
print("\033[32m✓ Todos os testes do terminal passaram!\033[0m")
