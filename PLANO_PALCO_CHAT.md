# Plano — layout "Palco + Chat" (estilo Antigravity) na TUI do Orquestra

> Plano de implementação, escrito em 05/09/2026 depois de analisar o código real
> (`orquestra_tui.py`, `orquestra.py`, `~/.codex/AGENTS.md`). Leia `AGENTES.md`
> antes — as 7 "regras que não podem quebrar" continuam valendo.
>
> Objetivo: transformar a TUI de "coluna de chat com tudo misturado" em
> **palco central grande (atividade ao vivo, mudanças/diff, editor) + chat pequeno
> embaixo**, onde o Orquestrador conversa e, ao final, **volta contando o que a
> equipe fez**. Tudo em Textual 8.2.8, sem trocar de framework.
>
> **Revisão (05/09/2026, Sonnet 5):** versão original do Fable revisada contra
> o código real (API do Textual testada ao vivo, `revert_pending`/GPS relidos).
> 6 pontos de atrito corrigidos abaixo, marcados **[REVISADO]** — todos são
> interações entre a ideia nova e comportamento que já existe, não erros de
> não ter lido o projeto.
>
> **Progresso real (não só o documento):** Etapas 0, 1, 2 e 3 implementadas e
> testadas — `test_orquestra.py` verde + boot headless via `app.run_test()`
> cobrindo os atalhos novos, as 4 abas do palco, a distinção GPS/ação-explícita
> do achado nº1, os fluxos reais (Operador de sucesso, Operador com mudança de
> arquivo + gate por risco, `<PIPELINE>` de 2 etapas, `<PIPELINE>` com erro,
> Orquestrador em chat puro) e o diff de verdade na aba Mudanças — tudo rodado
> num repo git isolado em `/tmp` com os agentes mockados (mesma técnica do
> APRENDIZADOS.md §10). **Achado durante a Etapa 3, não previsto no plano
> original:** `RichLog.write()` adia a renderização até o widget ter tamanho
> conhecido (docs do Textual) — como "± Mudanças" pode não ser a aba ativa no
> momento do refresh, o conteúdo do diff só aparece quando o usuário abre a
> aba (fila `_deferred_renders` esvazia no resize). Não é bug: badge da aba +
> `#changes-header` (`Label`, sem esse adiamento) já avisam antes disso —
> confirmado com teste headless, comentado em `_render_changes_panel`. Faltam
> Etapas 4-5 (Orquestrador prestando contas depois de cada tarefa, polimento).

---

## 0. Diagnóstico — como está hoje

### Layout atual

```
┌ top-bar: [Arquivos][Editor][Idea Studio][Aprendizado]     Orq: | Op: | Rev: ┐
├───────────┬────────────┬────────────┬──────────────────────────────────────┤
│ Arquivos  │ Editor     │ Idea       │ 💬 CHAT CENTRAL                      │
│ (oculto)  │ (oculto)   │ (oculto)   │  RichLog #chat-log  ← TUDO cai aqui  │
│           │            │            │  #status-indicator (1 linha)         │
│           │            │            │  #plan-panel / #plan-bar / #action-bar│
│           │            │            │  ChatInput (5 linhas)                │
├───────────┴────────────┴────────────┴──────────────────────────────────────┤
│ 🐕 watchdog-footer (3 linhas)  ·  💰 usage-footer (1 linha)                │
└────────────────────────────────────────────────────────────────────────────┘
```

`#main-content` é `Horizontal`; os painéis são colunas lado a lado e o chat é
**uma coluna à direita**, não uma faixa embaixo. Não existe painel de terminal /
atividade nem visualização de diff. No `#chat-log` caem, misturados: fala do
usuário, fala do Orquestrador, "📤 Prompt para…", texto ao vivo do Operador,
resposta final do Operador, `git diff --stat`, veredito do Cão de Guarda,
mensagens de commit. É por isso que "falta vida" e "a gente fica no escuro":
tudo tem o mesmo peso visual e a atividade não tem um lugar próprio.

### Achados no código (o que está desligado ou contraditório)

