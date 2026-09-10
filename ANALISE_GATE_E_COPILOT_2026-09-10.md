# Análise: Gate de verificação determinística + Copilot como revisor

**Data:** 2026-09-10
**Contexto:** o fluxo atual (Codex discute → Gemini implementa → Claude revisa →
Gemini ajusta) já está bom. Isto **não é mudança de fluxo** — são duas melhorias
pontuais pra ganhar qualidade de código, menos erros e mais confiança:
1. um **gate determinístico** que roda teste/build antes de entregar ao revisor;
2. ligar o **Copilot no modo agêntico** (o mesmo do VS Code) no Cão de Guarda.

Ponto de partida foi um texto de terceiro sobre "por que o Claude Code se destaca"
(engenharia do harness). Diagnóstico dele: correto no geral, com dois deslizes —
nega o papel dos pesos do modelo (boa parte da vantagem é RL em uso agêntico) e
chama o loop de auto-correção do Claude Code de "determinístico" (na verdade é
dirigido pelo modelo). Das 3 recomendações do texto, só uma se aplica aqui: a
camada de feedback determinístico.

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

## 2. O gap real: falta o gate de verificação determinística

### Situação atual

Depois que um operário mexe na árvore:

- **Não há** verificação dirigida por exit code. O Orquestra não roda
  `pytest` / `ruff` / `tsc --noEmit` / `npm build` nem realimenta o erro no worker.
- Único retry existente: **stdout vazio** (instabilidade do modelo, ~linha 4196).
- Único failover: **erro de quota/429** (~linha 3603).
- A verificação que existe é **humana no loop**: Cão de Guarda comenta, Revisor
  comenta, usuário aprova/rejeita. Nada mecânico confirma que o código roda.

Resultado: o Claude (recurso escasso — 1 conta paga) gasta quota revisando código
que às vezes nem compila.

### O que muda com o gate

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

## 3. O Cão de Guarda / Copilot hoje

Confirmado no código (`watchdog_verdict` em `orquestra.py:3465`,
`_run_copilot_watchdog` em 3529, chamadas em 3638 e 4422):

- **Dispara sozinho** depois de todo `<DELEGATE>` ou da última etapa de
  `<PIPELINE>`, **só se** o operário terminou sem erro **e** mexeu em arquivo.
- Pega o `git diff` (só 3 linhas de contexto, `--unified=3`) e manda pro Copilot:
  *"analise só o diff em busca de BUGS ou lógica errada, ignore estilo; se achar,
  seja curto: arquivo, problema, impacto; senão responda 'Tudo certo'."*
- Roda em **thread daemon, não bloqueia**. Cooldown de 15s.
- **A saída só é impressa no terminal** (vermelho se detectar "bug/erro/falha",
  amarelo se não).

### Dois problemas

1. **O veredito morre no terminal.** `has_bug=True` não faz nada: não volta pro
   operário, não entra no contexto do orquestrador, não muda o status da task.
2. **Ele lê o diff — não executa nada.** Não roda teste/build/lint. Pega o que um
   LLM enxerga lendo: lógica torta, erro óbvio. Não pega "não compila", "import
   quebrado", "teste vermelho", "quebrou quem chama isso 3 arquivos adiante".

O papel está **certo** (revisor barato, automático, sempre ligado). Falta:
- tornar o veredito acionável (voltar pro Gemini, ou segurar a task);
- dar mais contexto (arquivos inteiros, não 3 linhas de diff).

---

## 4. Ligar o Copilot no modo agêntico (o mesmo do VS Code)

### Descoberta

O **GitHub Copilot CLI agêntico já está instalado e logado** na máquina:

- `/home/max/.local/bin/copilot` — **GitHub Copilot CLI v1.0.83**
- É o **mesmo motor do VS Code (modo agente)**: edita arquivos, roda shell,
  **busca no codebase**, tem `/review`, `/security-review`, `/plan`, `/delegate`,
  MCP, subagentes (`/tasks`), skills, seleção de modelo.
- Suporta `--output-format json` (JSONL), `-s` (só a resposta), `--add-dir`,
  `--allow-tool` / `--deny-tool`, `--model auto`.
- **O `gh copilot` desta máquina já redireciona pra esse binário**
  ("gh will execute the Copilot CLI found in your PATH").

