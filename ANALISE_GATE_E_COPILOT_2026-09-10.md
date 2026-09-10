# Análise: Gate determinístico + trocar o revisor automático (Copilot → DeepSeek)

**Data:** 2026-09-10
**Contexto:** o fluxo atual (Codex discute → Gemini implementa → Claude revisa →
Gemini ajusta) já está bom. Isto **não é mudança de fluxo** — são duas melhorias
pontuais pra ganhar qualidade de código, menos erros e mais confiança:

1. um **gate determinístico** que roda teste/build e realimenta o erro no operário
   antes de entregar ao revisor;
2. **trocar o revisor automático**: o Cão de Guarda sai do Copilot e passa a rodar
   com **DeepSeek** (via `Claude-DeepSeek` em modo plan), com a saída indo pro
   Orquestrador em vez de morrer no terminal.

**O Claude continua revisor sênior em tudo, inalterado.**

Ponto de partida foi um texto de terceiro sobre "por que o Claude Code se destaca"
(engenharia do harness). Diagnóstico dele: correto no geral, com dois deslizes —
nega o papel dos pesos do modelo (boa parte da vantagem é RL em uso agêntico) e
chama o loop de auto-correção do Claude Code de "determinístico" (na verdade é
dirigido pelo modelo). Das 3 recomendações do texto, só uma se aplica aqui: a
camada de feedback determinístico (o gate).

---

## 1. Como o Orquestra aplica edições hoje

**O Orquestra nunca aplica patch.** Delega 100% da edição a CLIs agênticas que
escrevem no disco sozinhas (`orquestra.py:_build_worker_cmd`, ~linha 3330):

| Operário | Comando | Quem aplica a edição |
|---|---|---|
| Claude | `claude -p … --permission-mode bypassPermissions` | harness do Claude Code |
| Gemini | `agy -p … --dangerously-skip-permissions --add-dir <proj>` | harness do Antigravity CLI |
| Codex | `codex exec … --sandbox workspace-write -o <file>` | harness do Codex CLI |
| Copilot | `gh copilot -p … --no-ask-user` | CLI do Copilot |
| opencode | `opencode run … --auto` | harness do opencode |

O único caminho com API crua é o **cérebro do Orquestrador**
(`orchestrator_brain.py`) — e por design ele **não edita nada**, só emite
`<DELEGATE>` / `<PIPELINE>` / `<PLANO>`.

**Consequência:** "padronizar formato de edição / lib de patch determinística" não
se aplica — o Orquestra não decide como salvar arquivo. A qualidade da edição do
Gemini depende do harness do `agy`, fora do nosso controle. Trocar isso por API
crua + motor de patch próprio seria regressão. **Não fazer.**

---

## 2. Melhoria 1 — o gate de verificação determinística

### Situação atual

Depois que um operário mexe na árvore:

- **Não há** verificação dirigida por exit code. O Orquestra não roda
  `pytest` / `ruff` / `tsc --noEmit` / `npm build` nem realimenta o erro no worker.
- Único retry existente: **stdout vazio** (instabilidade do modelo, ~linha 4196).
- Único failover: **erro de quota/429** (~linha 3603).
- A verificação que existe é **humana no loop**. Nada mecânico confirma que o
  código roda.

Resultado: o Claude (recurso escasso — 1 conta paga) gasta quota revisando código
que às vezes nem compila.

### O que muda

**Hoje:** operário edita → entrega pro Revisor/usuário (mesmo com build quebrado).

**Com o gate:** operário edita → **o script do Orquestra roda o teste/build sozinho**
→ se quebrar, devolve o erro pro **mesmo operário** (o Gemini, no fluxo atual)
consertar, teto de 2–3 tentativas → só entrega pro Revisor/usuário quando passa.

Quem roda o teste é **o `orquestra.py`**, via `subprocess.run(["pytest", …])`.
Nenhum agente, zero tokens, resultado binário. Só o **log de erro** volta pra um
modelo. É exatamente onde o Gemini é forte: com o erro exato do compilador, ele
quase não erra na 2ª tentativa.

