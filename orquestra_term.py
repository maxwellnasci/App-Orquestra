"""Terminal embutido do Orquestra — protótipo.

Um widget do Textual que roda um shell de verdade num pty e desenha a tela
com `pyte` (emulador VT100/xterm em Python puro). Diferente do botão
"💻 Shell" (que suspende a TUI), aqui o shell vive numa aba do palco, ao lado
do chat e do diff.

Peças:
  • ptyprocess  — fork + pty, o filho enxerga um terminal de verdade
                  (TERM=xterm-256color, tamanho sincronizado com o widget).
  • pyte        — interpreta as sequências de escape e mantém a grade de células.
  • PtyTerminal — lê o pty pelo event loop (add_reader, sem thread), traduz
                  teclas do Textual pra bytes e pinta a grade via render_line.

Limites conhecidos (ver docstring de PtyTerminal): sem scrollback, sem mouse,
sem seleção/cópia. Tela alternativa (vim/less/codex) e paste com colchetes
são tratados aqui, porque o pyte não faz isso sozinho.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import os
import shutil

import pyte
from pyte import modes as _pm
from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.strip import Strip
from textual.widget import Widget

# Modos privados (DECSET/DECRST). O pyte guarda `modo << 5` em screen.mode.
_DECCKM = 1 << 5            # setas em "application mode" (ESC O A em vez de ESC [ A)
_BRACKETED_PASTE = 2004 << 5
_ALT_SCREEN = (47, 1047, 1049)

# Tecla que devolve o foco pro Orquestra (o resto vai inteiro pro shell).
EXIT_KEY = "ctrl+right_square_bracket"      # Ctrl+]
EXIT_KEY_LABEL = "Ctrl+]"


class _Screen(pyte.Screen):
    """pyte.Screen + tela alternativa (1049/1047/47).

    O pyte ignora esses modos, então vim/less/htop/codex desenhariam por cima
    do histórico do shell e deixariam lixo ao sair. Aqui guardamos a grade
    principal e restauramos na saída.
    """

    def __init__(self, columns: int, lines: int) -> None:
        super().__init__(columns, lines)
        self._saved: tuple | None = None

    @property
    def in_alt_screen(self) -> bool:
        return self._saved is not None

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private") and any(m in _ALT_SCREEN for m in modes):
            self._enter_alt()
            modes = tuple(m for m in modes if m not in _ALT_SCREEN)
        if modes:
            super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private") and any(m in _ALT_SCREEN for m in modes):
            self._leave_alt()
            modes = tuple(m for m in modes if m not in _ALT_SCREEN)
        if modes:
            super().reset_mode(*modes, **kwargs)

    def _enter_alt(self) -> None:
        if self._saved is not None:
            return
        self._saved = (copy.deepcopy(self.buffer), self.cursor.x, self.cursor.y)
        self.erase_in_display(2)
        self.cursor_position()

    def _leave_alt(self) -> None:
        if self._saved is None:
            return
        buffer, x, y = self._saved
        self._saved = None
        self.buffer = buffer
        self.cursor.x, self.cursor.y = x, y
        self.dirty.update(range(self.lines))


def _ignore_private(name: str):
    base = getattr(pyte.Screen, name)

    def wrapper(self, *args, private=False, **kwargs):
        if private:                 # variante DEC (CSI ? ...): não implementada, ignora
            return None
        return base(self, *args, **kwargs)

    wrapper.__name__ = name
    return wrapper


# O parser do pyte chama TODO handler CSI com private=True quando a sequência
# tem "?" (vim manda CSI ? 6 n etc.), mas ~20 handlers não aceitam o argumento:
# o TypeError mata o gerador do parser e a aba para de atualizar. Aqui esses
# handlers passam a ignorar a variante privada.
for _name in {n for n in pyte.Stream.csi.values()}:
    _params = inspect.signature(getattr(pyte.Screen, _name)).parameters
    if "private" not in _params and not any(p.kind is p.VAR_KEYWORD for p in _params.values()):
        setattr(_Screen, _name, _ignore_private(_name))


# ── Teclas → bytes ────────────────────────────────────────────────────────────
# Textual entrega o nome da tecla ("up", "ctrl+left", "f5"...). Teclas simples
# (letras, Enter=\r, Tab=\t, Esc, Backspace=\x7f, Ctrl+letra) já vêm em
# `event.character`; aqui só o que não tem caractere.
_CURSOR = {"up": "A", "down": "B", "right": "C", "left": "D", "home": "H", "end": "F"}
_TILDE = {"insert": 2, "delete": 3, "pageup": 5, "pagedown": 6,
          "f5": 15, "f6": 17, "f7": 18, "f8": 19, "f9": 20, "f10": 21, "f11": 23, "f12": 24}
_SS3 = {"f1": "P", "f2": "Q", "f3": "R", "f4": "S"}
_NAMED = {
    "enter": b"\r", "tab": b"\t", "escape": b"\x1b", "space": b" ",
    "backspace": b"\x7f", "ctrl+h": b"\x08",
    "ctrl+backslash": b"\x1c", "ctrl+circumflex_accent": b"\x1e", "ctrl+underscore": b"\x1f",
    "ctrl+@": b"\x00", "ctrl+space": b"\x00",
}


def key_to_bytes(key: str, character: str | None, app_cursor: bool = False) -> bytes:
    """Traduz um evento de tecla do Textual na sequência que o xterm enviaria."""
    *mods, base = key.split("+")
    # Modificadores xterm: 1 + shift(1) + alt(2) + ctrl(4)
    mod = 1 + ("shift" in mods) + 2 * ("alt" in mods) + 4 * ("ctrl" in mods)

    if base in _CURSOR:
        if mod > 1:
            return f"\x1b[1;{mod}{_CURSOR[base]}".encode()
        intro = "\x1bO" if app_cursor and base in ("up", "down", "right", "left") else "\x1b["
        return f"{intro}{_CURSOR[base]}".encode()
    if base in _TILDE:
        return (f"\x1b[{_TILDE[base]};{mod}~" if mod > 1 else f"\x1b[{_TILDE[base]}~").encode()
    if base in _SS3:
        return (f"\x1b[1;{mod}{_SS3[base]}" if mod > 1 else f"\x1bO{_SS3[base]}").encode()
    if key == "shift+tab":
        return b"\x1b[Z"
    # Teclas de controle por nome: `character` não é confiável (o Textual manda
    # None pra enter/ctrl+letra em alguns caminhos e \x08 pro backspace).
    if key in _NAMED:
        return _NAMED[key]
    if len(base) == 1 and base.isalpha() and "ctrl" in mods and not {"alt", "shift"} & set(mods):
        return bytes([ord(base.lower()) - 96])           # ctrl+a..ctrl+z → 0x01..0x1a
    if "alt" in mods and len(base) == 1:
        return b"\x1b" + base.encode()
    if character:
        return character.encode()
    return b""


# ── Cores pyte → rich ─────────────────────────────────────────────────────────
def _color(name: str) -> str | None:
    if name == "default":
        return None
    if name == "brown":
        return "yellow"
    if name == "brightbrown":
        return "bright_yellow"
    if name.startswith("bright"):
        return "bright_" + name[6:]
    if len(name) == 6 and all(c in "0123456789abcdefABCDEF" for c in name):
        return "#" + name
    return name


class PtyTerminal(Widget, can_focus=True):
    """Shell interativo num pty, desenhado dentro de um widget do Textual.

    Uso:  PtyTerminal(cwd="/pasta/do/projeto")  — o shell só sobe no primeiro
    `start()` (a aba chama quando é aberta), com o tamanho real do widget.

    Limites do protótipo:
      • sem scrollback (só a tela visível — use `less`/`tmux` ou o Shell externo);
      • sem mouse nem seleção/cópia (o terminal hospedeiro não copia daqui);
      • pyte não cobre 100% do xterm: apps de tela cheia com truques raros
        podem sair com cor/posição imperfeita.
    """

    DEFAULT_CSS = """
    PtyTerminal {
        background: $background;
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(self, cwd: str | None = None, shell: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cwd = cwd
        self._shell = shell
        self._proc = None
        self._fd: int | None = None
        self._screen: _Screen | None = None
        self._stream: pyte.ByteStream | None = None
        self._pending_start = False
        self._exit_code: int | None = None
        self._style_cache: dict[tuple, Style] = {}

    # ── ciclo de vida ────────────────────────────────────────────────────────
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._fd is not None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def start(self) -> None:
        """Sobe o shell (idempotente). Se o widget ainda não tem tamanho, adia."""
        if self.alive:
            return
        cols, rows = self.size.width, self.size.height
        if cols < 2 or rows < 2:
            self._pending_start = True
            return
        self._pending_start = False
        from ptyprocess import PtyProcess

        shell = self._shell or os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
        env = os.environ.copy()          # inclui as chaves que o vault injetou
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env.pop("COLUMNS", None)
        env.pop("LINES", None)

        self._screen = _Screen(cols, rows)
        self._stream = pyte.ByteStream(self._screen)
        self._exit_code = None
        self._proc = PtyProcess.spawn(
            [shell], cwd=self._cwd or os.getcwd(), env=env, dimensions=(rows, cols),
        )
        self._fd = self._proc.fd
        # O pyte responde a consultas do app (posição do cursor, DA) por aqui.
        self._screen.write_process_input = lambda data: self._write(data.encode())
        asyncio.get_running_loop().add_reader(self._fd, self._on_readable)
        self.refresh()

    def stop(self) -> None:
        """Encerra o shell (SIGHUP no grupo) e solta o fd."""
        self._pending_start = False
        fd, proc = self._fd, self._proc
        self._fd = self._proc = None
        if fd is not None:
            try:
                asyncio.get_running_loop().remove_reader(fd)
            except (RuntimeError, ValueError):
                pass
        if proc is not None:
            try:
                proc.close(force=True)
            except Exception:
                pass

    def restart(self) -> None:
        self.stop()
        self.start()

    def on_unmount(self) -> None:
        self.stop()

    # ── pty → tela ───────────────────────────────────────────────────────────
    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._on_exit()
            return
        self._feed(data)
        self.refresh()

    def _feed(self, data: bytes) -> None:
        """Alimenta o pyte. Uma exceção do parser mata o gerador dele; nesse
        caso recria o stream (perde só a sequência quebrada) em vez de deixar
        a aba congelada."""
        try:
            self._stream.feed(data)
        except Exception:
            self._stream = pyte.ByteStream(self._screen)

    def _on_exit(self) -> None:
        proc = self._proc
        code = None
        if proc is not None:
            try:
                proc.isalive()                      # reaps (WNOHANG)
                code = proc.exitstatus if proc.exitstatus is not None else proc.signalstatus
            except Exception:
                pass
        self._exit_code = code
        self.stop()
        if self._stream is not None:
            self._feed(
                f"\r\n\x1b[2m[shell encerrado (código {code}) — Enter reinicia · {EXIT_KEY_LABEL} sai]"
                f"\x1b[0m\r\n".encode()
            )
        self.refresh()

    def _write(self, data: bytes) -> None:
        if self._fd is None or not data:
            return
        try:
            os.write(self._fd, data)
        except OSError:
            pass

    # ── tela (texto puro, útil pra testes) ───────────────────────────────────
    def screen_text(self) -> str:
        return "\n".join(self._screen.display) if self._screen is not None else ""

    # ── entrada ──────────────────────────────────────────────────────────────
    def on_key(self, event: events.Key) -> None:
        if event.key == EXIT_KEY:
            return                                   # deixa o app tratar (devolve o foco)
        event.stop()
        event.prevent_default()
        if not self.alive:
            if event.key == "enter":
                self.restart()
            return
        app_cursor = _DECCKM in self._screen.mode
        self._write(key_to_bytes(event.key, event.character, app_cursor))

    def on_paste(self, event: events.Paste) -> None:
        event.stop()
        if not self.alive:
            return
        text = event.text.replace("\r\n", "\n").replace("\n", "\r")
        if _BRACKETED_PASTE in self._screen.mode:
            text = f"\x1b[200~{text}\x1b[201~"
        self._write(text.encode())

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    def on_resize(self, event: events.Resize) -> None:
        cols, rows = event.size.width, event.size.height
        if cols < 2 or rows < 2:
            return
        if self._pending_start:
            self.start()
            return
        if self._screen is not None and (cols, rows) != (self._screen.columns, self._screen.lines):
            self._screen.resize(rows, cols)
            if self._proc is not None:
                try:
                    self._proc.setwinsize(rows, cols)    # SIGWINCH pro filho
                except Exception:
                    pass
            self.refresh()

    # ── desenho ──────────────────────────────────────────────────────────────
    def _style(self, ch, cursor: bool) -> Style:
        key = (ch.fg, ch.bg, ch.bold, ch.italics, ch.underscore, ch.strikethrough,
               ch.reverse, ch.blink, cursor)
        style = self._style_cache.get(key)
        if style is None:
            style = Style(
                color=_color(ch.fg), bgcolor=_color(ch.bg), bold=ch.bold,
                italic=ch.italics, underline=ch.underscore, strike=ch.strikethrough,
                reverse=ch.reverse != cursor, blink=ch.blink,
            )
            self._style_cache[key] = style
        return style

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        screen = self._screen
        if screen is None or y >= screen.lines:
            return Strip.blank(width)
        row = screen.buffer[y]
        show_cursor = self.has_focus and not screen.cursor.hidden and self.alive
        cx = screen.cursor.x if show_cursor and screen.cursor.y == y else -1
        segments: list[Segment] = []
        run, run_style = "", None
        for x in range(min(screen.columns, width)):
            ch = row[x]
            if ch.data == "":                        # 2ª metade de caractere largo
                continue
            style = self._style(ch, x == cx)
            if style is run_style:
                run += ch.data
            else:
                if run:
                    segments.append(Segment(run, run_style))
                run, run_style = ch.data, style
        if run:
            segments.append(Segment(run, run_style))
        return Strip(segments).adjust_cell_length(width)