**⇒ O comentário em `orquestra.py:3392`** ("gh copilot não suporta `--output-format
json`, flag só existe no agent CLI nativo") **está desatualizado.** Já estamos
chamando o CLI novo — só não estamos usando as capacidades dele.

### Por que hoje ele responde raso

`gh copilot -p "<diff>" --no-ask-user` **sem conceder ferramentas**. Sem
`--allow-tool`, o agente não lê arquivo nenhum — responde só com o texto colado no
prompt. Não é o CLI que é raso; é que ninguém deu permissão pra ele explorar.

### Como ligar (mesma função, motor de verdade)

Trocar a invocação do Cão de Guarda por algo como:

```
copilot -p "/review" \
  --add-dir <projeto> \
  --allow-tool 'read' --allow-tool 'shell(git:*)' \
  --deny-tool 'write' \
  --output-format json -s
```

Mesmo papel (watchdog automático após cada edição), mas agora ele:

- lê os arquivos alterados inteiros **e quem chama eles** (não 3 linhas de diff);
- roda `git diff` / `git log` sozinho;
- `--deny-tool write` garante que **revisa sem editar**.

### O custo — ponto a vigiar

O CLI nativo cobra **AI Credits** por execução (um teste trivial custou 0,43; uma
revisão lendo arquivos pode custar 1–5+). A conta é **Copilot Pro (Student)**
(logada como `maxwellnasci`), que tem cota mensal — ~300 premium requests/mês ou o
equivalente em AI credits no billing novo — mais o modelo base "ilimitado".
Conferir em github.com/settings/billing, ou no CLI interativo: rodapé, `/usage`,
`/statusline quota`.

Como watchdog **automático a cada edição**, isso soma. Mitigação:

- rodar o watchdog automático com `--model` num modelo leve e cooldown maior que
  os 15s de hoje;
- reservar revisão agêntica pesada só sob demanda;
- como o Claude já é o revisor sênior de tudo, o Copilot pode continuar sendo só a
  camada rápida e barata.

### "Copilot code review é ilimitado" — o que isso significa de verdade

O que se ouve falar (review ilimitado) é uma **feature do editor / do GitHub**, não
do CLI:

| Superfície | Custo | Orquestra usa? |
|---|---|---|
| Code review no VS Code (pedir review inline) | incluído em plano pago, **não consome premium requests / AI credits** | ❌ |
| Copilot reviewer no PR (github.com) | idem — incluído | ❌ |
| `copilot` CLI (`-p`, `/review`) | **medido em AI credits**, cada turno gasta | ✅ é o que o Cão de Guarda faz |

O CLI é superfície de billing separada — tem `--max-ai-credits` e autopilot pause
justamente porque é metered. **`--model auto` não zera o custo** (billing novo é
por token; o multiplicador 0x do billing legado vale pro modelo base, não é
"review grátis").

**Como aproveitar o review incluído (grátis) pelo Orquestra:**

- **Opção A (grátis, assíncrona):** o fluxo empurra branch/PR e o Orquestra pede o
  **Copilot PR review** via API GitHub (`request_copilot_review`). Essa revisão é
  a incluída, não gasta cota. Precisa de PR.
- **Opção B (barata, metered):** Cão de Guarda no CLI com modelo leve + cooldown
  maior.

Não existe "CLI + ilimitado" ao mesmo tempo.

---

## 5. Estratégia consolidada

Divisão de trabalho, do mais barato ao mais caro:

| Camada | Quem | Pega | Custo |
|---|---|---|---|
| **Gate** | script do `orquestra.py` | "não roda / teste quebrou / import errado" | 0 |
| **Cão de Guarda** | Copilot agêntico (read-only) | "lógica obviamente errada", quebra de quem chama | AI Credits |
| **Revisor** | Claude (Opus-5) | arquitetura, segurança, bug sutil, design | quota Anthropic |

Decisões:

- **Claude fica em tudo** — é o revisor sênior, inalterado. Não vira filtro nem
  opcional.
- **Gate primeiro** — é o ganho principal e o de menor risco (~40 linhas, nenhum
  agente novo).
- **Copilot agêntico** — melhoria do Cão de Guarda existente: mesmo papel, motor
  já instalado, custa só granular as permissões na invocação + vigiar AI Credits.
  O "review ilimitado" é a feature do VS Code / PR, não o CLI; pra usar a versão
  grátis pelo Orquestra, pedir Copilot PR review via API num branch/PR.
- Opcional, depois: tornar o veredito do Cão de Guarda acionável (devolver pro
  Gemini em vez de só imprimir).

---

## Referências no código

- `orquestra.py:3330` — `_build_worker_cmd` (montagem do comando de cada operário)
- `orquestra.py:3392` — comentário desatualizado sobre `gh copilot`
- `orquestra.py:3465` — `watchdog_verdict` (Cão de Guarda síncrono)
- `orquestra.py:3529` — `_run_copilot_watchdog` (thread daemon)
- `orquestra.py:3549` — `_run_pipeline` (encadeia operador → revisor)
- `orquestra.py:3603` — failover por quota/429
- `orquestra.py:4196` — retry por stdout vazio
- `orquestra.py:3638`, `4422` — pontos onde o Cão de Guarda é disparado
- `orchestrator_brain.py` — cérebro do Orquestrador (zero ferramentas, não edita)
