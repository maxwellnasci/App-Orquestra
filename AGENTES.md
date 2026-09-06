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
- `<MOSTRAR arquivo="caminho/relativo">` — abre esse arquivo no painel Editor na hora,
  durante a conversa (sem esperar o usuário clicar em nada). O Orquestrador não lê
  arquivo — só sabe que `caminho/relativo` existe porque `build_orchestrator_prompt()`
  injeta uma árvore de arquivos leve (`_project_file_tree`, via `git ls-files`) no
  prompt. `orquestra_tui._run_orchestrator` extrai `MOSTRAR_RE` **antes** do parsing
  de PLANO/DELEGATE/PIPELINE — se entrasse no mesmo corte de "texto visível", o texto
  escrito depois da tag sumiria da bolha do chat. `_mostrar_resolve_path()` valida que
  o caminho fica dentro da pasta do projeto (mais restrito que `_bridge_path_ok`, que
  também aceita HOME/tempdir — não faz sentido aqui).

### Tags que o Revisor emite (Modo Aprendizado)

- `<SUGESTAO arquivo="..." linha_inicio="N" linha_fim="M" motivo="...">código</SUGESTAO>`
  — vira um cartão no painel Editor com ✅ Aceitar / ❌ Recusar. Aceitar aplica via
  `TextArea.replace()` (não escrita direta em disco pelo Revisor — ele está em modo
  leitura) e chama `action_save_file()`. Só existe hoje nos dois fluxos do Modo
  Aprendizado (revisão automática ao salvar / botão "Revisão profunda"), que já
  revisam um arquivo específico com `read_only=True` — não no "🔍 Revisar" tradicional
  (git diff, potencialmente multi-arquivo).

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
7. **Modo Aprendizado (`read_only=True`) precisa ser read-only DE VERDADE, não só por
   texto no prompt.** Mesmo princípio da regra 1: pedir "só revise, não edite" numa
   instrução não segura nada — o Revisor normal roda com
   `--permission-mode bypassPermissions`/`--dangerously-skip-permissions`/
   `--sandbox workspace-write`, com poder de escrita real. `_build_worker_cmd(...,
   read_only=True)` troca pras mesmas flags plano/read-only do Orquestrador
   (`--permission-mode plan`/`--mode plan`/`--sandbox read-only`/`--agent plan` no
   opencode) — testado que bloqueiam escrita sem travar em modo headless. Se adicionar
   um motor novo, ele só serve pro Modo Aprendizado se tiver um modo read-only real.
8. **Narração de worker nunca vai pro `#chat-log`.** Desde o layout Palco +
   Chat (`PLANO_PALCO_CHAT.md`), todo texto ao vivo/tool-call de um worker
   passa por `_activity()` (aba `⚡ Atividade`), não pelo `#chat-log`. No chat
   só entram: fala do usuário, fala do Orquestrador (inclusive o relato da
   Etapa 4), o resumo final de 1 linha do worker quando o relato falha, e as
   barras de decisão (`plan-bar`/`action-bar`). Narração nova vai pra
   `_activity()` por padrão — só entra no chat se for algo que o usuário
   precisa ler na conversa em si.

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
  "ui": { "mode": "chat", "detail": "normal", "learning_mode": false }
}
```

`load_config()` migra automaticamente configs v1 (orquestrador em `agy`/`claude`,
operários com nomes soltos, `ollama_api_key` em texto puro).

### Como o Orquestrador roda

Ele sempre roda em **modo plano / read-only** — pode LER pra dar contexto melhor,
mas nunca escreve arquivo nem roda comando que altera nada (implementar é sempre
delegado). O botão `Orq:` no topo escolhe o modelo e o `_orch_route()` decide o motor:

| Modelo escolhido | binary | Como conecta |
|---|---|---|
| `gemini-*` | `agy` | **login do Antigravity CLI** (`agy --mode plan`) |
| `claude-*` | `claude` | **login do Claude Code** (`claude --permission-mode plan`) |
| `gpt-*` (grupo Codex) | `codex` | **login do Codex CLI** (`codex exec --sandbox read-only`) |
| `deepseek-v4-*` | `brain` | `~/.claude-deepseek-env` ou `/senha DEEPSEEK_API_KEY` |
| `kimi-*`, `gpt-oss`, ... | `brain` | `kimi provider list` (Ollama Cloud) |
| Anthropic/OpenAI/Gemini via API própria | `brain` | `/senha ANTHROPIC_API_KEY` etc. |

Ou seja: **as contas já logadas no terminal (agy/claude/codex) funcionam sem API key.**
`load_config()` conserta combinações inválidas (ex: `binary=brain provider=copilot`).

### Motores dos operários (`binary=`)

| binary | ferramenta | headless |
|---|---|---|
| `claude` | Claude Code | `-p --output-format stream-json --permission-mode bypassPermissions` |
| `agy` | Antigravity | `-p --output-format stream-json --dangerously-skip-permissions` |
| `codex` | Codex CLI | `exec --json --sandbox workspace-write` |
| `copilot` | `gh copilot` | `-p --no-ask-user` (texto puro) |
| `opencode` | OpenCode (OpenRouter) | `run <instrução> --format json --dir <projeto> --auto` |

Todos aceitam `read_only=True` em `_build_worker_cmd`, que troca as flags de permissão
pra modo plano/read-only (ver regra 7) — usado pelo Modo Aprendizado.

---

## Palco (`#stage-tabs`) + Chat (`#chat-dock`)

