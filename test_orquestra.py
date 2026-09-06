#!/usr/bin/env python3
"""
Testes do Orquestra — sem rede, sem subprocessos de agente.
Rode:  python3 test_orquestra.py
"""
import json
import sys
import tempfile
from pathlib import Path

import orquestra

FAILS = []


def check(cond, msg):
    mark = "✓" if cond else "✗"
    print(f"  {mark} {msg}")
    if not cond:
        FAILS.append(msg)


# ── _resolve_worker: canônicos + aliases legados ────────────────────────────
print("_resolve_worker")
cfg = orquestra.DEFAULT_CONFIG

_w, b, label, *_ = orquestra._resolve_worker(cfg, "operador")
check(b == "claude" and label == "Operador", "operador → claude / Operador")

_w, b, label, *_ = orquestra._resolve_worker(cfg, "revisor")
check(b == "claude" and label == "Revisor", "revisor → claude / Revisor")

_w, b, *_ = orquestra._resolve_worker(cfg, "cao_guarda")
check(b == "copilot", "cao_guarda → copilot")

for legado, canon in [("implementador", "Operador"), ("designer", "Operador"),
                      ("pesquisador", "Operador"), ("arquiteto", "Revisor"),
                      ("analista", "Revisor"), ("fiscal", "Revisor")]:
    _w, _b, label, *_ = orquestra._resolve_worker(cfg, legado)
    check(label == canon, f"alias {legado} → {canon}")

_w, b, *_ = orquestra._resolve_worker(cfg, "codex")
check(b == "codex", "tag de motor 'codex' → codex")

_w, b, *_ = orquestra._resolve_worker(cfg, "tag-desconhecida-xyz")
check(b == "claude", "tag desconhecida → fallback operador/claude")


# ── _build_orchestrator_cmd: read-only, usa a conta logada ─────────────────
print("_build_orchestrator_cmd")
role = cfg["roles"]["orquestrador"]
ocmd, obin, oof = orquestra._build_orchestrator_cmd(role, "Histórico da Conversa:\nx")
check(obin == "brain", "orquestrador default → binary 'brain'")
check("orchestrator_brain.py" in " ".join(ocmd), "chama orchestrator_brain.py")
check("--provider" in ocmd and "deepseek" in ocmd, "passa --provider deepseek")
for banned in ("--dangerously-skip-permissions", "--project", "bypassPermissions"):
    check(banned not in " ".join(ocmd), f"NÃO contém {banned}")

acmd, abin, _ = orquestra._build_orchestrator_cmd({"binary": "agy", "model": "gemini-3.6-flash-high"}, "p")
check("--mode" in acmd and "plan" in acmd, "orquestrador via agy usa --mode plan (read-only)")
check("--dangerously-skip-permissions" not in acmd, "agy orquestrador sem skip-permissions")

ccmd, cbin, _ = orquestra._build_orchestrator_cmd({"binary": "claude", "model": "claude-sonnet-5"}, "p")
check("plan" in ccmd and "bypassPermissions" not in ccmd, "orquestrador via claude usa --permission-mode plan")

xcmd, xbin, xof = orquestra._build_orchestrator_cmd({"binary": "codex", "model": "gpt-5.6-terra"}, "p")
check(xbin == "codex" and "read-only" in xcmd and xof is not None, "orquestrador via codex: exec read-only + output file")


# ── _build_worker_cmd ──────────────────────────────────────────────────────
print("_build_worker_cmd")
for binfont, needle in [("claude", "claude"), ("agy", "agy"),
                        ("codex", "codex"), ("copilot", "copilot"),
                        ("opencode", "opencode")]:
    c, _e, _o = orquestra._build_worker_cmd({"model": ""}, binfont, "faça X")
    check(needle in c[0] or needle in " ".join(c), f"worker {binfont} monta comando")

c, _e, _o = orquestra._build_worker_cmd({"model": "claude-sonnet-5"}, "claude", "faça X")
check("bypassPermissions" in c, "operário claude usa bypassPermissions (headless)")

# read_only=True (Modo Aprendizado): troca pras mesmas flags plan/read-only
# do orquestrador, sem mudar mais nada no comando.
rc, _e, _o = orquestra._build_worker_cmd({"model": "claude-sonnet-5"}, "claude", "faça X", read_only=True)
check("plan" in rc and "bypassPermissions" not in rc, "read_only=True (claude) troca pra --permission-mode plan")

