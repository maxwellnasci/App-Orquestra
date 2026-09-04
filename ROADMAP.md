# Roadmap e quadro de ideias — Orquestra

Documento vivo. Conforme for usando a TUI no dia a dia, anote aqui o que incomoda e
o que dá pra melhorar.

## Arquitetura atual (V5)

TUI (Textual) como interface oficial. 3 agentes + Cão de Guarda, produtividade
+ aprendizado em segurança/código:

- **🎼 Orquestrador** — estrategista, chat puro (`orchestrator_brain.py`), **zero ferramentas**.
  Conversa, faz o `<PLANO>` (com gate de aprovação), delega, e pode mostrar
  código real durante a conversa (`<MOSTRAR arquivo="...">`).
- **🔨 Operador** — executa (lê/escreve/roda). Claude Code por padrão; também
  aceita OpenCode/OpenRouter (LLMs chinesas) como motor.
- **🔍 Revisor** — revisão sob demanda (botão ou pipeline) ou, no **Modo
  Aprendizado**, revisão automática ao salvar + sugestões inline (aceitar/recusar).
- **🐕 Cão de Guarda** — Copilot automático, audita cada `git diff` antes do commit.
- **📝 Editor** — multi-abas de verdade, sintaxe por extensão, `Ctrl+S`/`Ctrl+W`.

Ver `AGENTES.md` para a arquitetura completa e as regras que não podem quebrar.

## ✅ Feito

- [x] TUI vira a interface oficial (`orquestra` abre a TUI; `--repl` = REPL legado)
- [x] Orquestrador estrategista sem ferramentas (cérebro via chat completions: DeepSeek/Ollama)
- [x] Operador / Revisor / Cão de Guarda como agentes de verdade na config
- [x] Cão de Guarda funcionando (era código morto) — roda 1x/turno com o `git diff` no prompt
- [x] Gate de plano restaurado (`<PLANO>` → "▶ Executar / ✏️ Ajustar")
- [x] `<PIPELINE>` na TUI retoma sozinho após o Aprovar
- [x] "Aprovar" commita de verdade; "Rejeitar" reverte só os arquivos da tarefa
- [x] `/modo` e `/detalhe` reativados
- [x] Migração automática da config v1 → v2; `ollama_api_key` em texto puro removida
- [x] Bridge HTTP com token de sessão + validação de path
- [x] `test_orquestra.py` (testes sem rede)
- [x] Open Design religado: seção `open_design` na config (auto-semeada se o clone existir),
  `<DELEGATE binary="open-design">` e `/design` funcionando na TUI, prévia na aba Idea Studio,
  gate de aprovação se o Open Design escrever nos arquivos reais
- [x] Editor com abas de verdade (multi-arquivo), sintaxe por extensão, `Ctrl+S`
  salva no disco, `Ctrl+W` fecha aba, indicador de "não salvo"
- [x] `<MOSTRAR arquivo="...">` — Orquestrador abre arquivo real no Editor durante a conversa
- [x] Modo Aprendizado — toggle, revisão automática ao salvar (read-only de
  verdade) + botão de revisão profunda
- [x] Sugestões inline (`<SUGESTAO>`) com cartão Aceitar/Recusar por trecho
- [x] Motor OpenCode/OpenRouter (LLMs chinesas: DeepSeek, Qwen, GLM, Kimi, MiniMax)
- [x] Fix: chat vazava da caixa com os painéis abertos (`RichLog min_width`)

## 💡 Próximas ideias

- [ ] Menu `/agentes trocar` completo dentro da TUI (hoje só os botões do topo)
- [ ] `/goal` no REPL ainda usa o prompt antigo de estrategista — realinhar com o cérebro novo
- [ ] Revisor automático opcional depois de cada aprovação do Operador (config
  toggle — diferente do Modo Aprendizado, que é sobre edição manual)
- [ ] Limpar os roles v1 (`ajudante`, `fiscal`, `segurança`, `estrategista`) do `~/.orquestra.json`
- [ ] Failover de cérebro do Orquestrador (se DeepSeek cair, tenta Ollama)
- [ ] Sugestões inline (`<SUGESTAO>`) só existem no Modo Aprendizado hoje —
  estender pro "🔍 Revisar" tradicional (git diff multi-arquivo) é mais
  complexo, ainda não desenhado
- [ ] "X" clicável nas abas do Editor (hoje só `Ctrl+W` fecha) — Textual 8.2.8
  não tem isso nativo, precisaria de widget de aba customizado
- [ ] Toggle `🎓 Aprendizado` pode ficar espremido em terminais de ~80 colunas
  (mesma limitação de espaço do header já documentada em `NOTAS_TUI.md`)
- [ ] *(anote as suas aqui...)*

## 🐛 Bugs conhecidos

- Nenhum aberto no momento.

---
*Dica: "pega o item X do roadmap e implementa".*
