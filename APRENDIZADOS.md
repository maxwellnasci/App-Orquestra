# Aprendizados — Refatoração de 3 agentes (set/2026)

Registro do que descobrimos ao consolidar o Orquestra na TUI e religar as
conexões. Foco no que **não é óbvio** e no que quebrou. Para a arquitetura em si,
ver `AGENTES.md`; para uso, `README.md`.

---

## 1. Como cada agente CONECTA (a parte que confundiu)

Há **duas formas** de um modelo conectar no Orquestra, e elas não são
intercambiáveis:

### a) Conta logada no terminal (OAuth da CLI) — sem API key

`agy`, `claude` e `codex` já vêm autenticados na máquina (`agy`, `claude`,
`gh copilot`, `codex` no PATH). O Orquestra chama a CLI e ela usa esse login.

| Motor | binary | Usado como |
|---|---|---|
| Antigravity (Gemini) | `agy` | Operador, ou Orquestrador (`--mode plan`) |
| Claude Code | `claude` | Operador/Revisor, ou Orquestrador (`--permission-mode plan`) |
| Codex (OpenAI) | `codex` | Operador, ou Orquestrador (`codex exec --sandbox read-only`) |
| GitHub Copilot | `copilot` (`gh copilot`) | Cão de Guarda |

### b) API key (arquivo ou cofre) — NÃO tem login de CLI

| Motor | Como | Onde |
|---|---|---|
| **DeepSeek** | API key `sk-...` | `~/.claude-deepseek-env` → `ANTHROPIC_API_KEY` (ou `/senha DEEPSEEK_API_KEY`) |
| **Ollama Cloud** (Kimi, gpt-oss, nemotron...) | key do `kimi` CLI | `kimi provider list --json` → `.providers.ollama-cloud.apiKey` |
| Anthropic / OpenAI / Gemini "puros" | API key própria | `/senha ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` |

**"claude-deepseek" é um truque:** roda a CLI do Claude Code (`claude`) com
`ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` + a API key do DeepSeek.
O DeepSeek fez a API deles compatível com o formato Anthropic, então o `claude`
funciona apontado pra lá. **Não existe "CLI do DeepSeek"** neste projeto.

**Gemini API key ≠ login do `agy`.** A chave do Google AI Studio é uma coisa; o
login do Antigravity CLI é outra. Se você escolher um modelo Gemini pro
Orquestrador, ele vai pelo `agy` (login), não pela API key.

---

## 2. Como o ORQUESTRADOR roda

Sempre em **modo plano / read-only**: pode LER pra dar contexto melhor, mas nunca
escreve arquivo nem roda comando que altera nada. Implementar é sempre delegado.

`_orch_route(modelo)` decide o motor:

```
gemini-*        → agy --mode plan                    (login Antigravity)
claude-*        → claude --permission-mode plan      (login Claude Code)
gpt-* (Codex)   → codex exec --sandbox read-only     (login Codex)
deepseek-v4-*   → brain / provider=deepseek          (API key do arquivo)
kimi/gpt-oss/…  → brain / provider=ollama-cloud      (key do kimi CLI)
```

`binary="brain"` = `orchestrator_brain.py`, chat completions puro (zero
ferramentas por construção). Os outros usam a CLI em modo restrito.

**Por que não deixar o Orquestrador com ferramentas totais:** o `agy`/`claude`
executam comando mesmo pedindo pra não executar (`--disallowedTools` não
segura, spike confirmou). Só o modo **plan** trava a escrita de verdade. E o
`brain` é 100% incapaz de tocar em arquivo.

---

## 3. Bugs encontrados e corrigidos

### 3.1 O `agy` não enxergava a pasta do projeto  *(commit 19902c7)*

`_build_worker_cmd` chamava `agy -p ... --project <hash> --add-dir .`

- `--project <hash>` → o `agy` operava no **workspace-brain dele**
  (`~/.gemini/antigravity-cli/brain/<id>`), não na pasta real. `pwd` dava
  `/home/max/.gemini/...`, `cat arquivo` falhava.
- `--add-dir .` → o `.` era resolvido relativo ao scratch do `agy`, não ao cwd
  do processo.

**Fix:** removido `--project`; `--add-dir` agora é **caminho absoluto**
(`get_project_dir()`). Testado: `agy` lê os arquivos reais e roda comandos na
pasta certa.

> Regra: pro `agy`, `--add-dir` **sempre absoluto**, e **nunca** `--project`
> quando a intenção é trabalhar numa pasta externa.

### 3.2 Orquestrador só funcionava com API key  *(commit 14686b0)*

O refactor tinha trocado o Orquestrador pra `brain` (chat API). Gemini/Claude/GPT
por chat API precisam de API key própria — que o usuário **não tem**. Só DeepSeek
e Ollama funcionavam. As contas `agy`/`claude`/`codex` logadas ficaram sem uso
pelo Orquestrador.

Pior: o picker deixou salvar `{"binary":"brain","provider":"copilot"}` — o
`orchestrator_brain.py` não fala "copilot" → **erro em toda mensagem** =
"nada funciona".

**Fix:** Orquestrador volta a usar as CLIs logadas, em modo plano (ver §2).
`load_config()` conserta `provider` inválido re-roteando pelo nome do modelo.

### 3.3 Modelo de chat escolhido pra Operador/Revisor  *(commit 19902c7)*

O picker deixava escolher `kimi-k3` / `gpt-oss:120b` pro Operador. Esses não têm
CLI de agente → `_build_worker_cmd` rodava `claude --model kimi-...` → o `claude`
rejeitava o modelo → operário "não funcionava" **sem erro claro**.

**Fix:** picker do Operador/Revisor **não mostra mais Ollama Cloud**. Se um
modelo de chat puro for escolhido, avisa e não troca. `_build_worker_cmd` só
passa `--model` pro `claude` se for `claude-*`/`deepseek-*`.

### 3.4 O Cão de Guarda era código morto  *(commit aaf42d4)*

`_run_copilot_watchdog()` definia a thread interna e **nunca a disparava**. A
auditoria automática do `git diff` nunca rodou. **Fix:** dispara a thread de
verdade, 1x por turno (cooldown de 15s), com o `git diff` já pronto no prompt.

### 3.5 Texto alucinado depois de `<DELEGATE>`  *(commit 6b129a1)*

O cérebro às vezes escreve um "resultado" falso depois da tag `<DELEGATE>`.
**Fix:** só mostra o texto que vem **antes da 1ª tag** (`<PLANO>`/`<PIPELINE>`/
`<DELEGATE>`).

### 3.6 O `orquestra` abria o Cursor no boot  *(commit bdbaee0)*

`_startup()` da TUI abria `.orquestra_logs.md` no Cursor e subia um bridge HTTP —
sobras do REPL antigo. A TUI tem painel de log/editor próprios. **Fix:** removido.
`orquestra` abre **só a TUI**.

### 3.7 `__pycache__` / `*preview*.html` na aprovação  *(commits 6b129a1, 8a2ba1d)*

Artefatos que o operário/Open Design geram de passagem apareciam como "arquivos
alterados". **Fix:** `_is_noise_path()` filtra `__pycache__`, `*.pyc`,
`.orquestra_preview.*`, `*preview*.html`, `.pytest_cache`.

---

## 4. Open Design