| # | Achado | Onde | Efeito |
|---|---|---|---|
| A1 | O stream do **Orquestrador é descartado**: `_TUILiveSection("Orquestrador", lambda t: None, …)`. Só o heartbeat "pensando… (Ns)" chega na tela. | `orquestra_tui.py:_run_orchestrator` | Com o Codex de Orquestrador (que **lê** o repo em `--sandbox read-only`), o usuário não vê "lendo orquestra.py" — parece travado. |
| A2 | `_run_orchestrator_stream` só chama `_capture_orchestrator_result` (pega o resultado final). **Não narra tool calls** — os parsers `_narrate_codex_line`/`_narrate_claude_line`/`_narrate_agy_line` existem mas não são usados nesse caminho. | `orquestra.py:_run_orchestrator_stream` | Mesmo que A1 seja ligado, não haveria eventos pra mostrar. |
| A3 | `/detalhe` **não tem efeito na TUI**: `_run_worker` e `_run_pipeline_tui` chamam `_run_worker_headless(...)` sem `detail=` → sempre `"normal"`. | `orquestra_tui.py:_run_worker`, `_run_pipeline_tui` | O comando salva a config mas nada muda. |
| A4 | No `<PIPELINE>`, `on_line` é `lambda t: None` — o texto ao vivo de cada etapa é jogado fora (só o status de 1 linha aparece). | `orquestra_tui.py:_run_pipeline_tui` | Etapas longas parecem mudas. |
| A5 | O Orquestrador **só "volta e comenta"** (`_orchestrator_react`) na revisão automática do Modo Aprendizado (`orchestrator_react=True`). Depois de um `<DELEGATE>` normal, o chat mostra a resposta crua do Operador e o Orquestrador fica mudo. | `orquestra_tui.py:_run_worker` | É exatamente o "agente volta com a resposta do que fez" que falta. |
| A6 | `_ORCH_SYSTEM` diz "você NÃO tem ferramentas: não lê arquivos" — verdade pro `brain`, **falso pro Codex/Claude/Agy** (modo plano/read-only lê à vontade). O `~/.codex/AGENTS.md` diz o contrário: "pode ler e analisar livremente". | `orquestra.py:_ORCH_SYSTEM` + `~/.codex/AGENTS.md` | O Codex recebe duas instruções opostas no mesmo turno. Ora lê, ora diz que não pode. |
| A7 | `~/.codex/AGENTS.md` é **global** (vale pra todo `codex exec`). Ele proíbe "editar ou criar código". Se o usuário escolher um modelo `gpt-*` no botão `Op:` (Codex como **Operador**, `--sandbox workspace-write`), o Codex vai obedecer o AGENTS.md e recusar editar. | `~/.codex/AGENTS.md` + `orquestra.py:_build_worker_cmd` | Codex como Operador/Revisor fica quebrado silenciosamente. |
| A8 | Timeout do Orquestrador é 150s fixo. Codex lendo um repo grande em read-only pode passar disso. | `orquestra_tui.py:_run_orchestrator` | "⚠ Sem resposta do Orquestrador" sem causa clara. |
| A9 | `~/.codex/AGENTS.md` regra "Sem sugerir alternativas não pedidas" colide com o `_ORCH_SYSTEM` "PRÉVIA VISUAL: primeiro `<DELEGATE binary="open-design">`". Menor, mas é atrito. | ambos | O Codex pode pular a prévia do Open Design. |
| A10 **[REVISADO]** | `/modo chat\|tecnico` (README) promete "bolhas limpas × narração crua", mas a TUI **nunca leu `ui.mode`** — só `/detalhe` (minimal/normal/full) tem efeito real nela, e isso continua assim depois deste plano. | `orquestra_tui.py` (nenhuma referência a `_is_chat_mode`/`ui.mode`) | Usuário troca `/modo tecnico` na TUI e nada muda — o comando certo lá é `/detalhe`. |

Nada disso exige mudar a arquitetura (3 agentes + Cão de Guarda + gates). É
**fiação e layout**. Viável em Textual 8.2.8: layout é CSS; `TabbedContent`,
`RichLog`, `TextArea` já estão em uso; a única coisa que o Textual não tem
nativo é divisor arrastável com mouse — resolve-se com atalhos de teclado.

---

## 1. Alvo — o layout novo

```
┌ top-bar: 🎼 ORQUESTRA  [📁 Arquivos] [🎓 Aprendizado]        Orq: | Op: | Rev: ┐
├──────────┬──────────────────────────────────────────────────────────────────┤
│ 📁       │  ⚡ Atividade ● │ ± Mudanças (3) │ 📝 Editor │ 🎨 Idea Studio     │  ← abas do PALCO
│ Arquivos │──────────────────────────────────────────────────────────────────│
│ (toggle, │  14:02:11 🎼 Orquestrador  📖 Lendo orquestra_tui.py              │
│ oculto   │  14:02:19 🎼 Orquestrador  → plano pronto, aguardando você        │
│ por      │  14:03:02 🔨 Operador      📤 prompt enviado (ver abaixo)          │
│ padrão)  │  14:03:05 🔨 Operador      📖 Lendo orquestra.py                  │
│          │  14:03:20 🔨 Operador      ✏️ Editando orquestra_tui.py            │
│          │  14:03:21 🔨 Operador      💻 Rodando: python3 test_orquestra.py   │
│          │  14:03:40 🔨 Operador      ✅ terminou · 3 arquivos · +41 −12      │
│          │  14:03:41 🐕 Cão de Guarda nada a apontar                         │
├──────────┴──────────────────────────────────────────────────────────────────┤
│ 💬 Orquestrador (codex · gpt-5.6-terra)        ⠹ 🔨 Operador editando… 35s  │  ← chat-header (1 linha)
│ ▶ Você: ajusta o layout do chat                                             │
│ 🎼 Orquestrador: entendi, o plano é …  <PLANO>                              │
│ 🎼 Orquestrador: pronto. O Operador mexeu em 3 arquivos: … Quer revisar?    │  ← "volta contando"
│ [✅ Aprovar] [❌ Rejeitar] [🔍 Revisar]                                      │
│ > _                                                                         │  ← input (3 linhas)
├─────────────────────────────────────────────────────────────────────────────┤
│ 🐕 Cão de Guarda: nada a apontar ✅        💰 Uso: $0.12 · 4 chamadas        │
└─────────────────────────────────────────────────────────────────────────────┘
```