### Esboço de implementação

Encaixa na máquina que já existe (snapshot de git diff, task tracking, failover):

1. Detectar o tipo de projeto (`pytest.ini`/`pyproject.toml`,
   `package.json`+`tsconfig.json`, `Cargo.toml`, `go.mod`…).
2. Após `_run_worker_headless` / cada etapa de `_run_pipeline`, se a árvore mudou,
   rodar o check correspondente em background.
3. Se exit ≠ 0: recapturar o log e re-prompta o mesmo operário —
   *"O código gerou este erro: [log]. Ajuste só o trecho necessário."* —
   teto de 2–3 tentativas.
4. Só então entregar ao Revisor / usuário.

### Riscos (todos pequenos)

- Projeto sem suíte de teste → gate roda só build/lint, ou pula com aviso.
- Suíte lenta → timeout + rodar em background.
- Nunca ficar em loop: depois do teto, entrega pro humano de qualquer jeito.

---

## 3. O Cão de Guarda / Copilot hoje — e por que não serve

Confirmado no código (`watchdog_verdict` em `orquestra.py:3465`,
`_run_copilot_watchdog` em 3529, chamadas em 3638 e 4422):

- **Dispara sozinho** depois de todo `<DELEGATE>` ou da última etapa de
  `<PIPELINE>`, **só se** o operário terminou sem erro **e** mexeu em arquivo.
- Pega o `git diff` (só 3 linhas de contexto, `--unified=3`) e manda pro Copilot
  via `gh copilot -p "<diff>"`.
- Roda em **thread daemon, não bloqueia**. Cooldown de 15s.
- **A saída só é impressa no terminal.**

### Dois problemas

1. **O veredito morre no terminal.** `has_bug=True` não faz nada: não volta pro
   operário, não entra no contexto do Orquestrador, não muda o status da task.
   Você paga por uma opinião que ninguém lê.
2. **Ele lê 3 linhas de diff — não executa nada.** Pega só o que um LLM enxerga
   lendo um trecho minúsculo. Não pega "não compila", "import quebrado", "teste
   vermelho", "quebrou quem chama isso 3 arquivos adiante".

O **papel** está certo (revisor barato, automático, sempre ligado). O **motor e a
fiação** estão errados.

---

## 4. Por que NÃO usar o Copilot pra esse papel

### A revisão de código do Copilot deixou de ser grátis (jun/2026)

Pesquisa de 10/set/2026:

- Em **1º de junho de 2026** o GitHub trocou "premium requests" por **AI Credits** e
  **a revisão de código deixou de ser incluída** — antes era. Agora **tanto a
  revisão no PR quanto no VS Code** consomem AI Credits (token-metered, custo
  imprevisível). Em repo **privado**, a revisão no PR ainda queima minutos de
  GitHub Actions.
- **Conta estudante = 200 AI Credits/mês**, e esse orçamento é **compartilhado**
  entre chat, agent mode, code review, Copilot CLI e Spaces. (Bate com o rodapé do
  usuário: 16/200, reseta em 20 dias, `gpt-5.6-luna` no Auto, ~0,54 AIC/sessão.)
- **Só o autocomplete é ilimitado** (sugestões inline enquanto digita). Isso não
  gasta nada — mas não é revisão.
- `--model auto` **não zera o custo** (billing novo é por token; não há tier 0×).

**Conta:** ~0,5 AIC por revisão → teto de ~400/mês, dividido com todo o resto que
se faz no Copilot. Como watchdog automático a cada edição, estoura em 1–2 semanas
de uso intenso. **Não existe "Copilot revisando sempre, o mês todo".**

### O CLI agêntico do Copilot existe e funciona (mas não muda a conta acima)

- `/home/max/.local/bin/copilot` — **GitHub Copilot CLI v1.0.83**, instalado e
  logado (`maxwellnasci`). É o mesmo motor do VS Code (modo agente): edita, roda
  shell, busca no codebase, tem `/review`, `/security-review`, MCP, subagentes.
