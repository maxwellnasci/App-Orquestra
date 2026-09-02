# Orquestra — Arquitetura para Agentes de IA

> Leia antes de mexer no código. Descreve a arquitetura de 3 agentes, o fluxo de um
> turno e as regras que não podem quebrar.

---

## O que é

Uma **TUI multi-agente** (Textual) que funciona como um Cursor/VS Code de terminal.
O usuário conversa com um **Orquestrador estrategista** que planeja e delega; uma
equipe pequena executa; cada mudança passa por aprovação humana e por um Cão de
Guarda automático.

**Filosofia:** uma pessoa comum tem um fluxo de trabalho profissional (segurança,
qualidade, múltiplas perspectivas de IA) sem conhecer os detalhes de cada CLI.

---

## Os 3 agentes + o Cão de Guarda

| Agente (config) | Papel | Motor padrão | Ferramentas? |
|---|---|---|---|
| `roles.orquestrador` | Estrategista: conversa, `<PLANO>`, delega | `brain` / DeepSeek v4 Flash | **NÃO** (chat puro) |
| `workers.operador` | Executor: lê/escreve/roda/cria | `claude` / claude-sonnet-5 | sim (headless) |
| `workers.revisor` | Revisão sob demanda | `claude` / claude-opus-5 | sim (headless) |
| `workers.cao_guarda` | Auditoria automática do diff | `copilot` / auto | sim (read-only) |

Atalhos de motor (uso avançado): `workers.claude`, `.agy`, `.deepseek`, `.codex`.
Tags legadas (`implementador`, `arquiteto`, `pesquisador`, `analista`, `designer`,
`fiscal`) são traduzidas por `_WORKER_ALIASES` → operador/revisor.

---

## Fluxo de um turno

```
Usuário (TUI: chat)
   │
   ▼
Orquestrador  ─ orchestrator_brain.py (chat completions, ZERO ferramentas)
   │   • dúvida conceitual → responde e acabou
   │   • tarefa de código → escreve <PLANO> e PARA (gate)
   │
   ▼  usuário clica "▶ Executar plano"
Orquestrador emite <DELEGATE binary="operador"> / <PIPELINE>
   │
   ▼
Operador (headless, --output-format stream-json)
   │   • GPS: arquivos tocados abrem no Editor em tempo real
   │   • git snapshot antes/depois isola a tarefa
   │
   ▼  mexeu em arquivos → status "⚠️ Aguardando Aprovação"
Usuário: ✅ Aprovar (commit) │ ❌ Rejeitar (git reset dos arquivos da tarefa) │ 🔍 Revisar
   │
   ▼  ao aprovar
Cão de Guarda (Copilot) audita o git diff → rodapé verde, ou vermelho + alerta
```

### Tags que o Orquestrador emite

- `<PLANO> ... </PLANO>` — passos; abre destacado, é o gate de aprovação.
- `<DELEGATE binary="operador"> instruções </DELEGATE>` — uma tarefa. Vários soltos = paralelo.
- `<PIPELINE> <DELEGATE>...</DELEGATE> <DELEGATE>...</DELEGATE> </PIPELINE>` — cadeia; cada
  etapa recebe o resultado da anterior automaticamente.
- `<DELEGATE binary="open-design"> ... </DELEGATE>` — prévia visual real via Open Design
  (não entra em `<PIPELINE>`). A TUI roda `open_design_run_task`, mostra a prévia na aba
  **Idea Studio** e abre o link ao vivo no navegador. Config: seção `open_design` em
  `~/.orquestra.json` (`repo_path`, `node_bin_dir`, `agent`), auto-semeada por `load_config()`
  se o clone existir. `linkedDirs` NÃO é read-only — se o Open Design escrever nos arquivos
  reais, a TUI mostra o aviso e cai no gate de aprovação. Também: comando `/design <o que prever>`.

---

## Regras que não podem quebrar

1. **O Orquestrador nunca ganha ferramentas.** `_build_orchestrator_cmd` só usa
   `binary="brain"` (ou `agy`/`claude` como fallback **sem** `--add-dir` /
   `--dangerously-skip-permissions` / `--permission-mode bypassPermissions`). O spike
   provou que `agy`/`claude` executam comandos mesmo em `--mode plan` — por isso o
   caminho padrão é `orchestrator_brain.py` (chat REST puro).