- **Nunca tinha sido ligado.** A seção `open_design` não estava no
  `~/.orquestra.json` (nem no backup pré-refactor). As validações antigas foram
  feitas chamando as funções Python direto num teste, não pelo fluxo normal.
- **Agora:** seção `open_design` no `DEFAULT_CONFIG` (auto-semeada se o clone
  existir em `~/Documentos/Kali Linux/open-design`), `<DELEGATE binary="open-design">`
  e `/design` funcionando na TUI.
- **Agente que o Open Design usa:** **Claude Code** (`--agent claude`), forçado
  pelo Orquestra. O default de fábrica é `antigravity`/Gemini, que inventa
  paletas genéricas em vez de ler o design system real (lição de 08/2026).
- **`linkedDirs` NÃO é read-only** — o Open Design às vezes escreve nos arquivos
  reais mesmo em "prévia". O Orquestra tira snapshot `git` antes/depois e avisa;
  na TUI cai no gate Aprovar/Rejeitar.
- **Prévia renderizada dentro do Orquestra:** o Open Design salva um
  `.orquestra_preview.html` autossuficiente → `chromium --headless --screenshot`
  → imagem na aba Idea Studio via `textual-image`. Fidelidade depende do terminal
  (protocolo de imagem no Kitty/WezTerm/Ghostty/Konsole/terminal do Cursor;
  half-blocks no qterminal). Cópia em `.orquestra_preview.png`.
- **Daemon:** sobe sozinho no 1º `/design` da sessão (~15s, ~970MB RAM), fica de
  pé. Parar: `pnpm tools-dev stop` na pasta do clone (com o Node oficial no PATH:
  `~/.local/opt/node-open-design/bin`).

---

## 5. Gotchas para lembrar

- **`agy --add-dir` sempre absoluto**, nunca `--project` pra pasta externa.
- **`agy --mode plan`** e **`claude --permission-mode plan`** = read-only de
  verdade (testado: não escrevem). `--disallowedTools` NÃO segura.
- **`codex exec`** precisa de `-o <arquivo>` pra pegar a resposta final de forma
  confiável (o `--json` dele é best-effort).
- **`gh copilot -p`** devolve texto puro + rodapé de métricas ("AI Credits",
  "Tokens"...). O parser filtra esses prefixos.
- **Ollama Cloud não roda como agente** — só como cérebro de chat (Orquestrador).
- **DeepSeek depende do arquivo `~/.claude-deepseek-env`** — se a chave expirar,
  trocar lá ou via `/senha DEEPSEEK_API_KEY`.
- **`_build_orchestrator_cmd` retorna 3-tupla** `(cmd, binary, output_file)` —
  todo caller precisa desempacotar os 3.
- **TUI e REPL compartilham** `orquestra.py` — mudou assinatura de
  `_run_worker_headless`/`_resolve_worker`/`_build_*`, atualiza os dois.
- **`save_screenshot()` da Textual não captura `textual-image`** — o teste
  headless mostra a imagem como pontos; num terminal de verdade é imagem real.
- **`RichLog(min_width=...)` — o default do Textual é 78.** `render_width =
  max(render_width, self.min_width)` (código-fonte do Textual) força esse piso
  mesmo quando o painel real é mais estreito (`shrink=True` calcula certo, mas
  o `max()` sobrescreve). Com os painéis Arquivos/Editor abertos, o chat ficava
  mais estreito que 78 e a mensagem "vazava", exigindo rolagem horizontal pra
  ler. Fix: `min_width=1` na construção do `RichLog` do chat.
- **`TabbedContent.add_pane(pane)` agenda o mount, não é síncrono.** Quem chama
  `_open_file_in_editor()` e precisa do `TextArea` da aba NOVA na mesma
  chamada (não só depois, num próximo `pilot.pause()`/frame) não pode confiar
  em `query_one(f"#code-{pane_id}")` logo em seguida — dá `NoMatches`. Fix:
  `_open_file_in_editor` guarda a referência do `TextArea` que ela mesma criou
  (variável local `editor`) e **retorna esse objeto direto**, em vez de só o
  `pane_id`. Objetos Python já existem antes de montar no DOM; só a *consulta*
  por seletor CSS (`query_one`) depende do mount ter terminado.
- **`opencode agent list`** mostra os agentes pré-definidos e a permissão de
  cada um em JSON — o agente **`plan`** tem `{"permission":"edit","pattern":"*",
  "action":"deny"}`, ou seja, é o equivalente read-only do `--permission-mode
  plan`/`--mode plan`/`--sandbox read-only` dos outros motores. Testado: bloqueia
  escrita sem travar em modo headless (sem TTY), mesmo sem `--auto`.
- **`_binary_from_model()` decide o motor pelo `model_id` com `in` (substring),
  não por prefixo exato** — um `model_id` novo pode colidir com um check
  antigo por acaso. Os IDs do OpenCode/OpenRouter (`openrouter/deepseek/...`,
  `openrouter/qwen/...`, `openrouter/z-ai/...`) bateriam nos checks de
  `"deepseek"`/`"qwen"`/`"glm"` já existentes se o check de `"openrouter"` não
  vier **primeiro** na função. Regra geral: todo motor novo cujo `model_id`
  possa conter substrings de motores existentes precisa de um check mais
  específico ANTES dos genéricos.

---

## 6. Status verificado (set/2026)

Todos testados headless, exatamente como o código chama, lendo/rodando na pasta
real do projeto:

| Motor | Papel | Conecta via | Testado |
|---|---|---|---|
| `claude` | Operador/Revisor | login Claude Code | ✅ lê arquivos, roda comandos |
| `agy` | Operador | login Antigravity (`--add-dir` absoluto) | ✅ depois do fix — antes ia pro sandbox dele |
| `codex` | Operador | login Codex (`exec -o`) | ✅ lê arquivos, `pwd` na pasta certa |
| `claude` + `~/.claude-deepseek-env` | Revisor (DeepSeek) | API key no arquivo | ✅ chave válida, lê arquivos |
| `agy`/`claude`/`codex` `--mode plan` | Orquestrador | login da CLI, read-only | ✅ os 3 respondem, emitem `<PLANO>`/`<DELEGATE>`, NÃO escrevem |
| `brain` deepseek/ollama | Orquestrador | API key (arquivo / kimi CLI) | ✅ responde |
| **`gh copilot`** | **Cão de Guarda** | login GitHub Copilot | ✅ autenticado; achou `ZeroDivisionError` plantado num diff (`achou_bug=True`) |

O Copilot roda como `binary: copilot, model: auto` no `watchdog_verdict()` —
recebe o `git diff` pronto no prompt, filtra o rodapé de métricas (`AI Credits`,
`Tokens`, `Resume`, `Changes`) e classifica se tem bug pelas palavras-chave em
`_WATCHDOG_BUG_WORDS`. Silencioso se o veredito for "tudo certo".

---

## 7. Backups / rollback

- Config antiga: `~/.orquestra.json.bak-pre-refactor`
- Restaurar: `cp ~/.orquestra.json.bak-pre-refactor ~/.orquestra.json`
- REPL antigo: `orquestra --repl`
- Código aposentado: `_archive/` (gitignored) — `llm_orchestrator.py`, roles v1

---

## 8. Ordem dos commits (set-out/2026)

