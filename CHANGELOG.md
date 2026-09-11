# Changelog Orquestra

## Não lançado — Orquestrador que enxerga o que a gente mexe (set/2026)

Motivado por sessão real: o usuário pediu uma análise do servidor, a equipe
executou, e o Orquestrador devolveu um resumo raso + a pergunta enlatada "quer
ajustar algo ou seguir para outra coisa?". Diagnóstico: o Orquestrador **já**
rodava como `codex` read-only (podia ler o projeto), mas os prompts o tratavam
como um gerador de `<PLANO>`, não como parceiro de raciocínio. Nada de rebuild —
mudança de prompt + contexto.

- **`_ORCH_SYSTEM` ganhou o modo "pensa junto".** "COMO VOCÊ TRABALHA" tinha só
  dois caminhos (dúvida conceitual → resposta curta; mexer em código → `<PLANO>`
  e para). Agora o item 1 é conversa de análise/decisão: **leia o código
  relevante** (está em read-only, use isso), traga sua leitura, opções,
  trade-offs e recomendação com o porquê; `<PLANO>` só quando há mudança concreta
  E o usuário quer partir pra ação. Item 4 novo: ao receber resultado da equipe,
  engajar (o que faria a seguir, o que priorizaria, o que preocupa), não dar
  relatório de status.
- **`_orchestrator_react` deixou de ser enlatado.** Recebe `raw_result[:8000]`
  (era 4000), a lista de `changed_files` do worker, e — quando o Orquestrador tem
  olhos (`agy`/`claude`/`codex`) — a instrução de **abrir e ler os arquivos
  tocados** antes de comentar. Prompt reescrito: continue a conversa como
  parceiro, proponha o próximo passo você mesmo, **sem** a pergunta genérica.
  Cap de exibição da fala do Orquestrador na TUI 2000 → 6000 chars.
- **Estado de trabalho ao vivo no prompt** (`format_working_context` +
  `build_orchestrator_prompt(..., working_context=)`). A cada turno a TUI faz um
  snapshot dos arquivos abertos no Editor, o diff das **edições ainda não
  salvas** (buffer vs. disco, via `difflib`) e o diff pendente do repositório, e
  injeta como um bloco antes do histórico. O Orquestrador enxerga o que você está
  mexendo agora, não só o que está commitado. Bounded em ~6 KB. REPL passa vazio.
- **`Ctrl+N` cria arquivo novo no Editor.** Modal `FileNameModal` pede o caminho
  (relativo ao projeto, recusa escape pra fora da pasta), abre aba vazia; o
  arquivo nasce no disco no primeiro `Ctrl+S`. Fecha o buraco "eu crio, ele
  mostra, eu edito" — antes o Editor só abria arquivo já existente.
- **Menu do REPL destravado pro Orquestrador.** `interactive_agent_selection`
  oferecia só cérebros de chat puro (`_BRAIN_CHOICES`); agora oferece primeiro os
  motores "com olhos" em modo plano/read-only (`_ORQ_AGENT_CHOICES`: codex /
  claude / agy) e depois os cérebros. A TUI já roteava GPT→codex/Claude→claude
  há tempos (`_orch_route`); é só o REPL alcançando a TUI.
- **`~/.codex/AGENTS.md`:** nova exceção "quando você É o Orquestrador dentro da
  TUI". Aprovar um `<PLANO>` na TUI conta como a confirmação explícita da Regra 3
  — emita o `<DELEGATE>` sem perguntar de novo (a TUI já tem o gate). Ler/analisar
  o projeto a fundo é esperado, não pede permissão.

Testado: `python3 test_orquestra.py` — 168 checks verdes (12 novos).

## Não lançado — Gate de verificação + Cão de Guarda no DeepSeek (set/2026)

Análise em `ANALISE_GATE_E_COPILOT_2026-09-10.md`. Duas melhorias no fluxo, sem
mudar o fluxo (Codex discute → Gemini implementa → Claude revisa → Gemini ajusta).

