# Roadmap e quadro de ideias — Orquestra

Documento vivo. Conforme for usando a TUI no dia a dia, anote aqui o que incomoda e
o que dá pra melhorar.

## Arquitetura atual (V6 — Palco + Chat)

TUI reorganizada em **palco** (abas ⚡ Atividade / ± Mudanças / 📝 Editor /
🎨 Idea Studio, em cima, grande) + **chat** (faixa pequena embaixo, largura
cheia, só conversa — nunca narração crua de worker). Ver `PLANO_PALCO_CHAT.md`
pro plano completo (6 etapas, todas implementadas e testadas) e `AGENTES.md`
pros IDs fixos e as regras que não podem quebrar.

## Arquitetura anterior (V5)

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
- [x] Gasto e uso por LLM: `WorkerResult` captura custo/tokens/duração que já
  chegavam no stream do Claude/Agy/Codex e eram descartados (só Claude expõe
  custo real em $; Agy/Codex só tokens). Rodapé `#usage-footer` +
  `/uso`/`/saldo` (saldo real da DeepSeek via API) — é o "ganho rápido" que
  a Direção Estratégica pedia (ver abaixo)
- [x] Autoria por IA (índice paralelo, por arquivo): tabela `file_authorship`
  registra qual agente editou por último cada arquivo, gravada só quando a
  tarefa é aprovada — não mexe em `commit_pending`/`revert_pending`. Consulta
  via `/quem <arquivo>`
- [x] Atividade ao vivo no `<PIPELINE>`: religou a narração no
  `#status-indicator` entre etapas (antes usava callbacks no-op e ficava
  mudo)
- [x] Painel "📋 Plano" (`#plan-panel`): checklist ao vivo (⬜🔄✅❌) das etapas
  do `<PIPELINE>`, some quando Aprovar/Rejeitar resolve a tarefa
- [x] Gate por risco (`risk_gates`, nasce `enabled: false`): pula a pausa de
  aprovação se nenhum arquivo tocado bate em padrão sensível (`.env`,
  `secret`, `auth`, etc.) E o Cão de Guarda não achou nada — só no ponto de
  aprovação, não no `<PLANO>` (ainda não há arquivo pra classificar ali)
- [x] **Palco + Chat (set/2026, `PLANO_PALCO_CHAT.md`)** — layout novo:
  ⚡ Atividade / ± Mudanças / 📝 Editor / 🎨 Idea Studio como abas de um palco
  único em cima; chat vira faixa fina embaixo, largura cheia, só conversa.
  Orquestrador narra tool calls mesmo em modo plano (Codex/Claude/Agy); Codex
  como Operador/Revisor destravado (`[PAPEL: ...]` vs. `~/.codex/AGENTS.md`
  global); `/detalhe` finalmente com efeito na TUI; diff de verdade na aba
  Mudanças (sem tocar no índice do git — `git add -N` quebraria o
  `revert_pending`); Orquestrador presta contas depois de cada tarefa
  (`/relato on|off`); cor por agente, `/seguir on|off`, tamanho do chat
  persistido. 5 commits, testado em cada etapa (repo git isolado em `/tmp`,
  agentes mockados).

## 💡 Próximas ideias

- [ ] `codex exec resume --last` (existe no Codex CLI 0.152) em vez de
  reenviar o histórico inteiro a cada turno do Orquestrador — anotado na
  Etapa 5 do `PLANO_PALCO_CHAT.md`, não implementado ainda: precisa testar
  se o `resume` mantém `--sandbox read-only` e `-o`/`--output-last-message`.

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

## 🧭 Direção estratégica (set/2026)

Posicionamento: o Orquestra **não compete com Cursor/VS Code como editor** (LSP,
extensões, debugger — corrida perdida). Ele rege; o editor que o usuário quiser
toca (já existe a ponte com o Cursor). A aposta é no que as IDEs gringas não
conseguem ou não querem fazer por conflito de modelo de negócio.

### Onde o Orquestra já é diferente (manter e aprofundar)