| Commit | O quê |
|---|---|
| `aaf42d4` | Arquitetura de 3 agentes + Cão de Guarda, TUI oficial |
| `29126ec` | Open Design religado e ligado na TUI |
| `6b129a1` | Prévia do Open Design renderizada dentro do Orquestra |
| `bdbaee0` | Não abrir o Cursor no boot |
| `8a2ba1d` | Orquestrador aceita todas as empresas + `/senha` na TUI |
| `19902c7` | `agy` não enxergava a pasta + validação de modelo dos operários |
| `14686b0` | Orquestrador usa as contas LOGADAS no terminal (agy/claude/codex) |
| `af26769` | `APRENDIZADOS.md` (este arquivo) |
| `5fff54d` | docs: §6 status verificado — Copilot/Cão de Guarda testado OK |
| `8f25404` | fix(tui): chat aceita colar texto grande/multi-linha |

---

## 9. Sessão de 03/09/2026 — Editor de verdade, `<MOSTRAR>`, Modo Aprendizado, OpenCode, Sugestões

Cinco blocos grandes numa sessão só, todos planejados em modo plano antes de
implementar (cada um com `EnterPlanMode`/`ExitPlanMode`, plano salvo, testado
headless antes de considerar pronto). Visão geral — detalhes de cada mecanismo
já estão nas seções certas acima (§5 gotchas, "Editor" e "Tags" em
`AGENTES.md`):

1. **Editor com abas de verdade** (`TabbedContent`, um `TextArea` por arquivo,
   `Ctrl+S`/`Ctrl+W`, dirty-tracking, sintaxe por extensão). Fundação dos
   outros 4 blocos — todos reabrem/editam arquivo através dele.
2. **`<MOSTRAR arquivo="...">`** — Orquestrador (zero ferramentas) ganha uma
   árvore de arquivos leve no prompt (`_project_file_tree`, `git ls-files`) e
   pode abrir um arquivo no Editor durante a conversa, sem esperar o usuário.
3. **Modo Aprendizado** — toggle `🎓`; usuário escreve, Revisor só comenta
   (read-only de verdade, não por convenção de prompt — ver regra 7 do
   `AGENTES.md`). Revisão automática ao salvar (cooldown 20s) + botão de
   revisão profunda.
4. **Motor OpenCode/OpenRouter** — `Op:`/`Rev:` ganham modelos chineses
   (DeepSeek, Qwen, GLM, Kimi, MiniMax) via a CLI `opencode` já logada
   (mesmo padrão "conta logada" do claude/agy/codex, sem API key no cofre).
5. **Sugestões inline** — Revisor (Modo Aprendizado) pode propor
   `<SUGESTAO arquivo="..." linha_inicio="N" linha_fim="M" motivo="...">`,
   vira cartão com Aceitar/Recusar por trecho, aplicado via `TextArea.replace`.

**Testes:** tudo passou por `python3 test_orquestra.py` (backend, sem
Textual/rede/subprocessos de agente) ou por scripts headless com
`app.run_test()` do Textual (UI, mockando `_run_worker_headless`/
`_run_orchestrator_stream` quando precisava simular resposta de um agente sem
gastar chamada real). O comando `opencode` foi testado com chamadas reais
(barato, poucos centavos) pra confirmar formato de saída e comportamento
read-only antes de desenhar o parser — não dá pra confiar só na `--help`.

**Como retomar amanhã:** os 5 blocos estão testados headless, mas **não
testados numa sessão de uso real do zero, do início ao fim, num projeto novo**
(o usuário testou os Blocos 1+2 ao vivo e confirmou "funcionando", os outros
3 ficaram só no headless). Primeira coisa a validar: Modo Aprendizado ligado,
escrever um bug de propósito, `Ctrl+S`, conferir que o cartão de sugestão
aparece e que Aceitar aplica certo; trocar o `Rev:` pra um modelo OpenCode e
repetir.

## 10. Sessão de 04/09/2026 — Gasto/uso, autoria por IA, atividade ao vivo, todo.md, gate por risco

Cinco melhorias no fluxo, planejadas com `EnterPlanMode`/`ExitPlanMode`
(plano em `~/.claude/plans/concurrent-imagining-micali.md`) depois de um
pedido exploratório do usuário por ideias mais modernas (Manus AI como
referência). Commits `3213b48` (código) e `2c541b8` (roadmap):

1. **`WorkerResult`** (dataclass) substitui a tupla posicional de
   `_run_worker_headless` — captura `cost_usd`/`tokens_in`/`tokens_out`/
   `duration_ms`/`changed_files` que já chegavam no stream de cada CLI e
   eram descartados. Confirmado empiricamente (`claude -p --output-format
   stream-json`, `agy -p --output-format stream-json`, `codex exec --json`)
   que **só o Claude Code devolve custo real em dólar** (`total_cost_usd`);
   Agy e Codex só tokens — CLIs de assinatura não expõem preço. Rodapé
   `#usage-footer` + `/uso`/`/saldo` (saldo real da DeepSeek via
   `GET api.deepseek.com/user/balance`) deixam essa diferença explícita no
   texto ("estimado" vs "saldo").
2. **Autoria por IA** — índice paralelo (tabela `file_authorship`, por
   arquivo, não por linha — escopo v1 deliberado), gravado só quando a
   tarefa é aprovada de verdade; não mexe em `commit_pending`/
   `revert_pending`. Decisão tomada com o usuário via `AskUserQuestion`
   antes de implementar: alternativas (commit por etapa / branch efêmera)
   dariam git-blame nativo, mas mudariam o significado de "Rejeitar" — o
   usuário preferiu manter a rede de segurança intocada. Consulta via
   `/quem <arquivo>`.
3. **Atividade ao vivo** — `<PIPELINE>` na TUI passava callbacks no-op pro
   `_run_worker_headless`; religado no mesmo `#status-indicator` que o
   `<DELEGATE>` solto já usava (mecanismo existia, só não estava plugado).
4. **Painel "📋 Plano"** (`#plan-panel`) — checklist ao vivo (⬜🔄✅❌) das
   etapas do `<PIPELINE>`, populado a partir da mesma lista de steps já
   usada pra rodar o pipeline; some quando Aprovar/Rejeitar resolve.
5. **Gate por risco** (`risk_gates`, nasce `enabled: false`) — só se aplica
   no ponto de aprovação (arquivos já tocados, dá pra classificar caminho
   por `fnmatch`); NÃO no `plan-bar`, que aparece antes de qualquer arquivo
   ser tocado (ajuste feito na implementação ao perceber que o plano
   original propunha os dois pontos, mas o plan-bar não tem path nenhum
   disponível ainda). Mesmo com caminho "seguro", roda o Cão de Guarda
   síncrono antes de decidir — se ele achar algo, pausa igual ao fluxo
   manual (a rede de segurança não depende só do classificador de caminho).

**Bug pego no meio do caminho:** ao trocar o retorno de
`_run_worker_headless` pra `WorkerResult`, esqueci de migrar os 2 call
sites de `_worker_lifecycle` (o `<DELEGATE>` paralelo do **REPL legado**,
`--repl`) — continuavam desempacotando a tupla antiga e quebrariam em
runtime com `TypeError: cannot unpack non-iterable WorkerResult`. Só achei
ao re-grepar TODOS os call sites de `_run_worker_headless` depois de
"terminar" — se mexer nessa função nunca mais, confirmar isso de novo antes
de considerar pronto.

