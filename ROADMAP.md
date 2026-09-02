# Roadmap e quadro de ideias — Orquestra

Documento vivo. Conforme for usando a TUI no dia a dia, anote aqui o que incomoda e
o que dá pra melhorar.

## Arquitetura atual (V4)

TUI (Textual) como interface oficial. 3 agentes + Cão de Guarda:

- **🎼 Orquestrador** — estrategista, chat puro (`orchestrator_brain.py`), **zero ferramentas**.
  Conversa, faz o `<PLANO>` (com gate de aprovação) e delega.
- **🔨 Operador** — executa (lê/escreve/roda). Claude Code por padrão.
- **🔍 Revisor** — revisão sob demanda (botão ou pipeline).
- **🐕 Cão de Guarda** — Copilot automático, audita cada `git diff` antes do commit.

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

## 💡 Próximas ideias

- [ ] Menu `/agentes trocar` completo dentro da TUI (hoje só os botões do topo)
- [ ] `/goal` no REPL ainda usa o prompt antigo de estrategista — realinhar com o cérebro novo
- [ ] Revisor automático opcional depois de cada aprovação (config toggle)
- [ ] Limpar os roles v1 (`ajudante`, `fiscal`, `segurança`, `estrategista`) do `~/.orquestra.json`
- [ ] Failover de cérebro do Orquestrador (se DeepSeek cair, tenta Ollama)
- [ ] *(anote as suas aqui...)*

## 🐛 Bugs conhecidos

- Nenhum aberto no momento.

---
*Dica: "pega o item X do roadmap e implementa".*
