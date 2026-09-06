# Changelog Orquestra

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