**Testes:** `test_orquestra.py` (88 checks, sem rede) + smoke tests diretos
de banco (`get_session_usage_totals`, `set_file_author`/`get_file_author`,
`_classify_risk`) + boot real da TUI via `app.run_test()`. O gate por risco
foi validado à parte com um script dedicado — **3 cenários, num repo git
isolado em `/tmp`** (não no repo real, não em `~/.orquestra.json` real —
`load_config` trocado só em memória do processo de teste), mockando
`_run_worker_headless` (Operador) e `watchdog_verdict` (Cão de Guarda) pra
não gastar chamada real e não competir com o worker de fundo
`_load_models_and_config` (busca modelo de 7 provedores, pode demorar):
- **A** (caminho comum) → seguiu sem pausar, commitou, `file_authorship`
  gravado com o agente certo.
- **B** (`.env`, caminho sensível) → pausou, nada commitado, sem autoria
  gravada.
- **C** (caminho comum, mas Cão de Guarda mockado pra achar bug) → pausou
  mesmo assim — confirma que o classificador de caminho sozinho não é a
  única rede de segurança.

Gotcha do próprio teste: a primeira tentativa esperava `app.workers` ficar
vazio antes de checar o resultado — nunca fica, porque `_load_models_and_config`
(que roda em todo `on_mount`, buscando modelo de 7 provedores) é um worker
de fundo que pode demorar muito mais que a tarefa sendo testada. A espera
certa é por um sinal concreto (barra de aprovação visível, ou contagem de
commit mudou), não "nenhum worker rodando".

**Não coberto por este ciclo (gaps conhecidos):** autoria é por arquivo, não
por linha; o gate por risco não cobre o fluxo do Open Design (gate próprio,
mais raro); não há selo visual de autoria na aba do Editor (só `/quem` via
chat).

**Follow-up, mesmo dia — `/risco on|off` e dois gaps do Modo Aprendizado
achados testando na prática (commits `dca0c8d`, `895e0a6`):**

- **`/risco on|off`** — faltava jeito de ligar `risk_gates` sem editar
  `~/.orquestra.json` na mão. Comando novo mostra status + padrões
  configurados sem argumento, liga/desliga com argumento, persiste na
  config real. Usado pra ligar de verdade o gate por risco no projeto
  (dogfooding, não só teste em `/tmp`).
- **Cão de Guarda não reagia a edição manual.** `watchdog_verdict`/
  `_run_watchdog` só eram acionados depois do Operador + Aprovar
  (`_handle_approval`) — uma edição manual no Editor (Ctrl+S, o fluxo do
  Modo Aprendizado) nunca passava por ele. `action_save_file` agora chama
  `_run_watchdog(commit_after=False)` com cooldown próprio de 15s (separado
  do cooldown de 20s do Revisor — são auditorias diferentes, não devem
  competir pelo mesmo timer). **Cuidado se mexer em `_run_watchdog` de
  novo:** a lógica de retomar `<PIPELINE>` pausado e limpar o painel de
  plano ficou condicionada a `commit_after=True` — sem essa condição, uma
  chamada de auditoria de edição manual abandonaria silenciosamente um
  pipeline pausado que não tem nada a ver com ela (achado ao revisar o
  código antes de ligar, não em produção — mas era um bug real na primeira
  versão do fix).
- **Orquestrador ficava mudo depois do Revisor comentar.** Tecnicamente já
  "sabia" via histórico (`save_chat_message` salva tudo em
  `orquestrador.json`), mas nunca reagia sozinho — só o Revisor falava.
  `_orchestrator_react` (antes só do REPL) ganhou um parâmetro `section`
  opcional, aceita por duck typing um `_TUILiveSection` da TUI em vez de
  criar sua própria `LiveSection` do Rich console (que escreveria por baixo
  do Textual e corromperia a tela — mesma classe de problema do bug de
  `patch_stdout` já documentado). `_run_worker` ganhou `orchestrator_react=True`,
  ligado só na revisão automática do Modo Aprendizado (não em toda chamada
  de worker — era especificamente o que foi pedido).
- **Como foi validado:** screenshot do usuário mostrando o Revisor
  comentando um bug de verdade (`primeiro.py`) e o rodapé do Cão de Guarda
  parado em "Aguardando alterações..." → decisão confirmada com o usuário
  via pergunta direta (quer o Cão de Guarda auditando edição manual? quer o
  Orquestrador reagindo?) antes de implementar, não assumido. Depois,
  testado de ponta a ponta num repo isolado em `/tmp` (Operador/Revisor,
  `watchdog_verdict` e `_orchestrator_react` mockados, só a fiação de verdade
  rodando): save no Modo Aprendizado disparou os três — Revisor comentou,
  Cão de Guarda atualizou o rodapé, Orquestrador reagiu depois.

**Follow-up sério, mesmo dia — usuário chegou a cogitar parar o projeto
("está bem ruim o fluxo, a gente não sabe quem está fazendo nem o prompt
que foi mandado, estamos no escuro"). Investigação (não assumida, checada
no disco) antes de qualquer código (commit `5c9bbb8`):**

- **Nada tinha quebrado.** Repo real do Orquestra limpo, `primeiro.py` do
  Projeto Cisco com o MESMO bug de sempre, inalterado — Modo Aprendizado é
  read-only de verdade, fisicamente não tinha como ter estragado nada.
- **Causa raiz real: `Projeto Cisco` não é um repositório git** (`ls -la`
  confirmou — sem pasta `.git`, só um `.gitignore` que o `vault.py` cria
  sozinho no primeiro uso, isso é normal). **Todo** o modelo de segurança —
  Aprovar/Rejeitar, Cão de Guarda, autoria por IA, gate por risco — depende
  de `git diff`/`git status` por baixo. Sem `.git`, esses mecanismos ficam
  **silenciosamente inertes**, sem UM aviso sequer. Isso não é específico
  deste ciclo — é uma lacuna que sempre existiu, só nunca tinha sido
  testada num projeto sem git antes. **Se mexer em qualquer coisa do gate
  de novo, checar sempre**: existe aviso pro usuário se a pasta não é git?
  (agora sim, ver abaixo — mas é fácil reintroduzir esse silêncio.)
- **Achados concretos de transparência, direto no código:**
  1. O indicador de atividade ao vivo mostrava a tool call crua do Claude
     Code (`Bash({"command": "cd ... && ls"})`) mesmo em `detail="normal"`
     (o que o modo chat usa) — só devia ficar cru em `detail="full"`.
  2. **O prompt de verdade mandado pro agente nunca aparecia em lugar
     nenhum da tela** — só a resposta. Caixa-preta real, não impressão do
     usuário.
- **Fix, os 3 confirmados com pergunta direta ao usuário antes de
  implementar (não assumidos):**
  1. `on_mount` avisa (uma vez, linha amarela) se `Path(project_dir)/".git"`
     não existe, listando exatamente o que fica sem efeito.
  2. `_run_worker`/`_run_pipeline_tui` escrevem uma linha apagada
     "📤 Prompt para {agente}: {instrução}" antes de disparar o worker.
  3. `_friendly_tool_call` (novo, orquestra.py) traduz tool call comum
     (Bash/Read/Write/Edit/Grep/Glob/WebFetch/TodoWrite) pra frase legível
     em `detail="normal"`; `detail="full"` continua com o preview cru
     (`_preview`) de sempre — não perde informação pra quem quer o técnico.