- **Gate de verificação determinística** (`gate` no config, `enabled` nasce `true`).
  Depois que um operário edita arquivos, o Orquestra roda o teste/build do projeto
  (`subprocess`, zero LLM). Se falhar, devolve o log de erro pro **mesmo operário**
  consertar (`_GATE_REPROMPT`, teto `gate.max_retries`, default 2) antes de entregar
  ao Revisor; se não passar depois do teto, entrega assim mesmo com status
  "⚠️ Aguardando Aprovação (verificação falhou)". Detecção conservadora
  (`_detect_project_check`): `.orquestra_gate` (arquivo per-projeto, 1 comando por
  linha) > `gate.command` > auto (`pytest` / `npm test` / `tsc --noEmit` /
  `cargo check` / `go build`). Ambíguo, nada detectável, ferramenta ausente,
  timeout ou rc 127 → no-op silencioso, **nunca pune o operário**. Vale no REPL e
  na TUI (`_worker_lifecycle`, `_run_pipeline` só na última etapa, e os 2 handlers
  de worker da TUI). O próprio repo tem um `.orquestra_gate` = `python3 test_orquestra.py`
  (a suíte não é pytest, a auto-detecção não pegaria).
- **Cão de Guarda: Copilot → Claude-DeepSeek em `--permission-mode plan`.** A
  revisão de código do Copilot deixou de ser incluída em jun/2026 (vira AI Credits,
  200/mês compartilhados no plano estudante). O `cao_guarda` passa a rodar DeepSeek
  v4 Pro dentro do harness do Claude Code em modo read-only — **lê o codebase ao
  redor do diff** (quem chama a função alterada, arquivos vizinhos), custa ~centavos.
  `watchdog_verdict` ganhou `--permission-mode plan` + carga do `env_file` + timeout
  90→240s + prompt que autoriza leitura de arquivos e inclui riscos de segurança.
  Migração de `load_config` vira **só** o default antigo intocado (`copilot` +
  `auto`) — quem escolheu um modelo Copilot de propósito é preservado.
- **O achado do Cão de Guarda agora vai pro Orquestrador.** Antes só era impresso
  no terminal e ninguém consumia. `_watchdog_report(cfg)` faz print **e**
  `save_chat_message("orquestrador", ...)` — o Orquestrador vê o achado na próxima
  vez que você falar com ele. NÃO chama `_orchestrator_react` daqui (rodaria
  concorrente com o de `_worker_lifecycle`). Mesma rota nos 2 pontos automáticos +
  na revisão pós-Aprovar da TUI.
- **`save_chat_message` virou thread-safe** (`_chat_lock`) — a corrida entre
  operários paralelos já existia; o Cão de Guarda como mais um writer deixou óbvio.

Testado ao vivo: gate detectou o `.orquestra_gate` e rodou a suíte; Cão de Guarda
pegou um bug injetado (`a + b` → `a - b`) com verdict preciso do DeepSeek.
`test_orquestra.py`: 158 checks.

## Não lançado — endurecimento pós-análise de segurança (set/2026)

Análise ponta a ponta (relatório em `ANALISE_PONTA_A_PONTA_2026-09-08.md`).
Dois achados corrigidos:

- **A1 — delegação avulsa não tinha gate.** Um `<DELEGATE>`/`<PIPELINE>` que o
  Orquestrador emitia **fora** de um `<PLANO>` aprovado disparava o operário na
  hora, sem confirmação — e o operário roda com permissão total. Agora aparece
  a barra **▶ Rodar agora / ✋ Cancelar** com a lista do que vai rodar.
  Delegação **dentro** de um plano que você aprovou (botão ▶ Executar plano)
  continua sem pedir nada. Toggle `/auto on|off` (`ui.confirm_delegates`,
  nasce ligado = pede confirmação).