Princípios:

- **Palco (`#stage`) em cima, grande.** É onde a coisa acontece: atividade ao
  vivo (o "terminal"), diff das mudanças, editor, prévia visual.
- **Chat (`#chat-dock`) embaixo, pequeno e limpo.** Só conversa: você, o
  Orquestrador, e o resumo final de cada agente. Nada de narração crua aqui.
- **Uma linha de status sempre visível** (`#chat-header`), com spinner e tempo,
  pra nunca parecer travado.
- **O Orquestrador presta contas** ao fim de cada tarefa (regra 4 do próprio
  `AGENTS.md` do Codex) — a bolha final no chat é dele, não do Operador cru.
- **Palco segue a ação**: quando um worker começa, a aba Atividade entra em
  foco; quando termina com arquivos tocados, a aba Mudanças ganha um badge.

Alturas: chat-dock nasce com 14 linhas (min 8); `Ctrl+↑`/`Ctrl+↓` alternam
entre compacto (8) · normal (14) · metade da tela (50%). Input com 3 linhas
(hoje são 5 — no dock isso rouba espaço demais).

---

## 2. Etapas de implementação (ordem obrigatória, 1 commit por etapa)

Cada etapa deixa a TUI funcionando. Rode `python3 test_orquestra.py` e um boot
real (`orquestra` numa pasta de teste com git) no fim de cada uma.

### Etapa 0 — Preparo no backend (`orquestra.py`), sem mexer na TUI

**0a. Narração do Orquestrador (resolve A2).** Em `_run_orchestrator_stream`,
além de `_capture_orchestrator_result`, chamar o parser de narração do motor
(`_narrate_codex_line` / `_narrate_claude_line` / `_narrate_agy_line`) com um
`detail` novo (parâmetro `detail: str = "normal"`). Cuidado: os parsers também
escrevem em `state["final_text"]` — manter `_capture_orchestrator_result` como
fonte da verdade do resultado (e o arquivo `-o` no Codex) e usar o parser **só
pelos efeitos de `section.tool_call`/`section.write`**. Jeito simples: passar ao
parser um `state` descartável (`dict` separado) e a `section` real. Pro `brain`
não há tool calls — nada muda.