rc, _e, _o = orquestra._build_worker_cmd({}, "agy", "faça X", read_only=True)
check("plan" in rc and "--dangerously-skip-permissions" not in rc, "read_only=True (agy) troca pra --mode plan")

rc, _e, rof = orquestra._build_worker_cmd({}, "codex", "faça X", read_only=True)
check("read-only" in rc and "workspace-write" not in rc, "read_only=True (codex) troca pra --sandbox read-only")
import os as _os
_os.unlink(rof)

default_c, _e, _o = orquestra._build_worker_cmd({"model": "claude-sonnet-5"}, "claude", "faça X")
check(default_c == c, "read_only default (False) não muda o comando de hoje — zero regressão")


# ── OpenCode/OpenRouter (Bloco 4) ────────────────────────────────────────────
print("opencode")

oc, _e, _o = orquestra._build_worker_cmd({"model": "openrouter/deepseek/deepseek-v4-flash"}, "opencode", "revise X")
check("--auto" in oc and "--agent" not in oc, "opencode escrita normal usa --auto, sem --agent")
check("-m" in oc and "openrouter/deepseek/deepseek-v4-flash" in oc, "opencode passa -m com o model_id completo")

oc_ro, _e, _o = orquestra._build_worker_cmd({"model": ""}, "opencode", "revise X", read_only=True)
check("--agent" in oc_ro and "plan" in oc_ro and "--auto" not in oc_ro,
      "opencode read_only=True usa --agent plan, sem --auto")
check("-m" not in oc_ro, "opencode sem model_id não passa -m (evita CLI rejeitar valor vazio)")

# _fetch_opencode_models: CLI real está instalada neste ambiente de dev —
# se não estiver (CI/outra máquina), a função já cai no fallback sozinha.
oc_models = orquestra._fetch_opencode_models()
check(bool(oc_models), "_fetch_opencode_models nunca retorna vazio (real ou fallback)")
check(all(m[0].startswith("openrouter/") for m in oc_models), "todo model_id começa com openrouter/")
check(all(m[0].split("/")[1] in orquestra._OPENCODE_PROVIDERS for m in oc_models),
      "só provedores de LLM chinesa (_OPENCODE_PROVIDERS)")

# _narrate_opencode_line com linhas JSON reais (capturadas rodando o opencode de verdade)
class _FakeSection:
    def __init__(self): self.calls = []
    def tool_call(self, msg): self.calls.append(msg)

state = {"final_text": "", "is_error": False}
sec = _FakeSection()
orquestra._narrate_opencode_line('{"type":"step_start","part":{"type":"step-start"}}', sec, state)
orquestra._narrate_opencode_line(
    '{"type":"tool_use","part":{"type":"tool","tool":"read","state":{"status":"completed"}}}', sec, state)
orquestra._narrate_opencode_line('{"type":"text","part":{"type":"text","text":"ok"}}', sec, state)
orquestra._narrate_opencode_line('{"type":"step_finish","part":{"type":"step-finish","tokens":{}}}', sec, state)
check(state["final_text"] == "ok", "narrador acumula o texto do evento 'text'")
check(state["is_error"] is False, "sem evento de erro, is_error continua False")
check(any("read" in c for c in sec.calls), "tool_use narra a ferramenta usada")

state2 = {"final_text": "", "is_error": False}
orquestra._narrate_opencode_line('{"type":"error","error":{"message":"falha de rede"}}', _FakeSection(), state2)
check(state2["is_error"] is True and "falha de rede" in state2["final_text"], "evento de erro marca is_error e captura a mensagem")

check(orquestra._narrate_opencode_line("não é json", _FakeSection(), {"final_text": "", "is_error": False}) is None,
      "linha inválida não quebra (retorna None silenciosamente)")

# is_opencode chega em _run_worker_headless sem quebrar a assinatura (binário
# inexistente de propósito — não dispara rede nem processo de verdade)
import inspect
sig = inspect.signature(orquestra._run_worker_headless)
check("is_opencode" in sig.parameters, "_run_worker_headless aceita is_opencode")

# _apply_familia / _FAMILIA_CHOICES / atalho de motor
check(any(f[0] == "opencode" for f in orquestra._FAMILIA_CHOICES), "opencode está em _FAMILIA_CHOICES")
target = {}
orquestra._apply_familia(target, "opencode", "openrouter/qwen/qwen3-max")
check(target["binary"] == "opencode" and target["model"] == "openrouter/qwen/qwen3-max",
      "_apply_familia(opencode) seta binary/model certos")