- **A2 — "❌ Rejeitar" apagava a árvore inteira.** `revert_pending()` fazia
  `git checkout`/`clean` de **tudo** que estava sujo — inclusive edição manual
  sua no Editor e o trabalho de outra tarefa pendente no mesmo repo. Agora
  reverte só os arquivos daquela tarefa (`revert_pending(only=...)`), usando a
  lista que o worker reporta (`changed_files`, que já é "sujo depois − sujo
  antes") e, como reforço, um baseline capturado no início da tarefa. Arquivos
  que já estavam modificados aparecem como **🛡️ Preservado** no chat.

## V6 — Palco + Chat (set/2026)

Layout novo (`PLANO_PALCO_CHAT.md`, plano revisado e implementado em 5 etapas):
o chat deixa de ser uma coluna lateral que misturava conversa com narração
crua, e vira uma faixa fina embaixo, largura cheia, só conversa. Em cima, um
**palco** com abas: ⚡ Atividade, ± Mudanças, 📝 Editor, 🎨 Idea Studio.

- **⚡ Atividade** — prompt completo mandado pro agente, texto ao vivo, tool
  calls, resultado final, veredito do Cão de Guarda, commit — tudo com
  timestamp e cor por agente (Orquestrador cyan, Operador green, Revisor
  blue, Cão de Guarda yellow). Texto multi-linha colapsa (1ª linha + "(+N
  linhas)") a menos que `/detalhe full`. `Ctrl+K` limpa.
- **± Mudanças** — diff de verdade (`git_diff_for_display`), colorido (+
  verde, − vermelho, `@@` ciano), com contador no título da aba. Arquivo novo
  (untracked) lido direto do disco, sem `git add -N` — isso quebraria o
  `revert_pending` (o "Rejeitar" pararia de limpar arquivo novo de verdade).
- **Orquestrador narra mesmo em modo plano** — antes só um heartbeat
  "pensando... (Ns)" aparecia; agora Codex/Claude/Agy mostram o que estão
  lendo. O prompt do Orquestrador também para de dizer "não lê arquivos"
  quando o motor de fato lê (modo plano/read-only).
- **Codex como Operador/Revisor destravado** — o `~/.codex/AGENTS.md` global
  (que define o Codex como Orquestrador, "nunca edita sozinho") sabotava
  silenciosamente o Codex quando escolhido como motor do Operador. Prefixo
  `[PAPEL: OPERADOR/REVISOR]` na instrução + cláusula de exceção no
  AGENTS.md resolvem isso.
- **`/detalhe` finalmente com efeito na TUI** — o parâmetro existia desde a
  V5 mas nunca era repassado pro motor; `<PIPELINE>` também deixa de ficar
  mudo entre etapas.
- **O Orquestrador presta contas** — depois que o Operador (ou uma etapa de
  `<PIPELINE>`) termina sem erro, o Orquestrador comenta o resultado; o chat
  mostra só a bolha dele, não mais a resposta crua do worker (que continua
  completa na Atividade). Toggle `/relato on|off` (liga por padrão).
- **Atalhos novos**: `Ctrl+1..4` (aba do palco), `Ctrl+B` (Arquivos),
  `Ctrl+↑/↓` (tamanho do chat, persistido), `Ctrl+K` (limpa Atividade),
  `Ctrl+L` (foco no chat). `/seguir on|off` controla se o palco pula sozinho
  pra Atividade a cada tarefa nova.

## V5 — Editor de verdade, Orquestrador proativo, Modo Aprendizado, OpenCode (set/2026)

- **Editor multi-abas de verdade** — o painel Editor era um único `TextArea`
  fixo em `language="python"`, sem salvar. Agora: `TabbedContent` com uma aba
  por arquivo (abrir um arquivo já aberto só foca a aba, nunca recarrega e
  perde edição não salva), sintaxe por extensão (`.py/.md/.json/.js/.sql/...`),
  `Ctrl+S` salva de verdade no disco, indicador `●` de "não salvo" na aba,
  `Ctrl+W` fecha a aba ativa. GPS (arquivos tocados pelo Operador) e a árvore
  de arquivos reaproveitam o mesmo mecanismo.
- **`<MOSTRAR arquivo="...">`** — o Orquestrador (zero ferramentas, não lê
  arquivo) agora sabe a árvore de arquivos do projeto (injetada no prompt,
  via `git ls-files`) e pode abrir um arquivo no Editor sozinho durante a
  conversa ("vou te mostrar o arquivo X"), sem o usuário precisar clicar em
  nada. Validação de path própria (só dentro da pasta do projeto).
- **Modo Aprendizado** (toggle `🎓` no topo) — pro usuário escrever código à
  mão e ter o Revisor como mentor, não como substituto: revisão automática em
  background a cada `Ctrl+S` (cooldown de 20s) + botão "🔍 Revisão profunda"
  sob demanda. Roda em modo **plano/read-only de verdade** (mesmas flags do
  Orquestrador: `--permission-mode plan`/`--mode plan`/`--sandbox read-only`/
  `--agent plan`) — o Revisor comenta, nunca escreve, garantido pela CLI, não
  só por texto no prompt.
- **Sugestões inline com Aceitar/Recusar** — o Revisor (no Modo Aprendizado)
  pode emitir `<SUGESTAO arquivo="..." linha_inicio="N" linha_fim="M"
  motivo="...">código</SUGESTAO>`; vira um cartão no painel Editor com
  ✅ Aceitar (aplica via `TextArea.replace` + salva) / ❌ Recusar (descarta),
  por trecho — não por diff inteiro.
- **Motor novo: OpenCode/OpenRouter** — `Op:`/`Rev:` agora também listam
  modelos chineses via OpenRouter (DeepSeek, Qwen, GLM, Kimi, MiniMax), usando
  a CLI `opencode` já logada na máquina (sem API key no cofre — mesmo padrão
  "conta logada" do claude/agy/codex).
- **Fix: chat vazando pra fora da caixa** — o `RichLog` do chat tinha
  `min_width=78` (padrão do Textual), que nunca deixava a linha quebrar abaixo
  disso — com os painéis Arquivos/Editor abertos e o chat mais estreito, a
  mensagem "vazava" e exigia rolagem horizontal pra ler. `min_width=1` resolve.

## V4 — TUI + arquitetura de 3 agentes (set/2026)

- **A TUI (`orquestra_tui.py`) virou a interface oficial.** `orquestra` abre a TUI;
  `orquestra --repl` cai no REPL antigo. Startup da TUI agora faz cofre + memória +
  bridge + log.
- **3 agentes + Cão de Guarda:** Orquestrador (estrategista), Operador (executor),
  Revisor (sob demanda), Cão de Guarda (Copilot automático).
- **Orquestrador sem ferramentas por construção** — novo `orchestrator_brain.py`
  (chat completions puro, DeepSeek/Ollama). Removidas as flags `--add-dir` /
  `--dangerously-skip-permissions` do comando do Orquestrador.
- **Cão de Guarda consertado** — antes `_run_copilot_watchdog()` definia a thread e
  nunca a disparava (no-op). Agora roda de verdade, 1x por turno, com o `git diff`
  no prompt.
- **Gate de plano restaurado** — o Orquestrador emite `<PLANO>` e para; a TUI mostra
  "▶ Executar plano / ✏️ Ajustar".
- `/modo` e `/detalhe` voltaram a funcionar (eram hardcoded).
- Config migra automaticamente da v1; `ollama_api_key` em texto puro é removida.
- Bridge HTTP agora exige token de sessão e valida os caminhos.
- `test_orquestra.py` — testes sem rede.
- **Chat aceita colar textos grandes** — o campo `#chat-input` era um `Input` de
  uma linha só, cujo `_on_paste` do Textual descarta tudo além da primeira linha
  colada (`event.text.splitlines()[0]`). Virou `ChatInput` (baseado em `TextArea`):
  cola o texto inteiro (multi-linha), Enter continua enviando e `Ctrl+J` insere
  quebra de linha manual.

## V3 (Cockpit) 🚀

## Novidades (V3 Cockpit)
Este arquivo documenta as grandes mudanças da arquitetura e da experiência de uso do terminal no Orquestra V3.

### 1. Sistema de Fila de Tarefas (SQLite)
- **Banco de Dados Local:** As execuções dos operários agora são persistidas em `~/.orquestra_chats/memory.db` na tabela `tasks`.
- Cada tarefa recebe um Status: `✅ Concluído`, `❌ Erro` ou `⚠️ Aguardando Aprovação`.
- O Orquestrador tem contexto da memória de longo prazo e sabe quando uma tarefa falhou.

### 2. O Pilar Cursor (Injeção Rápida e Human-in-the-loop)
- **Modo Seguro de Código:** O Orquestra agora bloqueia alterações feitas na surdina. Se o Agente mexer em arquivos, o Orquestra gera um `git diff --stat` invisível e trava a execução no status `⚠️ Aguardando Aprovação`.
- **Aprovar (`/aprovar [id]`):** Commita a mudança no repositório automaticamente.
- **Rejeitar (`/rejeitar [id]`):** Executa um `git reset --hard` e `git clean`, limpando todo o código malfeito pelo operário antes de você revisar.
- **Contexto Mágico (`@`):** Autocompletar inteligente via `prompt_toolkit`. Digite `@` para listar e selecionar arquivos da pasta. Quando a mensagem for enviada, o conteúdo real dos arquivos é injetado no prompt.

### 3. O Pilar Manus AI (Modo Estrategista)
- **Comando `/goal <objetivo>`:** Você dita uma meta macro, e o orquestrador divide esse pedido em uma cadeia (`<PIPELINE>`) de até 5 etapas, rodando-as sequencialmente e passando o output de uma para a próxima.
- Elimina a necessidade de microgerenciar o agente.

### 4. TUI Cockpit (Terminal Vitaminado)
- **Barra Inferior (Bottom Toolbar):** Barra de status nativa indicando o modelo ativo.
- **Atalhos Mágicos:** 
  - `F1`: Abre o modal de `/ajuda`.
  - `F2`: Abre o modal de Troca Rápida de Agente (`/agentes trocar`).
  - `F3`: Lista instantaneamente as Tarefas (`/tarefas`).
- Uma interface que se aproxima de aplicações robustas como VSCode, mantendo-se puramente baseada no console raiz.