- **Validado:** boot numa pasta sem `.git` mostra o aviso; boot numa pasta
  COM `.git` não mostra (testado os dois); `_run_worker` com um marcador
  único no prompt (`"...XYZ123"`) confirmado aparecendo na linha de prompt
  do chat; `_friendly_tool_call` testado direto pra Bash/Read/Edit/Grep e
  fallback genérico.
- **Lição maior, não só de código:** a velocidade de adicionar as 5
  features + os 2 fixes anteriores no mesmo dia aumentou a superfície do
  projeto rápido (WorkerResult, file_authorship, risk_gates, plan-panel,
  usage-footer, orchestrator_react) bem na hora em que o próprio
  `ANALISE_ARQUITETURA.md` já tinha avisado sobre risco de over-engineering.
  A causa raiz do incidente (falta de git) não foi introduzida por nenhuma
  dessas features, mas a sensação de "não sei o que tá rolando" piora
  proporcionalmente à quantidade de coisa nova rodando ao mesmo tempo. Se o
  usuário voltar a sentir isso, a pergunta certa não é "que feature falta"
  — é "o que dá pra tirar".

## 11. Sessão de 05/09/2026 — Palco + Chat (layout inspirado no Antigravity)

Pedido do usuário: analisar se dava pra reformar a TUI no formato "palco
central em cima (atividade/mudanças/terminal) + chat pequeno embaixo" que
ele via no Antigravity do Linux — junção de Cursor + Manus AI, mas rodando
em terminal. Fable analisou e escreveu `PLANO_PALCO_CHAT.md`; Sonnet revisou
o plano contra o código real e implementou as 6 etapas, cada uma com commit
próprio e teste antes de fechar (`ba75acf`, `8e8f6a6`, `10feee2`, `4d180b0`,
+ commit de polimento, + este de docs).

**Confirmação, não achado:** o usuário chegou a suspeitar que o Fable tinha
escrito o plano "sem ler o projeto". Não era o caso — as linhas citadas
batiam com grep real, o mockup citava o modelo exato configurado em
`~/.orquestra.json` (`gpt-5.6-terra`), e o diagnóstico usava os nomes reais
de função (`_run_orchestrator_stream`, `_narrate_codex_line` etc.). Os "6
furos" achados na revisão eram interações de segunda ordem entre a ideia
nova e comportamento que já existia (GPS, `revert_pending`, paralelismo de
`<DELEGATE>`), não sinal de leitura rasa — achar esse tipo de furo exige
conhecer o código a fundo, não o contrário.

**Os 6 furos da revisão, todos corrigidos antes de implementar** (detalhes
no próprio `PLANO_PALCO_CHAT.md`, marcados `[REVISADO]`):
1. GPS chamando `_stage_show` puxaria o palco da Atividade pro Editor a cada
   arquivo tocado, brigando com a própria proposta — resolvido com
   `_open_file_in_editor(..., switch_stage=False)` só pro GPS.
2. `_task_running: bool` zeraria cedo com `<DELEGATE>` paralelos — virou
   `_active_workers: int`.
3. Narração amigável do Codex ("📖 Lendo X") não existe hoje — documentado
   como limitação, não implementado (exigiria testar o schema `--json` real
   do Codex antes).
4. `git add -N` pra ver conteúdo de arquivo novo quebraria `revert_pending`
   (mudaria `??` pra código "tracked", e `checkout HEAD` não faz nada nesse
   caso) — resolvido lendo o arquivo novo direto do disco, sem tocar no
   índice.
5. Etapa 4 não podia reaproveitar o parâmetro `orchestrator_react` que já
   existia (Modo Aprendizado, sempre `read_only=True`) — ficou como
   checagem independente (`not read_only and ...`).
6. `/modo` (chat|tecnico) nunca teve efeito na TUI — documentado como
   limitação existente (é só do REPL), não confundir com `/detalhe`.

**Achado NOVO durante a implementação, não previsto em nenhuma revisão
anterior:** `RichLog.write()` (Textual) adia a renderização até o widget ter
um tamanho conhecido (`_deferred_renders`, esvaziado no primeiro resize com
largura real). Como "± Mudanças" pode não ser a aba ativa no momento em que
`_refresh_changes_panel` roda, o diff só aparece de fato quando o usuário
abre a aba — descoberto porque um teste headless checava `#diff-log.lines`
logo depois do worker terminar, sem nunca ter ativado a aba, e via sempre 0
linhas mesmo com `git_diff_for_display` retornando dados corretos (confirmado
com trace: a função de render RODAVA com os dados certos, só não "aparecia").
Não é bug — o título da aba (badge `± Mudanças (N)`) e o `#changes-header`
(um `Label`, sem esse adiamento) já avisam antes disso. Documentado em
`_render_changes_panel` e no plano. **Se mexer em qualquer `RichLog` dentro
de uma aba que pode estar inativa:** não confiar em `.lines` logo após
escrever sem ativar a aba primeiro no teste.

**Metodologia de teste usada em toda etapa** (mesmo princípio do §10 — nunca
gastar chamada real de agente, nunca contaminar com o `~/.orquestra.json`
real do usuário):
- Repo git isolado em `/tmp`, `orquestra.get_project_dir`/`get_project_id`
  mockados pra apontar pra lá.
- `orquestra._run_worker_headless`/`_build_worker_cmd`/`watchdog_verdict`/
  `_orchestrator_react`/`_record_agent_performance` mockados conforme o
  cenário — nunca sobe um binário `claude`/`codex`/`agy` de verdade.
- `orquestra.load_config` mockado quando o cenário dependia de `risk_gates`
  desligado — o `~/.orquestra.json` REAL do usuário tem `risk_gates.enabled:
  true`, e sem isolar isso um teste "deveria pausar" cairia no caminho de
  auto-commit por engano (achado direto, não hipotético — aconteceu na
  prática ao escrever o teste da Etapa 2).
- `app.workers.wait_for_complete()` **não** é confiável quando o app tem
  outros workers de fundo (ex: `_load_models_and_config`, que roda sempre em
  `on_mount`) — pode levantar `WorkerCancelled` de um worker sem relação
  nenhuma com o que está sendo testado. Esperar por um **sinal concreto**
  (texto aparecendo num log, uma classe CSS mudando) continua sendo a regra
  certa, reforçando o que o §10 já tinha achado sobre `app.workers` vazio.