2. **O texto do prompt do Orquestrador tem fonte única:** `build_orchestrator_prompt()`
   em `orquestra.py`. TUI e REPL chamam essa função. O marcador literal
   `"Histórico da Conversa:\n"` é onde `orchestrator_brain.py` corta system/user — não mude.
3. **Comandos `/senha*`** são interceptados por `vault.handle_command` antes de
   qualquer agente.
4. **O Cão de Guarda roda de verdade:** `_run_copilot_watchdog(config)` dispara uma
   thread (cooldown de 15s) que chama `watchdog_verdict(config)`. Silencioso se o
   veredito for "tudo certo".
5. **`vault.py`** não tem dependência além de `cryptography` + stdlib.
6. **Assinaturas compartilhadas:** `_run_worker_headless`, `_resolve_worker`,
   `_build_worker_cmd`, `_build_orchestrator_cmd`, `build_orchestrator_prompt` são
   usadas pela TUI **e** pelo REPL — mudou uma, atualize os dois callers.

---

## Config (`~/.orquestra.json`)

```json
{
  "active_role": "orquestrador",
  "roles": {
    "orquestrador": { "binary": "brain", "provider": "deepseek", "model": "deepseek-v4-flash" }
  },
  "workers": {
    "operador":   { "binary": "claude", "model": "claude-sonnet-5" },
    "revisor":    { "binary": "claude", "model": "claude-opus-5" },
    "cao_guarda": { "binary": "copilot", "model": "auto" }
  },
  "ui": { "mode": "chat", "detail": "normal" }
}
```

`load_config()` migra automaticamente configs v1 (orquestrador em `agy`/`claude`,
operários com nomes soltos, `ollama_api_key` em texto puro).

### Cérebros do Orquestrador (`binary="brain"`)

`orchestrator_brain.py` fala com o modelo por chat completions puro (zero
ferramentas). Qualquer empresa serve — MENOS Copilot (não tem API de chat direto).
O botão `Orq:` no topo lista todos; se faltar a chave, o Orquestrador avisa no chat.

| provider | credencial | funciona sem configurar? |
|---|---|---|
| `deepseek` | `~/.claude-deepseek-env` (`ANTHROPIC_API_KEY`) ou `/senha DEEPSEEK_API_KEY` | ✅ (se o env existir) |
| `ollama-cloud` | `kimi provider list --json` | ✅ (se o `kimi` estiver configurado) |
| `anthropic` | `/senha ANTHROPIC_API_KEY sk-ant-...` | precisa da chave |
| `openai` | `/senha OPENAI_API_KEY sk-...` | precisa da chave |
| `gemini` | `/senha GEMINI_API_KEY ...` (Google AI Studio — não é o login do `agy`) | precisa da chave |

### Motores dos operários (`binary=`)

| binary | ferramenta | headless |
|---|---|---|
| `claude` | Claude Code | `-p --output-format stream-json --permission-mode bypassPermissions` |
| `agy` | Antigravity | `-p --output-format stream-json --dangerously-skip-permissions` |
| `codex` | Codex CLI | `exec --json --sandbox workspace-write` |
| `copilot` | `gh copilot` | `-p --no-ask-user` (texto puro) |

---

## Cofre (`vault.py`)

Fernet (AES-128-CBC + HMAC-SHA256), chave de 256 bits via PBKDF2-SHA256 (600k
iterações), salt de 16 bytes por projeto. `.orquestra_vault` e `.orquestra_salt`
entram no `.gitignore` sozinhos. Credenciais são injetadas como env vars da sessão
(`inject_env`). O valor nunca aparece no chat/log/histórico.

---

## Arquivos

```
orquestra                → launcher (TUI; --repl = REPL antigo)
orquestra_tui.py         → a interface (Textual)
orquestra.py             → backend: config, memória SQLite, cofre, execução, Cão de Guarda
orchestrator_brain.py    → cérebro do Orquestrador (chat puro, zero ferramentas)
vault.py                 → cofre de credenciais
test_orquestra.py        → testes sem rede (python3 test_orquestra.py)
_archive/                → código aposentado (roles antigos, llm_orchestrator.py)
```
