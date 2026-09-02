# Changelog Orquestra

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