- **O `gh copilot` desta máquina já redireciona pra esse binário.** Logo o
  comentário em `orquestra.py:3392` ("gh copilot não suporta `--output-format
  json`") **está desatualizado**.
- Hoje o Cão de Guarda chama `gh copilot -p` **sem `--allow-tool`** → o agente não
  lê arquivo nenhum, responde só com o texto colado. Dá pra ligar as tools de
  leitura (`--allow-tool read`, `shell(git:*)`, `--deny-tool write`), mas **isso
  não resolve o custo** — continua saindo dos 200 AIC.

### Copilot é bom em quê

- **Autocomplete** (o motivo real de usarem no VS Code): muito bom e rápido —
  modelo pequeno afinado pra latência + integração no editor.
- **Revisão**: nada de especial — modelo classe GPT com um prompt de review. Um
  modelo bom de código (DeepSeek) faz a mesma função.

**Conclusão:** Copilot pra revisão automática = a única opção onde o custo te
limita, sem ganho de qualidade que justifique. Sai do papel.

---

## 5. Melhoria 2 — DeepSeek como revisor de código automático

### A ideia (do usuário)

Um **membro do time focado só em código — lógica e segurança** — que:

- roda **automático** depois que o Operador (Gemini) termina;
- **analisa sem executar e sem editar** (read-only);
- **reporta os achados pro Orquestrador** (não pro terminal);
- o Orquestrador decide: trivial → manda o Operador ajustar; relevante → passa como
  contexto pro Claude; nada → segue.

É **um agente do time**, não um chat separado pra vigiar. A interface dele com o
usuário é via Orquestrador (encaixa no "Orquestrador presta contas", Etapa 4 do
Palco+Chat / achado A5).

### Por que DeepSeek, e qual atalho

O DeepSeek **já está ligado** no Orquestra — credencial em `~/.claude-deepseek-env`
(chave da conta **DeepSeek** pré-paga em platform.deepseek.com; **não** é Anthropic,
**não** é conta nova). Dois atalhos existentes:

| Atalho (`orquestra.py`) | O que é | Ferramentas | Modelo padrão |
|---|---|---|---|
| **`Claude-DeepSeek`** (`deepseek`, ~linha 111) | CLI do `claude` com `env_file` apontando `ANTHROPIC_BASE_URL` pro DeepSeek → **harness do Claude Code + modelo DeepSeek** | sim | `deepseek-v4-pro` |
| **`brain-deepseek`** (`orchestrator_brain.py`) | API de chat do DeepSeek direto | **não** — prompt→texto | `deepseek-v4-flash` |

**Usar `Claude-DeepSeek` em `--permission-mode plan` (read-only):**

- o revisor precisa **ler o codebase** (o diff + os arquivos de quem chama a função
  alterada) — o `brain-deepseek` não consegue (teria que colar tudo no prompt, o
  mesmo problema das "3 linhas" de hoje);
- o `Claude-DeepSeek` usa **o harness bom** (grep/glob/ler trechos, gestão de
  contexto — exatamente as vantagens do texto original), só que com modelo barato;
- `_build_worker_cmd` **já suporta** `read_only=True` → `--permission-mode plan`
  garante que ele analisa sem executar nem editar.

### Custo

DeepSeek V4 ≈ US$0,30/1M tokens de entrada. Uma revisão de diff + alguns arquivos
≈ 30–80k tokens ≈ **US$0,01–0,03 por revisão**. 100 revisões/mês ≈ US$1–3.
Praticamente ilimitado — o oposto dos 200 AIC do Copilot.

### Antes / depois

| | Cão de Guarda hoje (Copilot) | Revisor DeepSeek |
|---|---|---|
| Contexto | 3 linhas de diff | lê o codebase de verdade (harness) |
| Saída | printada no terminal, ignorada | vai pro Orquestrador, entra no relatório dele |
| Ação | nenhuma | Orquestrador roteia (Operador / Claude / segue) |
| Modo | — | read-only (`--permission-mode plan`) |
| Custo | 200 AIC/mês compartilhado, ~0,5/run | ~US$0,01–0,03/run, pré-pago DeepSeek |
| Escopo | "bug ou lógica errada" | lógica + segurança no diff |

### O que fazer com o Copilot

- **Recomendado:** tirar do papel de revisor automático. Deixar como **opção
  manual** — já está instalado e logado; o usuário chama quando quiser um segundo
  par de olhos e aceitar gastar AIC. Não dispara sozinho.
- Alternativa: remover de vez (`cao_guarda` aponta pro `deepseek`).

### Riscos (todos pequenos)

- Manter o **escopo apertado**: lógica + segurança no diff, não redesenhar
  arquitetura.
- **Não somar latência** no caminho crítico — rodar em paralelo / passada rápida,
  não bloqueando.
- **Não virar mais um painel/chat pra babá** — a saída entra no relatório que o
  Orquestrador já presta, só isso.
- Garantir o **read-only por flag** (`--permission-mode plan`), não só no prompt.
- Garantir **saldo** na conta DeepSeek.

---

## 6. Estratégia consolidada

Divisão de trabalho, do mais barato ao mais caro:

| Camada | Quem | Pega | Custo |
|---|---|---|---|
| **Gate** | script do `orquestra.py` | "não roda / teste quebrou / import errado" | 0 |
| **Revisor de código** | `Claude-DeepSeek` (plan / read-only) | lógica + segurança, quebra de quem chama; reporta ao Orquestrador | ~US$0,01–0,03/run |
| **Revisor sênior** | Claude (Opus-5) | arquitetura, segurança profunda, bug sutil, design | quota Anthropic |
| **2º par de olhos (manual)** | Copilot CLI, sob demanda | quando o usuário pedir | AI Credits |

Decisões:

- **Claude fica em tudo** — revisor sênior, inalterado. Não vira filtro nem
  opcional.
- **Gate primeiro** — ganho principal, menor risco (~40 linhas, nenhum agente
  novo).
- **Cão de Guarda: Copilot → DeepSeek** — mesmo papel no fluxo, motor barato, e a
  saída passa a servir pra algo (vai pro Orquestrador). Troca contida, não feature
  nova.
- **Copilot** — fora do automático; fica como opção manual.

---

## Referências no código

- `orquestra.py:49` — `DEEPSEEK_ENV = ~/.claude-deepseek-env`
- `orquestra.py:111` — atalho worker `deepseek` (`Claude-DeepSeek`)
- `orquestra.py:3330` — `_build_worker_cmd` (monta o comando; suporta `read_only`)
- `orquestra.py:3392` — comentário desatualizado sobre `gh copilot`
- `orquestra.py:3465` — `watchdog_verdict` (Cão de Guarda síncrono)
- `orquestra.py:3529` — `_run_copilot_watchdog` (thread daemon)
- `orquestra.py:3549` — `_run_pipeline` (encadeia operador → revisor)
- `orquestra.py:3603` — failover por quota/429
- `orquestra.py:4196` — retry por stdout vazio
- `orquestra.py:3638`, `4422` — pontos onde o Cão de Guarda é disparado
- `orchestrator_brain.py:72` — `_creds_deepseek()` (lê `~/.claude-deepseek-env`)

---

## Fontes (billing Copilot)

- GitHub Changelog — Copilot code review vai consumir Actions minutes em 1º/jun/2026:
  https://github.blog/changelog/2026-04-27-github-copilot-code-review-will-start-consuming-github-actions-minutes-on-june-1-2026/
- Codacy — "Copilot Code Review used to be included, from June 1st you pay twice":
  https://blog.codacy.com/github-copilot-code-review-used-to-be-included-from-june-1st-you-pay-twice
- push-based.io — GitHub Copilot AI Credits, a mudança de billing
- StudyAI Hub — Copilot grátis pra estudante 2026 (200 AI Credits/mês)