check("opencode" in orquestra._WORKER_TAG_DEFAULTS, "atalho de motor 'opencode' em _WORKER_TAG_DEFAULTS")
check("opencode" in orquestra.DEFAULT_CONFIG["workers"], "atalho de motor 'opencode' em DEFAULT_CONFIG")


# ── SUGESTAO_RE (Bloco 5 — sugestões inline com aceitar/recusar) ────────────
print("regex SUGESTAO")

sug_sample = (
    'Achei um problema.\n'
    '<SUGESTAO arquivo="foo.py" linha_inicio="3" linha_fim="5" motivo="Falta validar entrada">\n'
    'def f(x):\n'
    '    if x is None:\n'
    '        raise ValueError\n'
    '</SUGESTAO>\n'
    'Mais texto depois.'
)
m = orquestra.SUGESTAO_RE.search(sug_sample)
check(bool(m), "casa o bloco <SUGESTAO>")
check(m.group(1) == "foo.py", "captura o arquivo")
check(m.group(2) == "3" and m.group(3) == "5", "captura linha_inicio/linha_fim")
check(m.group(4) == "Falta validar entrada", "captura o motivo")
check("raise ValueError" in m.group(5), "captura o código proposto")

sem_tag = orquestra.SUGESTAO_RE.sub("", sug_sample)
check("<SUGESTAO" not in sem_tag and "Achei um problema." in sem_tag and "Mais texto depois." in sem_tag,
      "sub() remove só a tag, preserva texto ao redor")

import orquestra_tui as _otui
auto_i = _otui._auto_review_instruction("/tmp/x.py")
deep_i = _otui._deep_review_instruction("/tmp/x.py")
check("<SUGESTAO" in auto_i, "instrução automática ensina o formato <SUGESTAO>")
check("<SUGESTAO" in deep_i, "instrução profunda ensina o formato <SUGESTAO>")


# ── Regex de tags ──────────────────────────────────────────────────────────
print("regex DELEGATE / PIPELINE / PLANO")
sample = (
    "Vou analisar.\n<PLANO>\n1. operador → ler\n</PLANO>\n"
    "<PIPELINE>\n<DELEGATE binary=\"operador\">passo 1</DELEGATE>\n"
    "<DELEGATE binary=\"revisor\">passo 2</DELEGATE>\n</PIPELINE>\n"
    "<DELEGATE binary=\"operador\">solto</DELEGATE>"
)
check(bool(orquestra.PLANO_RE.search(sample)), "acha <PLANO>")
pls = list(orquestra.PIPELINE_RE.finditer(sample))
check(len(pls) == 1, "acha 1 <PIPELINE>")
steps = list(orquestra.DELEGATE_RE.finditer(pls[0].group(1)))
check(len(steps) == 2 and steps[0].group(1) == "operador", "pipeline tem 2 etapas")
no_pipe = orquestra.PIPELINE_RE.sub("", sample)
solto = list(orquestra.DELEGATE_RE.finditer(no_pipe))
check(len(solto) == 1, "1 <DELEGATE> solto fora do pipeline")


# ── regex MOSTRAR ────────────────────────────────────────────────────────────
print("regex MOSTRAR")
m1 = orquestra.MOSTRAR_RE.search('vou te mostrar <MOSTRAR arquivo="a/b.py"> repara nisso')
check(bool(m1) and m1.group(1) == "a/b.py", "casa <MOSTRAR> sem barra de fechamento")
m2 = orquestra.MOSTRAR_RE.search('<MOSTRAR arquivo="c.py" />')
check(bool(m2) and m2.group(1) == "c.py", "casa <MOSTRAR /> autofechado")
antes_depois = orquestra.MOSTRAR_RE.sub("", 'antes <MOSTRAR arquivo="x.py"> depois')
check(antes_depois == "antes  depois", "sub() remove só a tag, preserva texto ao redor")


# ── build_orchestrator_prompt ──────────────────────────────────────────────
print("build_orchestrator_prompt")
p = orquestra.build_orchestrator_prompt(cfg, [{"role": "user", "content": "oi"}], "lição X")
check("Histórico da Conversa:\n" in p, "mantém o marcador de corte system/user")
check("operador" in p and "revisor" in p, "cita operador e revisor")
check("lição X" in p, "injeta a memória")
check("run_command" not in p, "não menciona ferramentas próprias")
check("MOSTRAR" in p, "ensina a tag <MOSTRAR>")
check("orquestra.py" in p, "injeta a árvore de arquivos do projeto (cwd real, é repo git)")


