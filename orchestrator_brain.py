#!/usr/bin/env python3
"""
orchestrator_brain.py — o "cérebro" do Orquestrador (estrategista).

Chat completions PURO: uma requisição REST, texto entra, texto sai. Sem harness
de agente, sem ferramentas, sem sandbox — o Orquestrador é fisicamente incapaz
de ler/escrever arquivos ou rodar comandos. Todo trabalho braçal é delegado a um
operário via <DELEGATE>/<PIPELINE>.

Uso (chamado pelo orquestra.py via _build_orchestrator_cmd):
    orchestrator_brain.py -p "<prompt completo>" --provider deepseek --model deepseek-v4-flash

Saída (uma linha, mesmo contrato que o ramo "agy" de _capture_orchestrator_result):
    {"event":"result","result":{"status":"SUCCESS"|"ERROR","response":"..."}}

Provedores suportados:
    deepseek      — key em ~/.claude-deepseek-env (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN)
    ollama-cloud  — key via `kimi provider list --json`
    anthropic     — ANTHROPIC_API_KEY do ambiente (formato Messages)
    openai        — OPENAI_API_KEY do ambiente
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HISTORY_MARKER = "Histórico da Conversa:\n"
TIMEOUT_S = 90


def emit(response: str, ok: bool = True):
    print(json.dumps({
        "event": "result",
        "result": {"status": "SUCCESS" if ok else "ERROR", "response": response},
    }, ensure_ascii=False))
    sys.exit(0 if ok else 1)


def _arg(name: str, default: str = "") -> str:
    if name in sys.argv:
        i = sys.argv.index(name) + 1
        if i < len(sys.argv):
            return sys.argv[i]
    return default


def _read_env_file(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().removeprefix("export ").strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _split_prompt(prompt: str) -> tuple[str, str]:
    """Separa o system prompt (tudo antes de 'Histórico da Conversa:') do resto."""
    if HISTORY_MARKER in prompt:
        head, tail = prompt.split(HISTORY_MARKER, 1)
        return head.strip(), (HISTORY_MARKER + tail).strip()
    return "", prompt


# ── Provedores ──────────────────────────────────────────────────────────────

def _creds_deepseek() -> tuple[str, str]:
    env = _read_env_file(Path.home() / ".claude-deepseek-env")
    key = env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("DEEPSEEK_API_KEY", "")
    return key, "https://api.deepseek.com/chat/completions"


def _creds_ollama() -> tuple[str, str]:
    try:
        res = subprocess.run(["kimi", "provider", "list", "--json"],
                             capture_output=True, text=True, timeout=8)
        data = json.loads(res.stdout)
        prov = data.get("providers", {}).get("ollama-cloud", {})
        base = (prov.get("baseUrl") or "https://ollama.com/v1").rstrip("/")
        return prov.get("apiKey", ""), f"{base}/chat/completions"
    except Exception:
        return "", "https://ollama.com/v1/chat/completions"


def _call_openai_compatible(url: str, key: str, model: str, messages: list) -> str:
    body = json.dumps({
        "model": model, "messages": messages,
        "stream": False, "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        payload = json.loads(resp.read())
    return payload["choices"][0]["message"]["content"]


def _call_anthropic(key: str, model: str, system: str, user: str) -> str:
    body = json.dumps({
        "model": model, "max_tokens": 4096,
        "system": system or "Você é um orquestrador estrategista.",
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        payload = json.loads(resp.read())
    return "".join(b.get("text", "") for b in payload.get("content", []))


def main():
    prompt = _arg("-p") or _arg("--prompt")
    if not prompt:
        emit("Erro interno: prompt vazio (-p ausente).", ok=False)

    provider = (_arg("--provider") or "deepseek").lower()
    model = _arg("--model") or ("deepseek-v4-flash" if provider == "deepseek" else "kimi-k3")

    system, user = _split_prompt(prompt)

    try:
        if provider == "deepseek":
            key, url = _creds_deepseek()
            if not key:
                key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not key:
                emit("Sem credencial DeepSeek. Adicione no cofre: /senha DEEPSEEK_API_KEY <sua-chave> "
                     "(ou configure ~/.claude-deepseek-env).", ok=False)
            msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
            emit(_call_openai_compatible(url, key, model, msgs))

        elif provider in ("ollama-cloud", "ollama"):
            key, url = _creds_ollama()
            if not key:
                emit("Sem credencial Ollama Cloud (configure o `kimi` CLI). Rode /agentes trocar.", ok=False)
            msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
            emit(_call_openai_compatible(url, key, model, msgs))

        elif provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                emit("Sem ANTHROPIC_API_KEY no ambiente/cofre.", ok=False)
            emit(_call_anthropic(key, model, system, user))

        elif provider == "openai":
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key:
                emit("Sem OPENAI_API_KEY. Adicione no cofre: /senha OPENAI_API_KEY <sua-chave>", ok=False)
            msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
            emit(_call_openai_compatible("https://api.openai.com/v1/chat/completions", key, model, msgs))

        elif provider in ("gemini", "google"):
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
            if not key:
                emit("Sem GEMINI_API_KEY. Adicione no cofre: /senha GEMINI_API_KEY <sua-chave> "
                     "(chave do Google AI Studio, não é a mesma coisa que o login do agy).", ok=False)
            msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
            emit(_call_openai_compatible(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", key, model, msgs))

        else:
            emit(f"Provedor de cérebro desconhecido: '{provider}'.", ok=False)

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        emit(f"Erro da API ({provider}/{model}, HTTP {e.code}): {detail}", ok=False)
    except Exception as e:  # noqa: BLE001 — degrada com mensagem legível, nunca traceback cru
        emit(f"Falha ao falar com o cérebro ({provider}/{model}): {e}", ok=False)


if __name__ == "__main__":
    main()