Desde `PLANO_PALCO_CHAT.md` a tela não é mais "painéis lado a lado com o chat
numa coluna". `#main-content` (Horizontal) tem só `#file-panel` (toggle) e
`#stage-tabs` (um `TabbedContent` único: `stage-activity`, `stage-changes`,
`stage-editor`, `stage-idea`). `#chat-dock` é um `Vertical` **fora** de
`#main-content` — uma faixa de largura cheia embaixo, não uma coluna.

**IDs fixos que não podem mudar** (código depende deles em vários pontos):
`#chat-log`, `#chat-input`, `#status-indicator`, `#plan-panel`, `#plan-bar`,
`#action-bar`, `#editor-tabs`, `#editor-toolbar`, `#suggestions-panel`,
`#idea-image`, `#idea-md`, `#file-panel`, `#watchdog-footer`, `#usage-footer`,
`#activity-log`, `#diff-log`, `#changes-header`, `#stage-tabs`, `#chat-dock`.

- **`_stage_show(pane_id)`** — único jeito de trocar de aba do palco
  (`self.query_one("#stage-tabs", TabbedContent).active = pane_id`). Não há
  mais painéis com `hidden`/`featured` pro Editor/Idea Studio — são sempre
  panes do mesmo `TabbedContent`.
- **`_activity(icon, who, texto)`** — escreve na aba Atividade, thread-safe
  (chama de qualquer thread). Regra 8 acima: é pra onde vai narração de
  worker, nunca pro `#chat-log`.
- **`_active_workers` (contador, não bool)** — `<DELEGATE>` soltos rodam em
  paralelo; `_start_task`/`_end_task` incrementam/decrementam. `_stage_pinned`
  deixa o usuário "prender" o palco numa aba manualmente durante uma tarefa
  (`on_tabbed_content_tab_activated`), sem o próximo worker do mesmo lote
  puxar de volta pra Atividade.
- **GPS não é `_stage_show`.** `_open_file_in_editor(filepath, switch_stage=True)`
  — a thread GPS (abre no Editor cada arquivo tocado, a cada 2s, DURANTE a
  tarefa) chama com `switch_stage=False` de propósito: ela só deve popular a
  aba no Editor, nunca puxar o palco pra lá enquanto a Atividade está sendo
  seguida. Clique na árvore, `<MOSTRAR>` e aceitar sugestão usam o default
  `True` (ação explícita).
- **`git_diff_for_display`/`_refresh_changes_panel`** — o diff de "± Mudanças"
  NUNCA usa `git add -N` pra ver conteúdo de arquivo novo (leia direto do
  disco) — isso mudaria o código de status de `??` pra "tracked" e quebraria
  `revert_pending` (regra pra qualquer função nova que precise inspecionar
  arquivo não commitado). `RichLog.write()` adia renderização até o widget
  ter tamanho conhecido — o diff só aparece de fato quando o usuário abre a
  aba (não é bug, o badge da aba e o `#changes-header` já avisam antes).

### Editor (dentro da aba `stage-editor`)

Painel com `TabbedContent` — um `TextArea` por aba (`id=f"code-{pane_id}"`), não um
único widget reaproveitado. Estado em dois dicts na `App`:
`self._open_files: dict[pane_id -> {"path","original_text","dirty"}]` e
`self._path_to_pane: dict[caminho_absoluto -> pane_id]` (evita abrir a mesma aba
duas vezes vinda de fontes diferentes — clique na árvore, GPS, `<MOSTRAR>`).

- **`_open_file_in_editor(filepath, switch_stage=True)`** — abre OU foca (nunca
  recarrega do disco se já está aberto, senão perderia edição não salva).
  Retorna o `TextArea` direto (não só o `pane_id`) porque `tabs.add_pane(...)`
  agenda o mount de forma assíncrona — quem chama e precisa mexer no widget na
  mesma sincronia (ex: `_accept_suggestion`) usaria `query_one` e quebraria
  com `NoMatches` (bug real, corrigido — ver `APRENDIZADOS.md`).
- **`action_save_file(editor=None)`** — `Ctrl+S`. Aceita o `TextArea` já resolvido
  opcionalmente, pelo mesmo motivo acima. Também dispara `_refresh_changes_panel()`.
- **Dirty-tracking** — `on_text_area_changed` compara `editor.text` com
  `info["original_text"]` (string inteira, não um bool ingênuo — cobre desfazer até
  o texto original). `TextArea.replace()` (usado por `_accept_suggestion`) dispara
  `Changed` normal, então o dirty-tracking pega de graça.
- **Linguagem por extensão** — `_EXT_TO_LANGUAGE`, só as nativas do Textual
  (`BUILTIN_LANGUAGES`); fora da lista cai em `language=None` (texto puro, sem
  highlight, sem crash).

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
