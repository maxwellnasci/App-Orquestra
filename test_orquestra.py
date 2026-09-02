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
                        ("codex", "codex"), ("copilot", "copilot")]:
    c, _e, _o = orquestra._build_worker_cmd({"model": ""}, binfont, "faça X")
    check(needle in c[0] or needle in " ".join(c), f"worker {binfont} monta comando")

c, _e, _o = orquestra._build_worker_cmd({"model": "claude-sonnet-5"}, "claude", "faça X")
check("bypassPermissions" in c, "operário claude usa bypassPermissions (headless)")


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


# ── build_orchestrator_prompt ──────────────────────────────────────────────
print("build_orchestrator_prompt")
p = orquestra.build_orchestrator_prompt(cfg, [{"role": "user", "content": "oi"}], "lição X")
check("Histórico da Conversa:\n" in p, "mantém o marcador de corte system/user")
check("operador" in p and "revisor" in p, "cita operador e revisor")
check("lição X" in p, "injeta a memória")
check("run_command" not in p, "não menciona ferramentas próprias")


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


print()
if FAILS:
    print(f"\033[31m✗ {len(FAILS)} falha(s):\033[0m")
    for f in FAILS:
        print(f"   - {f}")
    sys.exit(1)
print("\033[32m✓ Todos os testes passaram!\033[0m")