- `app._run_worker(...)` só AGENDA a thread (`@work`) — checar
  `app._active_workers == 0` imediatamente após chamar é ambíguo ("ainda não
  começou" e "já terminou" têm o mesmo valor). Sempre esperar por um
  marcador de conteúdo antes de checar contadores.

**Não coberto por este ciclo:** a narração amigável do Codex ("📖 Lendo X",
igual ao `_friendly_tool_call` do Claude) fica como próximo passo — precisa
rodar `codex exec --json` de verdade numa tarefa de leitura pra confirmar o
schema; `codex exec resume --last` como forma de não reenviar o histórico
inteiro a cada turno também não foi implementado, só anotado no `ROADMAP.md`.

---

## Fix — modelo do Operador/Revisor ≠ motor invocado (set/2026)

**Sintoma (relato do usuário):** "botei o Gemini pra implementar, deu erro;
troquei pro Revisor, deu erro também." O erro real:

```
API Error: 400 The supported API model names are deepseek-v4-pro,
deepseek-v4-flash, deepseek-v4-flash-vision-exp, but you passed gemini-3.1-pro-high
⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY ... is set
{"model":"gemini-3.1-pro-high","query_source":"sdk"}
```

Textos `query_source`/`generate_session_title`/`claude.ai connectors` são do
**`claude` (Claude Code)**, não do `agy`. Ou seja: a etapa do Revisor rodou o
`claude` apontado pro endpoint do DeepSeek (`ANTHROPIC_BASE_URL` via
`~/.claude-deepseek-env`) **passando `--model gemini-3.1-pro-high`** → o DeepSeek
só aceita `deepseek-v4-*` → 400.

**Causa raiz:** `_run_worker_body` / `_run_pipeline_tui_body` pegavam `binary` +
`env_file` da config em disco (`_resolve_worker`) mas sobrescreviam **só o
`model`** com o seletor do header (`self._op_model`/`self._rev_model`). Config e
header discordando de provedor = comando Frankenstein (motor de um, id de modelo
de outro). Agravantes: `_resolve_worker` caía no dict do `operador` inteiro
quando faltava `workers.revisor` — inclusive o `label`, então a tela dizia
"Operador" rodando como Revisor; e o processo da TUI fica de pé por horas com o
header em memória velha.

**Correção:**
- `orquestra.engine_for_model(model_id)` — fonte ÚNICA: modelo → `{binary,
  model, env_file?, provider?}`. `_binary_from_model` da TUI passou a delegar.
- `orquestra.apply_model_to_worker(worker_cfg, model_id)` — deriva binary +
  env_file do modelo, preserva label/ícone/cor. Usado nos 2 caminhos de worker
  em vez de `worker_cfg["model"] = chosen`; `actual_binary` recalculado.
- header só rege `operador`/`revisor` canônicos — `<DELEGATE binary="codex">`
  etc. voltam a respeitar a própria config.
- `_resolve_worker`: fallback do operador herda só o motor, nunca a identidade.
- `_persist_agent`: env_file sempre derivado do modelo salvo (fim do
  `binary=claude`+`env_file=deepseek` com modelo Gemini); `campo=None` = remoção.
- `_sync_model_headers_from_disk`: re-alinha os seletores Op:/Rev: com o disco
  no início de cada execução (mata o problema de processo velho).
- testes novos em `test_orquestra.py` (engine_for_model, apply_model_to_worker,
  cenário Frankenstein, fallback sem roubar identidade).

**Colateral não corrigido:** `~/.claude-deepseek-env` tem `[1m]` colado em 3
linhas (`ANTHROPIC_MODEL=deepseek-v4-pro[1m]`) — inválido pro endpoint DeepSeek,
mas é setup do usuário, fora do escopo do código.

## Fix — análise de segurança ponta a ponta: achados A1 e A2 (08/09/2026)

Análise completa (compila, testes passam, base limpa de shell-injection / SQLi /
eval / pickle). Relatório: `ANALISE_PONTA_A_PONTA_2026-09-08.md`. Dois achados
corrigidos nesta sessão.

### A1 — `<DELEGATE>` avulso executava operário sem gate

**Sintoma:** só `<PLANO>` sozinho tinha gate de aprovação. Um
`<DELEGATE>`/`<PIPELINE>` que o Orquestrador emitisse direto (sem plano, ou
junto de um `<PLANO>` no mesmo texto) caía em `_run_orchestrator_body` e
disparava `_run_worker`/`_run_pipeline_tui` na hora — e o operário roda com
`--dangerously-skip-permissions` / `bypassPermissions` / `workspace-write`. Um
delegate que só roda comando / lê segredo / exfiltra (sem tocar arquivo) nem
batia na barra Aprovar depois, que só aparece com `changes_summary`.

**Causa raiz:** o gate era conceitual (o system prompt manda usar `<PLANO>`),
nunca estrutural. O README já vendia "você aprova cada mudança" — não era
verdade pro delegate avulso.

**Correção (`orquestra_tui.py`):**
- `ui.confirm_delegates` (novo, `DEFAULT_CONFIG`, nasce `True`, auto-backfill).
- `_run_orchestrator_body`: ao achar `delegates`/`pipelines`, se
  `confirm_delegates` ligado **e** não veio de um plano aprovado → guarda em
  `self._pending_delegates`, mostra `#delegate-bar` (▶ Rodar agora / ✋ Cancelar)
  com a lista do que vai rodar, e `return`.
- `self._delegates_preapproved`: setado no `btn-exec-plan`, lido+zerado no topo
  de `_run_orchestrator_body` (one-shot) — libera a delegação DAQUELE plano sem
  re-perguntar.
- `_dispatch_delegates(deleg_tuples, pipelines)`: extraído (roteamento _OD /
  worker / pipeline) — chamado direto (gate off / plano aprovado) ou pelo botão.
- `/auto on|off` — `on` = `confirm_delegates=False` (projeto seu de confiança).
- delegates viram `(tag, instr)` na hora do parse (antes eram `re.Match` só
  consumidos no loop).

### A2 — "❌ Rejeitar" na TUI revertia a árvore inteira

**Sintoma:** `_handle_approval("reject")` chamava `revert_pending(cwd)` que fazia
`git checkout HEAD -- <todos os tracked sujos>` + `git clean -fd -- <todos os
untracked>`. Edição manual sua no Editor + outra tarefa pendente no mesmo repo
eram apagadas junto, silenciosamente. O caminho REPL (`orquestra.py` linha ~3919)
já tinha guarda `pre_snapshot`/`other_pending`; a TUI (interface oficial) não.

**Correção:**
- `orquestra.revert_pending(cwd, only=None)` — com `only` (set de caminhos
  relativos à raiz), reverte só esses. Sem `only`, comportamento antigo.
- `_handle_approval("reject")`: monta `task_files` a partir de
  `_pending_attribution["files"]` (que já é `git_dirty depois − antes`,
  calculado em `_capture_worker_result`) e, quando falta atribuição
  (open-design etc.), `git_dirty_agora − self._task_baseline_dirty`.
- `_task_baseline_dirty`: capturado em `_warn_pending_manual_edits` (chokepoint
  único de início de tarefa). Arquivos dele nunca são revertidos → aparecem como
  `🛡️ Preservado` no chat. (Delegações paralelas compartilham o campo — a lista
  precisa do worker tem prioridade; documentado.)
- `_rel_to_repo()` normaliza caminho absoluto que o worker às vezes reporta.
- Se não dá pra identificar os arquivos da tarefa com segurança → NÃO reverte
  nada, manda o usuário desfazer na mão (mesma filosofia da guarda do REPL).

**Testes novos** (`test_orquestra.py`): `revert_pending(only=...)` com cenário
manual+tarefa; `ui.confirm_delegates` default. Smoke tests da TUI via
`app.run_test()` (barra, botões, `/auto`, reject preservando edição manual).

**Não corrigido (baixo, no relatório):** A3 permissão 0664 do cofre, A4
`/senha-mestra` sem validação, A5 prompt como argv, A6 egresso pro DeepSeek,
A7 folgas no bridge HTTP, A8 `_load_vault_data` engole Exception.


## Gate de verificação + Cão de Guarda no DeepSeek (10/09/2026)

Contexto e decisões em `ANALISE_GATE_E_COPILOT_2026-09-10.md`. Duas melhorias
aditivas no fluxo, sem mudar o fluxo. Commit da implementação + este doc.

### O que NÃO é óbvio

- **A revisão de código do Copilot deixou de ser grátis (1º/jun/2026).** Virou
  AI Credits (token-metered), e no plano estudante são 200 AIC/mês **compartilhados**
  entre chat, agent mode, code review, CLI e Spaces. Só o autocomplete continua
  ilimitado. Não existe "Copilot revisando sempre o mês todo". Por isso o Cão de
  Guarda saiu do Copilot.
- **`watchdog_verdict` NÃO usa `_run_worker_headless`.** Mantém `subprocess.run`
  próprio com `timeout` (o `_run_worker_headless` faz `proc.wait()` sem timeout —
  bom pra worker interativo, ruim pra um watchdog automático). Então o motor DeepSeek
  entra como `claude -p <prompt> --permission-mode plan` **sem** `--output-format
  stream-json` → stdout é texto puro, que o parser de hoje já trata. `plan` = pode
  ler arquivos, nunca escreve. Combo `claude -p ... --permission-mode plan` já era
  testado em `_build_orchestrator_cmd`.
- **O env_file (`~/.claude-deepseek-env`) precisa ser passado no `env=` do
  `subprocess.run`.** `watchdog_verdict` não fazia isso (só o Cão de Guarda-Copilot,
  que não precisa). Sem isso o `claude` usaria o login real, não o DeepSeek.
- **NÃO chamar `_orchestrator_react` da thread do Cão de Guarda.** `_run_copilot_watchdog`
  dispara `_watchdog_report` numa thread daemon (`name="cao-guarda"`) enquanto
  `_worker_lifecycle` já chama `_orchestrator_react` na própria thread. Duas reações
  concorrentes = dois subprocessos de orquestrador + dois `_print_chat_bubble` +
  corrida no `orquestrador.json`. Solução: `_watchdog_report` só faz `print` +
  `save_chat_message` — o Orquestrador vê o achado na próxima vez que o usuário
  falar com ele (`build_orchestrator_prompt` lê o histórico).
- **`save_chat_message` não era thread-safe** (read-modify-write de JSON sem lock).
  Já era disputado por `_worker_lifecycle` threads paralelas hoje; o Cão de Guarda
  como mais um writer deixou óbvio. Envolvido em `_chat_lock` (RLock de módulo).
- **`load_config` só faz backfill de workers faltando, nunca modifica os existentes.**
  Pra virar `cao_guarda` de Copilot → DeepSeek precisou de um bloco de migração
  dedicado (padrão do bloco "sanidade do Orquestrador"), com condição **engine-only**
  (`binary == "copilot" and model in ("auto","",None)`) — quem escolheu um modelo
  Copilot real é preservado.
- **`~/.orquestra.json` é global, não por projeto.** Por isso o gate tem o
  `.orquestra_gate` (arquivo na raiz do projeto, 1 comando por linha, `#` = comentário)
  com prioridade sobre `gate.command`. O próprio repo tem um (`python3 test_orquestra.py`),
  porque `test_orquestra.py` não é pytest e a auto-detecção não pegaria — pior,
  `pytest` rodando nele daria erro de coleta (o módulo faz `sys.exit(1)`).
- **Gate nunca pune o operário por problema de ambiente.** `_run_gate_check`
  devolve `rc=None` (→ skip silencioso) em `FileNotFoundError`, `TimeoutExpired` e
  `rc == 127`. Ecossistema ambíguo (ex.: `pyproject.toml` + `Cargo.toml`) também
  → `None`.
- **Gate só na última etapa do `<PIPELINE>`** (mesma regra do Cão de Guarda): as
  etapas intermediárias são incompletas de propósito (etapa 1 escreve, etapa 2
  testa) — gate no meio re-promptaria por "falhas" que o próximo passo conserta.

### Arquivos

`orquestra.py` — `gate` no `DEFAULT_CONFIG` + migração; `_chat_lock`;
`_watchdog_report`; `watchdog_verdict` (env_file + `--permission-mode plan` +
timeout 240 + prompt); `_gate_enabled` / `_detect_project_check` / `_run_gate_check`
/ `_run_verification_gate` / `_GATE_REPROMPT` / `_open_live_section`; wiring em
`_worker_lifecycle` e `_run_pipeline`. `orquestra_tui.py` — 2 sites do gate
(`_run_worker_body`, `_run_pipeline_tui_body` só última etapa) + 3 de
`save_chat_message` (2 auto-approve + `_run_watchdog` só pós-Aprovar).
`test_orquestra.py` — +28 checks (158 no total): detecção do gate, fluxos de
`_run_verification_gate` (skip / passa / falha→corrige / teto → entrega / operário
erra), migração do `cao_guarda`, cmd do watchdog, `_watchdog_report`.

### Testado ao vivo

Gate: `_detect_project_check` pegou o `.orquestra_gate`, `_run_gate_check` rodou a
suíte (rc 0), `_run_verification_gate` → `(True, "✅ verificação passou")`.
Cão de Guarda: repo temporário com `a + b` → `a - b`; `watchdog_verdict` retornou
`has_bug=True` + verdict do DeepSeek apontando arquivo/linha/impacto.


---

## 12. Sessão de 18–19/09/2026 — Terminal real dentro do Orquestra (PRs #4, #5, #6)

**O pedido:** "adicionar o terminal real do Linux dentro do Orquestra" — pra poder abrir
um agente na pasta, rodar `claude login`, `export CHAVE=...` e outras coisas que só o
usuário faz, sem sair da TUI. Depois: "deixar o terminal na tela dos logs, junto do
Orquestra" e "um X pra fechar clicando".

**Como terminou:** um painel lateral `📊 Logs | 🖥 Shell` (ao lado da Atividade e acima
do chat), com um shell de verdade num pty, `Ctrl+E` abre, `Ctrl+]` volta pro chat,
`Ctrl+T`/✕ fecham (shell segue vivo), `Ctrl+O` = shell externo. Regras em `AGENTES.md`
("Painel lateral"), resumo em `NOTAS_TUI.md`, changelog em `CHANGELOG.md`.

| PR | Commit | O que entregou |
|---|---|---|
| #4 | `26116ea` (wip) + `8ebed71` | botão `💻 Shell` corrigido + aba `🖥 Terminal` no palco (`orquestra_term.py`) |
| #5 | `29382d1` | terminal sai do palco e vai pro painel lateral; `Ctrl+E` prioritário; `Ctrl+O` externo |
| #6 | `cfdee79` | botão ✕ pra fechar o painel com o mouse |

### ✅ Acertos

- **Ler antes de propor.** A análise inicial achou, só lendo o código, que já existia um
  "terminal" (`action_open_shell` com `self.suspend()`) e que ele provavelmente estava
  morto — e disse "provável", porque ainda não tinha rodado. Isso mudou o plano: de "criar
  do zero" para "consertar 1 linha + prototipar o embutido".
- **Validação em 3 camadas**, cada uma pegou coisa que a anterior não pegou:
  1. unitária (`key_to_bytes`, parser do `pyte`);
  2. Pilot do Textual com **shell real** num pty (`test_orquestra_term.py`, 62 checks:
     teclas, cwd, Ctrl+C, tela alternativa, cores, resize, exit/restart, sem processo
     órfão, layout lado a lado, foco);
  3. **e2e no tmux real**, com bytes de teclado de verdade e clique de mouse (sequência
     SGR) — o único que pegou o vim em branco.
- **Widget próprio e enxuto** (`orquestra_term.py`, ~400 linhas com comentários) em cima
  de `ptyprocess` + `pyte`, que já estavam instalados — sem dependência nova pesada.
- **PRs pequenos e revisáveis** (#4 base, #5 layout, #6 ✕), cada um mesclado pelo usuário e
  seguido de `git pull` em fast-forward limpo.
- **Limites escritos, não escondidos:** sem scrollback/mouse/cópia; `claude`/`codex` de
  tela cheia **não foram validados** lá dentro (o `Ctrl+O` cobre).
- **Limpeza testada:** sair do app mata o shell filho; `exit` coleta o processo (sem zumbi);
  fechar o painel não mata o shell.

### ❌ Erros e armadilhas (sintoma → causa → correção → lição)

**1. O botão `💻 Shell` não fazia nada.** `action_open_shell` é `async`, mas o handler
síncrono chamava `self.action_open_shell()` sem `await` — cria a coroutine e ela nunca roda,
sem erro nenhum. *Correção:* `call_later` (depois `action_focus_shell`). *Lição:* action
`async` chamada de um handler síncrono é um no-op silencioso; só por binding/`run_action`/
`call_later`.

**2. "Provei" a correção só com mock — e o usuário ainda via o botão morto.** O primeiro
teste do botão usou um mock de `action_open_shell`. Quando o usuário disse "cliquei e não
acontece nada", a causa **não era o código**: o checkout dele ainda tinha a linha antiga
(faltava o `git pull`). Confirmei lendo o arquivo dele (sem tocar em git) e depois com um
**clique real no tmux** (banner do bash → `echo` → `exit` → TUI volta). *Lição:* mock não é
prova; e antes de depurar, confira se o usuário está rodando o código novo.

**3. O worktree partiu do `master` commitado — sem o trabalho não commitado do usuário.**
O botão Shell só existia nas ~570 linhas ainda não commitadas do checkout principal.
Copiei esses 3 arquivos num commit `wip` separado (`26116ea`) e fiz o meu por cima; o `wip`
**entrou no PR #4 e no `master`**. Foi avisado na descrição do PR, mas ainda assim o PR levou
trabalho que não era da feature. *Lição:* antes de isolar em worktree, olhe o `git status`
do checkout principal e combine com o usuário o que fazer com o não commitado.

**4. `event.character` não é confiável.** Sintoma: no teste, os comandos se acumulavam na
mesma linha do shell — o Enter não chegava. `key_to_character("enter")` é `None`, `ctrl+c`
também, e o Backspace vem como `\x08` em vez de `\x7f`. *Correção:* `key_to_bytes` mapeia
pelo **nome** da tecla (Enter, Backspace, `ctrl+letra`, setas com modificadores, F1–F12) e usa
`character` só pro resto.

**5. O vim aparecia em branco — e os testes estavam verdes.** `less` passava no Pilot; o
vim, no tmux real, não desenhava nada. Causa: o parser do `pyte` chama todo handler CSI
com `private=True` quando a sequência tem `?` (o vim manda `CSI ? 6 n` etc.), e ~20 handlers
não aceitam esse argumento → `TypeError` que derruba o parser. *Correção:* `_ignore_private`
(ignora a variante DEC) + `_feed` que recria o stream se o parser ainda falhar + testes de
regressão. *Lição:* teste verde com `less` **não** prova o vim; só o app real pegou.

**6. O `pyte` não tem tela alternativa** (`?1049h`) — vim/less/codex desenhariam por cima
do histórico e deixariam lixo ao sair. *Correção:* `_Screen` guarda/restaura a grade.

**7. `textual-terminal` (0.3.0) já estava instalado, mas quebra no Textual 8**
(`ImportError: DEFAULT_COLORS`). Descartado; widget próprio.

**8. `Ctrl+5` é o mesmo byte de `Ctrl+]`** (`0x1d`) em terminal sem protocolo kitty
(xterm/qterminal). Abrir o terminal com `Ctrl+5` teria aberto e já saído. *Correção:*
`Ctrl+E`. *Hipótese não verificada:* os `Ctrl+1..4` que já existiam no app podem sofrer do
mesmo problema no `qterminal`.

**9. O `TextArea` do chat engolia o `Ctrl+E`** (liga `end,ctrl+e` a "fim da linha") — o
atalho falhava justo com o foco no chat, o caso principal. *Correção:* binding
`priority=True`; com o shell em foco a action repassa `\x05` ao bash. (O Textual não
avisou; o teste falhou.)

**10. Um teste quebrado falou com o LLM de verdade — duas vezes.** Como o `Ctrl+E` falhou
(item 9), o foco ficou no chat, o teste digitou `echo integrado-…` e apertou Enter: chamada
**real** ao Orquestrador (o traceback do `_heartbeat` denunciou). *Correção:* o teste agora
só digita se `app.focused is term`, senão aborta. *Lição:* teste que digita em TUI precisa
garantir o foco antes; Enter no chat dispara uma chamada real (que pode custar; aqui foram 2 mensagens curtas, custo não medido).

**11. O fim de uma tarefa roubava o foco do shell.** `_re_enable_input` sempre focava o chat;
com você digitando no terminal, ele te tirava de lá. *Correção:* só foca se o foco não estiver
no `PtyTerminal`.

**12. Clicar no ✕ passa o foco pro próprio botão antes do handler rodar** — então a regra "se
o foco está no terminal, devolve ao chat" não disparava. Achado pelo teste. *Correção:* vale
para qualquer foco dentro do painel (`panel in focused.ancestors_with_self`).

**13. Um check meu falhou por estar errado, não o código:** `RichLog` oculto **adia** a escrita
(regra que já está no `AGENTES.md`); com a aba Shell ativa os logs só aparecem ao voltar
pra `📊 Logs`. O teste passou a trocar de aba antes de conferir.

**14. Git: `git checkout worktree-terminal-lateral` falhou pro usuário** ("já está em uso
pelo worktree…") — a branch estava aberta no worktree da minha sessão e eu não conferi o
`git worktree list` antes de mandar o comando. Saídas: rodar o launcher de dentro do
worktree pra testar, ou PR + merge + `git pull`.

**15. `python orquestra_tui.py` cai no assistente do cofre** (senha mestra). Meu primeiro
e2e digitou dentro desse prompt. Pra automação: `python -c 'from orquestra_tui import
OrquestraTUI; OrquestraTUI().run()'`; pro usuário: sempre `orquestra`.

**16. Ferramentas de teste:** `pytest` diz "no tests ran" (as suítes são scripts:
`python3 test_orquestra.py`); o `pyproject.toml` do checkout do usuário está sem versionar,
então `pyte`/`ptyprocess` só constam no `pip install` do README.

### O que continua NÃO validado

- `claude` e `codex` (TUIs de tela cheia) dentro do painel — pode haver cor/posição
  imperfeita; o `Ctrl+O` é o plano B.
- Scrollback, mouse e seleção/cópia não existem no painel (ver `ROADMAP.md`).
- Terminais com protocolo kitty (onde `Ctrl+5` etc. chegam distintos) não foram testados.
- Uso longo: nenhum teste de carga (saída muito grande e contínua) no widget.