**[REVISADO] Cuidado com o Codex especificamente:** `_narrate_codex_line` HOJE
só produz `tool_call(f"{item_type}...")` genérico ("command_execution...",
"reasoning...") pra qualquer coisa que não seja `agent_message`/erro — **não
existe** o equivalente ao `_friendly_tool_call` do Claude (que vira "📖 Lendo
X"). Antes de prometer "🎼 Orquestrador 📖 Lendo orquestra_tui.py" na
Atividade (como no mockup da seção 1), rode `codex exec --json` de verdade
numa tarefa de leitura e confira o schema real de `item_type` que ele emite
pra comando/leitura de arquivo — só depois escreva um tradutor tipo
`_friendly_tool_call` pro Codex. Sem isso, a Atividade com Codex de
Orquestrador vai mostrar linhas genéricas, não as amigáveis do mockup.

**0b. Prompt do Orquestrador coerente com o motor (resolve A6).** Em
`build_orchestrator_prompt(config, history, memory_context)`, o bloco
"QUEM VOCÊ É" passa a depender de `config["roles"]["orquestrador"]["binary"]`:

- `brain`/`ollama-cloud` → texto atual ("NÃO tem ferramentas, não lê arquivos").
- `agy`/`claude`/`codex` → "Você roda em modo plano/read-only: **pode ler**
  arquivos e rodar comandos de leitura pra entender o projeto, **mas não escreve
  arquivo nem roda nada que altere o estado** — implementar é sempre delegado".

Regra 2 de `AGENTES.md` (fonte única do prompt) continua valendo: é a mesma
função, só com um `if`. O marcador `"Histórico da Conversa:\n"` não muda.
Adicionar um teste em `test_orquestra.py` pros dois ramos.

**0c. Codex como Operador/Revisor (resolve A7).** Em `_build_worker_cmd`, no
ramo `codex`, prefixar a instrução:

```
[PAPEL: OPERADOR — executor com permissão de escrita]
As regras do AGENTS.md sobre "Orquestrador" NÃO se aplicam a esta execução:
você é o Operador descrito naquele arquivo. Execute a tarefa abaixo de verdade
(editar/criar arquivos, rodar comandos). Ao terminar, resuma o que fez.
```

(`REVISOR — só leitura` quando `read_only=True`.) E no `~/.codex/AGENTS.md`,
acrescentar uma seção curta no topo: "Se a instrução começar com `[PAPEL: …]`,
esse papel prevalece sobre este arquivo". Sem isso o AGENTS.md global sabota o
Codex como operário.

**0d. Timeout por motor (resolve A8).** `_run_orchestrator` usa
`timeout_s=300 if binary == "codex" else 150`. Trivial.

**0e. `_TUILiveSection`** (`orquestra_tui.py`, mas é preparo): ganha um
callback opcional `on_tool(text)` separado de `on_status`. Hoje `tool_call`
vai só pro status; no layout novo cada tool call vira **uma linha na
Atividade** e também atualiza o status. Manter compatibilidade: se `on_tool`
for `None`, comporta-se como hoje.

### Etapa 1 — Só layout (`orquestra_tui.py`: `compose` + `CSS`), zero lógica nova

Novo `compose`:

```python
with Container(id="top-bar"):              # igual, menos os botões Editor/Idea
    with Container(id="controls"):
        yield Label("🎼 ORQUESTRA", id="app-title")
        yield Button("📁 Arquivos", id="btn-files", classes="toggle-btn")
        yield Button("🎓 Aprendizado", id="btn-learning", classes="toggle-btn")
    with Container(id="models"): ...        # Orq:/Op:/Rev: iguais

with Horizontal(id="main-content"):
    with Vertical(id="file-panel", classes="panel hidden"):   # igual
        yield Label("📁 ARQUIVOS", classes="panel-title")
        yield DirectoryTree("./")
    with TabbedContent(id="stage-tabs", initial="stage-activity"):
        with TabPane("⚡ Atividade", id="stage-activity"):
            yield RichLog(id="activity-log", markup=True, wrap=True, min_width=1)
        with TabPane("± Mudanças", id="stage-changes"):
            yield Label("Nenhuma mudança pendente.", id="changes-header")
            yield RichLog(id="diff-log", markup=True, wrap=False, min_width=1)
        with TabPane("📝 Editor", id="stage-editor"):
            with Container(id="editor-toolbar", classes="hidden"): ...   # igual
            yield Vertical(id="suggestions-panel", classes="hidden")     # igual
            yield TabbedContent(id="editor-tabs")                        # igual
        with TabPane("🎨 Idea Studio", id="stage-idea"):
            yield Container(id="idea-image")                             # igual
            yield Markdown(..., id="idea-md")                            # igual

with Vertical(id="chat-dock"):
    with Horizontal(id="chat-header"):
        yield Label("💬 Orquestrador", id="chat-title")
        yield Label("", id="status-indicator")          # mesmo id, agora sempre visível aqui
    yield RichLog(id="chat-log", markup=True, wrap=True, min_width=1)
    yield Vertical(id="plan-panel", classes="hidden")   # igual
    with Container(id="plan-bar", classes="hidden"): ...   # igual
    with Container(id="action-bar", classes="hidden"): ... # igual
    yield ChatInput(placeholder="...", id="chat-input")

yield Label("🐕 ...", id="watchdog-footer")
yield Label("💰 ...", id="usage-footer")
```

CSS essencial (o resto se mantém):

```css
Screen { layout: vertical; }
#main-content { height: 1fr; layout: horizontal; }
#stage-tabs { width: 1fr; height: 1fr; }
#chat-dock { height: 14; min-height: 8; border-top: heavy $primary; background: $surface; }
#chat-dock.compact { height: 8; }
#chat-dock.tall { height: 50%; }
#chat-header { height: 1; background: $boost; padding: 0 1; }
#chat-title { width: auto; color: $primary; text-style: bold; }
#status-indicator { width: 1fr; text-align: right; color: $text-muted; }
#chat-log { height: 1fr; padding: 0 1; }
#chat-input { height: 3; }            /* era 5; REMOVER o bloco `ChatInput { dock: bottom; margin: 1 }` */
#activity-log, #diff-log { height: 1fr; }
#changes-header { height: 1; padding: 0 1; background: $boost; }
```

**IDs que NÃO podem mudar** (código existente depende deles): `#chat-log`,
`#chat-input`, `#status-indicator`, `#plan-panel`, `#plan-bar`, `#action-bar`,
`#editor-tabs`, `#editor-toolbar`, `#suggestions-panel`, `#idea-image`,
`#idea-md`, `#file-panel`, `#watchdog-footer`, `#usage-footer`, `#btn-files`,
`#btn-learning`, `#btn-sel-*`, `#btn-deep-review`, `#btn-exec-plan`,
`#btn-adjust-plan`, `#btn-approve/reject/review`.

**Referências a remover/trocar** (grep confirmado, 05/09/2026):
`#editor-panel` (CSS 323, código 900, 955), `#idea-panel` (CSS 327/331, código
1747), `btn-editor` (481, 762, 903-904, 958), `btn-idea` (482, 763, 1750),
classe `featured` (331, 772, 1749), `_reveal_editor_panel` (926, 950, 953).
Substituir todos por um helper único:

```python
def _stage_show(self, pane_id: str) -> None:
    """Traz uma aba do palco pra frente (thread da UI)."""
    self.query_one("#stage-tabs", TabbedContent).active = pane_id
```

- `_reveal_editor_panel()` → `self._stage_show("stage-editor")`.
- Open Design (`_run_open_design`, ~1747) → `self._stage_show("stage-idea")`
  em vez de `featured`.
- `_handle_approval("review")` (~900) → `self._stage_show("stage-changes")`.
- `on_button_pressed`: `panel_map` fica só com `btn-files`.

Atalhos novos em `BINDINGS`:

| Tecla | Ação |
|---|---|
| `Ctrl+1` … `Ctrl+4` | aba do palco (Atividade, Mudanças, Editor, Idea) |
| `Ctrl+B` | mostra/oculta Arquivos |
| `Ctrl+↑` / `Ctrl+↓` | chat-dock: compact ↔ normal ↔ tall (troca as classes `compact`/`tall`) |
| `Ctrl+L` | foco no input do chat |
| `Ctrl+S` / `Ctrl+W` | iguais (Editor) |

Regra do `NOTAS_TUI.md` (`.model-btn min-width: 25`, `shorten_name`) continua
intocada — com os botões Editor/Idea fora do header, sobra até mais espaço.

Critério de pronto: TUI abre, chat funciona igual a antes (tudo ainda cai no
`#chat-log`), abas do palco trocam, editor abre arquivo pela árvore e pelo GPS,
`Ctrl+↑/↓` redimensiona. `test_orquestra.py` verde.

### Etapa 2 — Roteamento: o que vai pro palco e o que fica no chat (a "vida")

Helper central (thread-safe, usa `call_from_thread` por dentro):

```python
def _activity(self, who_icon: str, who: str, text: str, style: str = "") -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[dim]{ts}[/dim] {who_icon} [bold]{who}[/bold]  {text}"
    self.call_from_thread(self.query_one("#activity-log", RichLog).write, line)
```

E um `_set_status(text)` único (substitui os 3 `on_status` duplicados em
`_run_orchestrator`, `_run_worker`, `_run_pipeline_tui`) que atualiza o
`#status-indicator` do `#chat-header`.

Roteamento por evento:

| Evento | Palco: Atividade | Chat |
|---|---|---|
| Usuário envia | — | `▶ Você: …` (igual) |
| Orquestrador pensando / tool call (0a) | `🎼 Orquestrador 📖 Lendo X` | status: `⠹ 🎼 pensando… 12s` |
| Orquestrador responde (texto visível) | `🎼 Orquestrador → respondeu` (1 linha) | bolha 🎼 (igual) |
| `<PLANO>` | — | bloco do plano + plan-bar (igual) |
| Worker dispara | `📤 Prompt para 🔨 Operador:` + instrução (até 500 chars) | 1 linha dim: `🔨 Operador começou · acompanhe em ⚡ Atividade (Ctrl+1)` |
| Worker `write` (texto ao vivo) | linha por texto | — |
| Worker `tool_call` | linha `✏️ Editando X` (via `on_tool`, 0e) | status: `⠹ 🔨 Operador ✏️ Editando X · 35s` |
| Worker termina OK | `✅ terminou · N arquivos · +a −b` + **texto final completo** | bolha `🔨 Operador` com texto até 1200 chars (Etapa 4 troca isso pela fala do Orquestrador) |
| Worker erro | `❌ erro: …` (detalhe completo) | `⚠ Erro no Operador: …` (até 300 chars) |
| `changes_summary` | vai pro cabeçalho de **Mudanças** (Etapa 3) | 1 linha: `± 3 arquivos · ver Mudanças (Ctrl+2)` |
| Cão de Guarda | linha `🐕 …` | só rodapé (igual); bolha vermelha no chat **só** se achou bug (igual) |
| Commit / revert | linha `📦 commit abc123` | 1 linha dim (igual) |

Mudanças concretas:

- `_run_worker` e `_run_pipeline_tui`: `on_line` → `_activity(...)` (o pipeline
  para de passar `lambda t: None` — resolve A4); passar
  `detail=orquestra._detail_level(config)` a `_run_worker_headless` (resolve A3).
- `_run_orchestrator`: `section = _TUILiveSection("Orquestrador", on_line=_activity, …, on_tool=_activity)` (resolve A1).
- **[REVISADO] Contador de tarefas ativas, não bool.** `<DELEGATE>` soltos já
  rodam em paralelo (várias chamadas de `_run_worker` disparadas sem esperar
  uma da outra, cada uma um `@work(thread=True)` próprio). Um `_task_running:
  bool` zeraria assim que o PRIMEIRO worker paralelo terminasse, mesmo com o
  segundo ainda rodando (spinner para cedo, palco despina antes da hora). Usar
  `self._active_workers: int = 0`, incrementado ao disparar cada worker e
  decrementado no fim de cada um (`try/finally`); `_task_running` vira uma
  property (`self._active_workers > 0`).
- **Palco segue a ação**: ao disparar um worker, `self._stage_show("stage-activity")`
  a menos que `self._stage_pinned` seja `True`. `_stage_pinned` vira `True`
  quando o usuário troca de aba manualmente **durante** uma tarefa
  (`on_tabbed_content_tab_activated` + `self._task_running`) e volta a `False`
  quando `_active_workers` chega a 0.
- **[REVISADO] GPS não pode chamar `_stage_show`.** `_open_file_in_editor` é
  chamado por 3 fontes: clique na árvore (`on_directory_tree_file_selected`),
  `<MOSTRAR>` do Orquestrador, e o GPS (thread que abre no Editor cada arquivo
  tocado, a cada 2s, **durante** a tarefa). As duas primeiras são ação
  explícita — legítimo trocar de aba pro Editor. O GPS **não é** — se
  `_reveal_editor_panel()`/`_stage_show("stage-editor")` for chamado ali sem
  distinção, o palco vai ser puxado da Atividade pro Editor a cada arquivo
  novo tocado pelo Operador, brigando com a própria promessa desta etapa.
  Fix: `_open_file_in_editor(filepath, switch_stage: bool = True)` — a
  `_gps_watch` (dentro de `_run_worker`) chama com `switch_stage=False`; os
  outros dois call sites usam o default `True`. Só quando `switch_stage=True`
  o método chama `self._stage_show("stage-editor")` no lugar do
  `_reveal_editor_panel()` de hoje.
- Spinner: `set_interval(0.12, self._tick_spinner)` só enquanto
  `self._active_workers > 0`; `_tick_spinner` troca o primeiro caractere do
  status por `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` e atualiza o contador de segundos. Parar o timer
  quando `_active_workers` chega a 0 (`timer.stop()`).
- Título da aba: `⚡ Atividade ●` enquanto roda; `± Mudanças (3)` quando há
  arquivos pendentes (`self.query_one("#stage-tabs").get_tab("stage-changes").label = ...`).
- `notify()` (toast do Textual) quando um worker termina e a aba ativa não é
  Atividade.
- **[REVISADO]** `/modo` (A10) fica fora de escopo desta etapa — documentar
  em `README.md` (seção 4) que `/modo chat|tecnico` é só do REPL antigo; na
  TUI quem manda é `/detalhe`. Não acoplar os dois automaticamente (um
  usuário que já ajustou `/detalhe` na mão não deveria levar surpresa se
  depois digitar `/modo`).

### Etapa 3 — Aba "± Mudanças" (o diff de verdade)

Função pura em `orquestra.py` (testável sem TUI):

```python
def git_diff_for_display(cwd: str, max_lines_per_file: int = 400) -> tuple[str, list[dict]]:
    """Retorna (stat_text, [{"path", "added", "removed", "diff"}]) do diff
    não-commitado. Cwd sem git → ("", [])."""
```

**[REVISADO] Como pegar o conteúdo de arquivo NOVO sem tocar no índice do
git.** `git diff` sozinho não mostra conteúdo de arquivo não rastreado (`??`
no `git status --porcelain`) — só de arquivo já rastreado. A tentação óbvia é
`git add -N` (intent-to-add) antes do diff, só que isso muda o código do
`git status --porcelain` daquele arquivo de `??` pra um código de "tracked",
e `revert_pending` (já existe, `orquestra.py`) decide `git checkout HEAD`
(rastreado) vs `git clean -fd` (novo) **exatamente por esse código**. Um
arquivo novo "marcado" por `git add -N` e não desfeito depois viraria
candidato a `checkout HEAD` — que não faz nada, porque o arquivo não existe
no HEAD — e o "Rejeitar" pararia de limpar arquivos novos de verdade.
**Não usar `git add -N`.** Em vez disso: pra cada `path` em
`_git_status_paths(cwd)` com código `??`, ler o conteúdo direto do disco
(`Path(cwd, path).read_text()`) e montar um diff sintético (todas as linhas
como adição) sem chamar `git add` em nada — zero efeito colateral no índice.
Pra arquivo já rastreado, `git diff -- <path>` normal. Contagem de
`added`/`removed` por `git diff --numstat -- <path>` (mais robusto que contar
`+`/`-` manualmente — evita erro com linhas de cabeçalho `+++`/`---` ou
arquivo binário).

Render na TUI (`_refresh_changes_panel`, thread da UI):

- `#changes-header`: `3 arquivos · +41 −12 · aguardando aprovação` (ou
  `Nenhuma mudança pendente.` / aviso de "pasta sem git" já existente).
- `#diff-log`: por arquivo, um cabeçalho `━━ orquestra_tui.py (+30 −5)` e o
  diff com cor manual por linha: `+` verde, `-` vermelho, `@@` cyan, resto dim.
  (Mais confiável que `rich.syntax.Syntax(..., "diff")` num `RichLog` com
  `wrap=False`; manter simples.) Escape de markup: usar `rich.markup.escape`
  em cada linha, senão `[` no código quebra o RichLog.

Gatilhos do refresh: (1) a thread GPS que já roda `git diff --name-only` a cada
2s — quando o conjunto de arquivos muda, chama o refresh; (2) `on_close` do
worker; (3) depois de Aprovar/Rejeitar (limpa); (4) `action_save_file` (edição
manual no Modo Aprendizado). Tudo via `call_from_thread`.

A barra ✅/❌/🔍 continua **só no chat-dock** (fonte única; não duplicar
botões no palco — `_handle_approval` já esconde/mostra por id).

### Etapa 4 — O Orquestrador volta e conta o que fez (resolve A5)

Config nova em `DEFAULT_CONFIG["ui"]`: `"orchestrator_reports": true`
(+ comando `/relato on|off` no `_handle_slash`, mesmo padrão do `/risco`).

**[REVISADO] Não misturar com o parâmetro que já existe.** `_run_worker` já
tem `orchestrator_react: bool = False`, hoje só ligado pelo caller da revisão
automática do Modo Aprendizado — que é **sempre** `read_only=True`. Esta
etapa é sobre o caminho `read_only=False` (Operador de verdade), então os
dois convivem sem colisão **desde que fiquem separados**: não reaproveite o
parâmetro `orchestrator_react` pro caminho novo. Dentro de `_run_worker`, o
gatilho da Etapa 4 é uma checagem própria, independente do parâmetro:
`if not read_only and not is_error and config.get("ui", {}).get("orchestrator_reports", True):`.
Se alguém tentar unificar os dois num único flag, quebra o Modo Aprendizado
(que precisa continuar reagindo mesmo com essa config nova desligada, porque
é um recurso à parte).

Em `_run_worker` (não `read_only`) e ao **fim de cada etapa** do
`_run_pipeline_tui`, quando o worker termina sem erro e a config está ligada:

```python
comment = orquestra._orchestrator_react(config, label, final_text + "\n\n" + changes_summary, section=react_section)
```

- Se `comment` veio: chat mostra **só** a bolha 🎼 do Orquestrador (o texto
  cru do worker já está na Atividade). Salvar no histórico como hoje.
- Se falhou/timeout: cai no comportamento atual (bolha crua do worker no chat).
- Status durante a reação: `⠹ 🎼 Orquestrador resumindo…`.
- Ajustar o prompt de `_orchestrator_react` pra incluir a linha de mudanças
  ("mexeu em X arquivos: …") e pedir explicitamente: "diga o que foi feito, o
  que mudou e o que falta; se houver arquivos pendentes, lembre que ele pode
  Aprovar/Rejeitar/Revisar". É a regra 4 ("Prestar contas") do
  `~/.codex/AGENTS.md`, agora acontecendo de verdade na TUI.
- Custo: +1 chamada do Orquestrador por worker. Com Codex/Agy/Claude é cota de
  assinatura; com `brain` (DeepSeek) é centavos. O `/uso` já contabiliza.
- Não reagir no Cão de Guarda nem no Revisor do botão "🔍 Revisar" (esses já
  são "o retorno" por natureza) — só em Operador e etapas de pipeline.

### Etapa 5 — Polimento (opcional, depois das anteriores)

- Cor por agente na Atividade: Orquestrador cyan, Operador green, Revisor blue,
  Cão de Guarda yellow (usar `worker_cfg["color"]` que já existe na config).
- `Ctrl+K` limpa a Atividade; a Atividade guarda no máximo 2000 linhas
  (`RichLog(max_lines=2000)`).
- "📤 Prompt para…" na Atividade colapsado: primeira linha + `(+N linhas)`,
  `detail=full` mostra inteiro.
- Persistir `ui.chat_dock_size` (`compact|normal|tall`) e `ui.stage_follow`.
- Codex: `codex exec resume --last` existe no 0.152 (confirmado no `--help`).
  Hoje cada turno reenvia o histórico inteiro no prompt. **Não** trocar agora —
  só anotar no ROADMAP; exige testar se o `resume` mantém o `--sandbox
  read-only` e o `-o`.

---

## 3. Testes e validação

`test_orquestra.py` (sem rede), acrescentar:

- `build_orchestrator_prompt` com `binary=brain` contém "NÃO tem ferramentas";
  com `binary=codex` contém "pode ler" e **não** contém "não lê arquivos".
- `_build_worker_cmd({...}, "codex", "faz X")` → `cmd[2]` começa com
  `[PAPEL: OPERADOR`; com `read_only=True` → `[PAPEL: REVISOR`.
- `git_diff_for_display` num repo temporário em `/tmp` (mesma técnica do teste
  do gate por risco em `APRENDIZADOS.md §10`): 1 arquivo editado → 1 entrada,
  `added`/`removed` corretos; pasta sem git → `("", [])`.
- Boot da TUI via `app.run_test()`: existem `#stage-tabs` com 4 panes,
  `#chat-dock`, `#activity-log`, `#diff-log`; `Ctrl+2` ativa `stage-changes`;
  `Ctrl+↓` adiciona classe `compact`. Lembrete do §10: esperar por sinal
  concreto, nunca por `app.workers` vazio (`_load_models_and_config` demora).

Validação manual (checklist pra colar no `APRENDIZADOS.md §11`):

1. Pasta de teste **com git** (`/tmp/orq_palco && git init`), Orquestrador =
   Codex. Pedir "cria um hello.py". Ver: Atividade mostra o Codex lendo; plano
   no chat; Executar → Operador narrando na Atividade; aba Mudanças com badge
   e diff colorido; Orquestrador volta com resumo; Aprovar → commit → Mudanças
   limpa.
2. Trocar `Op:` pra um `gpt-*` (Codex como Operador) e repetir — ele **deve
   editar** (era A7).
3. `/detalhe full` → tool calls cruas na Atividade; `/detalhe minimal` → só
   fala (era A3).
4. `<PIPELINE>` de 2 etapas — texto ao vivo de cada etapa na Atividade (era A4).
5. Pasta **sem git**: aviso amarelo no chat (já existe) + aba Mudanças mostra
   o mesmo aviso em vez de ficar vazia.
6. Terminal de 80 colunas: header não quebra (`NOTAS_TUI.md`).

---

## 4. Documentação a atualizar (no mesmo PR de cada etapa)

- `README.md` — seção "Como funciona um turno" e nova seção "A tela" com o
  mockup; tabela de atalhos; nota explícita de que `/modo chat|tecnico` é só
  do REPL antigo — na TUI quem controla o nível de detalhe é `/detalhe` (A10).
- `AGENTES.md` — trocar a seção "Editor" por "Palco (`#stage-tabs`) + Chat
  (`#chat-dock`)", listar os IDs fixos e o helper `_stage_show`; nova regra 8:
  "**Narração de worker nunca vai pro `#chat-log`** — vai pra `_activity()`.
  No chat só entram: usuário, Orquestrador, resumo final, barras de decisão."
- `NOTAS_TUI.md` — nota de que Editor/Idea saíram do header.
- `CHANGELOG.md` — "V6 — Palco + Chat (set/2026)".
- `APRENDIZADOS.md §11` — o que deu errado/certo na implementação.
- `ROADMAP.md` — marcar feito; anotar `codex exec resume --last` como ideia.
- `~/.codex/AGENTS.md` — seção `[PAPEL: …]` (0c) e afrouxar "sem sugerir
  alternativas" pra "exceto a prévia visual do Open Design quando a mudança é
  de interface" (A9).

---

## 5. Gotchas pra quem for implementar (Sonnet 5, leia isto)

- **Toda atualização de widget vinda de thread** (`_run_worker`,
  `_run_orchestrator`, `_run_pipeline_tui`, GPS, watchdog) passa por
  `self.call_from_thread(...)`. Sem exceção. É a causa nº 1 de tela corrompida
  neste projeto (`APRENDIZADOS.md`, bug do `patch_stdout`).
- `TabbedContent.add_pane` monta de forma **assíncrona** — não faça
  `query_one` no pane recém-criado na mesma chamada (`AGENTES.md`, seção
  Editor). O palco tem panes fixos no `compose`, então isso só afeta o
  `#editor-tabs` (que já está resolvido).
- `RichLog(min_width=1)` em **todos** os logs novos, senão o texto vaza
  horizontalmente (fix da V5).
- `rich.markup.escape()` em qualquer texto de usuário/diff/tool call antes de
  `RichLog.write` com `markup=True`.
- Remover o bloco CSS `ChatInput { dock: bottom; margin: 1 }` — dentro do
  `#chat-dock` (layout vertical) ele não é mais necessário e briga com a
  altura fixa.
- `_run_worker_headless`, `_resolve_worker`, `_build_worker_cmd`,
  `_build_orchestrator_cmd`, `build_orchestrator_prompt` são compartilhadas
  com o REPL (`--repl`) — regra 6 do `AGENTES.md`. Toda mudança de assinatura
  precisa de parâmetro com default e grep em **todos** os call sites (o §10
  conta o bug do `WorkerResult` que só apareceu no REPL).
- Não mexer em `.model-btn { min-width: 25 }` (`NOTAS_TUI.md`).
- Não dar ferramentas ao Orquestrador (regra 1). A Etapa 0a só **mostra** o
  que ele já faz em read-only; não muda flag nenhuma de permissão.
- O `#status-indicator` deixa de usar `add_class("hidden")`/`remove_class`
  — agora fica sempre visível no `#chat-header` e recebe `""` quando ocioso.
  Grep por `status_lbl.add_class("hidden")` e remover.
- Commits: um por etapa, mensagem em português no padrão do repo
  (`feat(tui): …`, `fix(orq): …`), sem commitar antes dos testes passarem.