# ── _project_file_tree / _mostrar_resolve_path ──────────────────────────────
print("_project_file_tree / _mostrar_resolve_path")
tree = orquestra._project_file_tree(str(Path(__file__).parent))
check("orquestra.py" in tree, "árvore lista orquestra.py")
tmp_no_git = Path(tempfile.mkdtemp())
check(orquestra._project_file_tree(str(tmp_no_git)) == "", "pasta sem git → árvore vazia")

_orig_get_project_dir = orquestra.get_project_dir
orquestra.get_project_dir = lambda: str(Path(__file__).parent)
try:
    resolved = orquestra._mostrar_resolve_path("orquestra.py")
    check(resolved is not None and resolved.endswith("orquestra.py"), "resolve caminho relativo válido")
    check(orquestra._mostrar_resolve_path("../../../etc/passwd") is None, "rejeita escape com ../")
    check(orquestra._mostrar_resolve_path("/etc/passwd") is None, "rejeita path absoluto disfarçado")
    check(orquestra._mostrar_resolve_path("nao_existe_de_jeito_nenhum.py") is None, "rejeita arquivo inexistente")
finally:
    orquestra.get_project_dir = _orig_get_project_dir


# ── load_config: migração v1 → v2 idempotente ──────────────────────────────
print("load_config migração")
tmp = Path(tempfile.mkdtemp())
orig_cfg = orquestra.CONFIG_FILE
try:
    orquestra.CONFIG_FILE = tmp / ".orquestra.json"
    # config quebrada: brain com provider inválido (foi o bug real do usuário)
    v1 = {
        "active_role": "orquestrador",
        "roles": {"orquestrador": {"binary": "brain", "provider": "copilot", "model": "gpt-5.6-terra"}},
        "workers": {"claude": {"binary": "claude"}},
        "ollama_api_key": "SEGREDO",
        "ui": {"mode": "chat"},
    }
    orquestra.CONFIG_FILE.write_text(json.dumps(v1), encoding="utf-8")
    c1 = orquestra.load_config()
    o1 = c1["roles"]["orquestrador"]
    check(o1["binary"] == "codex" and "provider" not in o1,
          "provider inválido 'copilot' + modelo gpt → re-roteado p/ codex (login)")
    check("ollama_api_key" not in c1, "ollama_api_key removida da config")
    check("operador" in c1["workers"] and "revisor" in c1["workers"], "workers novos semeados")
    check(c1["ui"].get("detail") == "normal", "ui.detail preenchido")
    check(c1["ui"].get("learning_mode") is False, "ui.learning_mode preenchido (backfill, sem migração manual)")
    c2 = orquestra.load_config()
    check(c2["roles"]["orquestrador"]["binary"] == "codex", "2ª carga idempotente")

    # config válida via CLI logada (agy) — NÃO deve ser mexida
    orquestra.CONFIG_FILE.write_text(json.dumps({
        "roles": {"orquestrador": {"binary": "agy", "model": "gemini-3.6-flash-high"}},
        "workers": {}, "ui": {},
    }), encoding="utf-8")
    c3 = orquestra.load_config()
    check(c3["roles"]["orquestrador"]["binary"] == "agy", "orquestrador via agy (login) preservado")
    od = c1.get("open_design")
    from pathlib import Path as _P
    if _P(orquestra.DEFAULT_CONFIG["open_design"]["repo_path"]).is_dir():
        check(od and od.get("agent") == "claude", "open_design semeado com agent=claude")
    else:
        check(od is None, "open_design NÃO semeado (clone ausente)")
finally:
    orquestra.CONFIG_FILE = orig_cfg


# ── ui helpers ─────────────────────────────────────────────────────────────
print("ui helpers")
check(orquestra._is_chat_mode({"ui": {"mode": "chat"}}) is True, "modo chat")
check(orquestra._is_chat_mode({"ui": {"mode": "tecnico"}}) is False, "modo tecnico")
check(orquestra._detail_level({"ui": {"detail": "full"}}) == "full", "detalhe full")
check(orquestra._detail_level({}) == "normal", "detalhe default normal")


# ── PLANO_PALCO_CHAT.md Etapa 0b: prompt do Orquestrador coerente com o motor
print("build_orchestrator_prompt — motor coerente (Etapa 0b)")

_cfg_brain = {"roles": {"orquestrador": {"binary": "brain"}}}
_p_brain = orquestra.build_orchestrator_prompt(_cfg_brain, [], "")
check("NÃO\ntem ferramentas" in _p_brain, "brain: mantém 'NÃO tem ferramentas'")
check("pode ler" not in _p_brain.lower(), "brain: não promete leitura")

