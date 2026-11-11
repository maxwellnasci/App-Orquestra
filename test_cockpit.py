"""Testes do cockpit. Roda com `python3 test_cockpit.py` ou pytest.

A parte tmux usa um servidor isolado (tmux -L) e agentes falsos — não toca
nas suas sessões nem abre agente de verdade."""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cockpit  # noqa: E402

FAKE_AGENT = r'''
import os, sys, termios, tty
log = open(sys.argv[1], "wb", buffering=0)
fd = sys.stdin.fileno()
tty.setraw(fd)
sys.stdout.write("\x1b[?2004hFAKE-PRONTO\r\n"); sys.stdout.flush()
while True:
    b = os.read(fd, 4096)
    if not b:
        break
    log.write(b)
'''


def test_session_name_igual_ao_antigo():
    assert cockpit.session_name("App Orquestra") == "App_Orquestra"
    assert cockpit.session_name("a.b:c") == "a_b_c"


def test_plan_windows_pula_binario_e_arquivo_faltando():
    ws = [{"nome": "a", "binario": "existe", "cmd": "a"},
          {"nome": "b", "binario": "sumiu", "cmd": "b"},
          {"nome": "t", "binario": None, "cmd": ""},
          {"nome": "d", "binario": "existe", "cmd": "d", "precisa": "/nao/existe"}]
    ativas, puladas = cockpit.plan_windows(ws, which=lambda b: b == "existe", exists=lambda p: False)
    assert [w["nome"] for w in ativas] == ["a", "t"]
    assert [n for n, _ in puladas] == ["b", "d"]


def test_ordem_padrao_preserva_alt_1_2_3():
    assert [w["nome"] for w in cockpit.DEFAULT_WINDOWS][:3] == ["claude", "antigravity", "testes"]


def test_nenhuma_aba_passa_model_por_padrao():
    assert not any("--model" in w["cmd"] for w in cockpit.DEFAULT_WINDOWS)


def test_launch_line():
    w = {"cmd": "claude"}
    assert cockpit.launch_line(w, venv=False) == "claude"
    assert cockpit.launch_line(w, venv=True) == "source .venv/bin/activate && claude"
    assert cockpit.launch_line({"cmd": ""}, venv=False) == ""


def test_git_context_falha_alto():
    with tempfile.TemporaryDirectory() as d:
        _, err = cockpit.git_context(d)
        assert "não é um repositório git" in err
        subprocess.run(["git", "init", "-q", d], check=True)
        _, err = cockpit.git_context(d)
        assert "Nenhuma mudança" in err
        Path(d, "x.txt").write_text("oi\n")
        text, err = cockpit.git_context(d)
        assert not err and "x.txt" in text


def test_payload_grande_vira_arquivo_no_cache():
    with tempfile.TemporaryDirectory() as cache:
        old = cockpit.CACHE_DIR
        cockpit.CACHE_DIR = Path(cache)
        try:
            small = cockpit.build_payload("diff do git", "claude", "a\nb", "diff", "s")
            assert small.startswith("[Contexto passado pelo cockpit — diff do git, vindo da aba 'claude']")
            big = cockpit.build_payload("tela da aba", "codex", "x\n" * 1000, "", "s")
            assert "Leia o arquivo:" in big and str(cache) in big
            f = Path(big.split("Leia o arquivo: ")[1])
            assert f.exists() and f.read_text().count("x\n") == 1000
        finally:
            cockpit.CACHE_DIR = old


def test_tmux_integracao():
    sock = f"cockpit-test-{os.getpid()}"
    os.environ["COCKPIT_TMUX_SOCKET"] = sock
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        proj = tmp / "meu proj"
        proj.mkdir()
        subprocess.run(["git", "init", "-q", str(proj)], check=True)
        (proj / "app.py").write_text("print('mudança')\n")
        fake = tmp / "fake_agent.py"
        fake.write_text(FAKE_AGENT)
        log = tmp / "recebido.bin"
        cfg = tmp / "cockpit.json"
        cfg.write_text(json.dumps({"janelas": [
            {"nome": "origem", "binario": None, "cmd": ""},
            {"nome": "agente", "binario": sys.executable, "cmd": f"{sys.executable} '{fake}' '{log}'"},
            {"nome": "sumido", "binario": "binario-que-nao-existe", "cmd": "x"},
        ]}))
        os.environ["COCKPIT_CONFIG"] = str(cfg)
        try:
            s = cockpit.session_name(proj.name)
            puladas = cockpit.create_session(proj, s)
            assert [n for n, _ in puladas] == ["sumido"]
            assert cockpit.has_session(s)
            rows = cockpit.tmux("list-windows", "-t", f"={s}", "-F",
                                "#{window_name}\t#{pane_current_path}\t#{pane_id}", capture=True).splitlines()
            rows = [r.split("\t") for r in rows]
            assert [r[0] for r in rows] == ["origem", "agente"]
            assert all(Path(r[1]) == proj for r in rows)
            for _ in range(50):
                if "FAKE-PRONTO" in cockpit.tmux("capture-pane", "-p", "-t", rows[1][2], capture=True):
                    break
                time.sleep(0.1)
            respostas = iter(["1", "1"])  # diff do git → primeira aba alvo ("agente")
            assert cockpit.passar(s, rows[0][2], ask=lambda _p: next(respostas)) == 0
            time.sleep(0.5)
            raw = log.read_bytes()
            assert raw.startswith(b"\x1b[200~"), raw[:40]
            assert raw.endswith(b"\x1b[201~"), raw[-40:]  # nada depois do paste = sem Enter
            assert "app.py".encode() in raw and "vindo da aba 'origem'".encode() in raw
            # Sem repo git: falha alto e não cola nada
            log.write_bytes(b"")
            respostas = iter(["1", ""])
            cockpit.tmux("send-keys", "-t", rows[0][2], "cd /", "Enter")
            time.sleep(0.4)
            assert cockpit.passar(s, rows[0][2], ask=lambda _p: next(respostas)) == 1
            time.sleep(0.3)
            assert log.read_bytes() == b""
        finally:
            subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True)
            os.environ.pop("COCKPIT_TMUX_SOCKET", None)
            os.environ.pop("COCKPIT_CONFIG", None)


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"  ✗ {name}: {e!r}")
    print("Todos os testes passaram!" if not fails else f"{fails} falha(s)")
    sys.exit(1 if fails else 0)