- **Multi-fornecedor de verdade** — DeepSeek planeja, Claude executa, Codex revisa,
  Copilot audita, tudo com as assinaturas que o usuário já paga. Cursor/Copilot
  não fazem porque revendem um motor só. É a parte mais rara do projeto.
- **Orquestrador sem ferramentas + gate humano** — quem pensa não toca arquivo.
  Base pra governança.
- **Cão de Guarda** auditando todo diff antes do commit — nenhuma IDE tem nativo.
- **Modo Aprendizado** — contra-tendência ao "vibe coding" que atrofia júnior.
  Ninguém grande faz porque reduz uso do modelo (que é o que eles vendem).

### Três apostas (escolher UMA pela dor sentida depois de ~2 semanas de uso real)

- [ ] **1. Governança de dados (pitch empresa/LGPD)** — regra por caminho:
  arquivos sensíveis (segredos, dados de cliente) só vão pra modelo local
  (Ollama); o resto pode ir pra Claude/DeepSeek. Log por turno do que saiu da
  máquina e pra qual fornecedor. Cofre por projeto já existe; é crescer em cima.
  (`_classify_risk`/`risk_gates` já dá um classificador de caminho sensível —
  hoje só decide pausa/segue, ainda não decide *pra qual fornecedor* vai;
  é a peça que falta pra virar essa aposta de verdade.)
- [ ] **2. Camada de segurança automática (perfil ciber + portfólio)** — engordar
  o Cão de Guarda: varredura de segredo no diff, dependência com CVE, checklist
  OWASP quando o arquivo é rota web. Skills do Kali já na máquina.
- [ ] **3. Modo Aprendizado como produto** — histórico do que o Revisor corrigiu
  ao longo das semanas, "seus erros mais comuns", trilha. A memória de lições no
  SQLite já existe, só não está virada pro usuário. Mercado: bootcamp, faculdade,
  empresa treinando júnior.

Ganho rápido que ajuda as três: rodapé mostrando, por turno, qual fornecedor foi
usado e quanto de cota gastou (transparência que o Cursor esconde). **Feito**
— ver `#usage-footer`/`/uso` no "✅ Feito" acima.

### 🛡️ Júri de Segurança (depois das apostas, não antes)

Ideia: 3 modelos de fornecedores diferentes revisam o mesmo trecho em paralelo e o
resultado destaca consenso / só um achou / divergência.

O que já existe no código: DELEGATEs soltos rodam em paralelo; modo read-only
funciona nos 4 motores; o Orquestrador (chat puro, barato) já recebe o resultado
dos operários no histórico — **ele é o juiz, não precisa de 4º modelo**.

Decisões de desenho:
- Só segurança, não revisão genérica: CWE/OWASP dão taxonomia; cada jurado devolve
  achados em formato fixo (arquivo, linhas, categoria, severidade) e o cruzamento
  é feito **em código**, não por LLM. Mata o risco de "o juiz vira a resposta".
- Latência = jurado mais lento (paralelo), não 3x. Custo é cota de assinatura.
- Divergência entre modelos é sinal de aprendizado — encaixa no Modo Aprendizado.

Ordem obrigatória:
1. Experimento de custo zero: 3 arquivos com vulnerabilidade conhecida, botão
   Revisar trocando o motor (Claude → Codex → DeepSeek/OpenCode), comparar na mão.
   Se a divergência for trivial, a ideia morre barato.
2. Se for interessante: botão "🛡️ Júri" ao lado do "🔍 Revisar" (MVP reaproveita
   `_run_worker(read_only=True)` ×3 + prompt de síntese no Orquestrador).
3. Validar com o Daniel / programador pleno usando um caso real, não a descrição.

Referências de mercado: LLM Council (Karpathy), Poe (respostas lado a lado),
CodeRabbit (ensemble escondido), plugin Claude Security da Anthropic (painel
adversarial de 3 agentes com contagem em código). A tendência é confirmada; o
diferencial é o júri **multi-fornecedor**.

## 🐛 Bugs conhecidos

- Nenhum aberto no momento.

---
*Dica: "pega o item X do roadmap e implementa".*