_cfg_codex = {"roles": {"orquestrador": {"binary": "codex"}}}
_p_codex = orquestra.build_orchestrator_prompt(_cfg_codex, [], "")
check("PODE ler arquivos" in _p_codex, "codex: promete que PODE ler")
check("não lê arquivos" not in _p_codex, "codex: não nega mais a leitura")

for _b in ("claude", "agy"):
    _p = orquestra.build_orchestrator_prompt({"roles": {"orquestrador": {"binary": _b}}}, [], "")
    check("PODE ler arquivos" in _p, f"{_b}: promete que PODE ler")


# ── PLANO_PALCO_CHAT.md Etapa 0c: Codex operário recebe o papel explícito
print("_build_worker_cmd — papel explícito pro Codex (Etapa 0c)")

_worker = {"model": "", "env_file": ""}
_cmd, _env, _ofile = orquestra._build_worker_cmd(_worker, "codex", "faz X", read_only=False)
check(_cmd[2].startswith("[PAPEL: OPERADOR"), "codex operador: prefixo [PAPEL: OPERADOR")
check("faz X" in _cmd[2], "codex operador: instrução original preservada")

_cmd_ro, _env_ro, _ofile_ro = orquestra._build_worker_cmd(_worker, "codex", "revê Y", read_only=True)
check(_cmd_ro[2].startswith("[PAPEL: REVISOR"), "codex revisor (read_only): prefixo [PAPEL: REVISOR")
for _f in (_ofile, _ofile_ro):
    try:
        Path(_f).unlink()
    except OSError:
        pass


# ── PLANO_PALCO_CHAT.md Etapa 3: git_diff_for_display (aba "± Mudanças") ────
print("git_diff_for_display (Etapa 3)")

import subprocess as _sp

_no_git = tempfile.mkdtemp()
check(orquestra.git_diff_for_display(_no_git) == ("", []), "pasta sem git → vazio")

_repo = tempfile.mkdtemp()
_sp.run(["git", "init", "-q"], cwd=_repo)
_sp.run(["git", "config", "user.email", "t@t.com"], cwd=_repo)
_sp.run(["git", "config", "user.name", "t"], cwd=_repo)
Path(_repo, "a.py").write_text("linha1\nlinha2\nlinha3\n")
_sp.run(["git", "add", "-A"], cwd=_repo)
_sp.run(["git", "commit", "-q", "-m", "base"], cwd=_repo)

check(orquestra.git_diff_for_display(_repo) == ("", []), "repo limpo → vazio")

Path(_repo, "a.py").write_text("linha1\nMUDOU\nlinha3\nlinha4\n")
Path(_repo, "novo.py").write_text("print(1)\nprint(2)\n")
_stat, _entries = orquestra.git_diff_for_display(_repo)
check("2 arquivo" in _stat, "stat_text conta os 2 arquivos")
_by_path = {e["path"]: e for e in _entries}
check(set(_by_path) == {"a.py", "novo.py"}, "achou os 2 arquivos certos")
check(_by_path["novo.py"]["added"] == 2 and _by_path["novo.py"]["removed"] == 0,
      "arquivo novo: added/removed corretos")
check(_by_path["novo.py"]["diff"] == "+print(1)\n+print(2)", "arquivo novo: diff sintético (tudo +)")
check(_by_path["a.py"]["added"] == 2 and _by_path["a.py"]["removed"] == 1,
      "arquivo rastreado: added/removed via --numstat")
check("-linha2" in _by_path["a.py"]["diff"] and "+MUDOU" in _by_path["a.py"]["diff"],
      "arquivo rastreado: diff de verdade")

# a garantia central do achado nº4: git_diff_for_display NÃO pode mexer no
# índice — senão revert_pending trataria novo.py como rastreado (checkout
# HEAD, que não faz nada) em vez de limpar (git clean -fd).
_status_after = _sp.run(["git", "status", "--porcelain"], cwd=_repo,
                        capture_output=True, text=True).stdout
check("?? novo.py" in _status_after, "novo.py continua '??' — índice intocado (achado nº4)")


print()
if FAILS:
    print(f"\033[31m✗ {len(FAILS)} falha(s):\033[0m")
    for f in FAILS:
        print(f"   - {f}")
    sys.exit(1)
print("\033[32m✓ Todos os testes passaram!\033[0m")
