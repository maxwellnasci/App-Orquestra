# Análise: integração do Muse Code (Meta) como agente no Orquestra

**Data:** 2026-09-18
**Contexto:** o usuário assinou o plano básico mensal do **Muse Code**, o agente de
terminal que a Meta Superintelligence Labs lançou em beta em 05/08/2026 (modelo
próprio Muse Spark), e queria testar onde ele encaixa melhor dentro do Orquestra —
como Orquestrador (estrategista) ou como Operador (executor) — e entender até onde
dava pra "liberar o raciocínio máximo" dele sem estourar custo.

PR da implementação: **#1** (`worktree-muse-worker-integration` → `master`, ainda
como rascunho aguardando teste manual do usuário).

---

## 1. O que é o Muse Code e o que descobrimos testando de verdade

Antes de mexer em código, rodamos o binário direto (`muse --help`, `muse exec ...
--json`) pra não confiar só em documentação de marketing:

- **Protocolo próprio (MSP — Muse Session Protocol):** um log de eventos bem mais
  verboso que Codex/Claude/Antigravity — até pra responder "ok" ele já disparou
  2 subagentes internos automáticos (`skill-reminder`, `verify-reminder`) e gerou
  ~180KB de stream de evento. O texto final de verdade só chega no evento
  `run.terminal.completed`.
- **Approval mode default é `on-request`** — se chamado headless sem
  `--approval-mode never`, ele fica esperando uma aprovação que nunca chega
  (trava). Resolvido passando essa flag em toda invocação.
- **Workspace roda "untrusted" por padrão** — sem `--trust-workspace`, ele ignora
  completamente o `AGENTS.md`/skills do projeto. Isso na prática nos livrou do
  mesmo problema que o Codex já tinha (o `AGENTS.md` global dizendo "Orquestrador
  nunca executa sozinho" vazando pro papel de Operador) — não precisamos do
  mesmo prefixo `[PAPEL: ...] prevalece` que o Codex precisa.
- **`--reasoning-effort max` funciona no plano básico, sem aviso nenhum de
  limite.** `--reasoning-effort ultra` é bloqueado ("gate ultra_reasoning_effort
  is closed") e cai sozinho pra `xhigh` — ou seja, o teto real hoje é `max`, e
  ele já vem liberado.
- **Sem `--json`, o stdout já sai limpo** (só o texto final; todo o ruído de
  diagnóstico — skills carregadas, workspace untrusted, etc. — vai pro stderr
  sozinho). Isso permitiu usar o Muse no Cão de Guarda em modo texto puro, igual
  aos outros motores desse caminho.

---

## 2. Decisão de arquitetura: quem faz o quê

O usuário paga 3 assinaturas mensais fixas (Claude, Antigravity, Muse) + tem
acesso à API do DeepSeek (cobrada por uso). A pergunta era como distribuir isso
em 4 papéis sem redundância e sem custo surpresa.

**Princípio usado:** nenhum motor revisa o próprio código — quem escreve
(Muse) nunca é quem audita depois (evita viés correlacionado, onde o mesmo
raciocínio que errou também "aprova" o próprio erro).

| Papel | Motor final | Por quê |
|---|---|---|
| **Orquestrador** (estrategista, roda toda hora) | Antigravity (sem mudança) | Nunca toca em código — só roteia. Protocolo leve, resposta rápida; o Muse tem overhead alto demais (subagentes automáticos) pra rodar em *todo* turno de chat |
| **Implementador/Operador** (escreve código) | **Muse**, `reasoning_effort` — começou em `medium` pra validar a canalização, sobe pra `max` depois de confirmado que funciona | Uso de propósito do Muse: agente de terminal real, com sandbox/aprovação |
| **Cão de Guarda** (automático, todo diff) | **Claude puro** (sem DeepSeek) | Dispara com alta frequência — precisa ser assinatura fixa, não cobrado por uso |
| **Revisor** (sob demanda) | **Claude-DeepSeek Flash** | Só quando o usuário pede — aqui sim vale a pena um motor cobrado por token, porque a frequência é baixa |

### A pegadinha de custo que quase colocamos no lugar errado

A primeira ideia foi colocar o DeepSeek no Cão de Guarda. Calculamos o custo real
da API do DeepSeek V4 Pro (~$1.32/M tokens input, ~$3.96/M output, preço de
setembro/2026) contra o padrão de uso de uma sessão agêntica de revisão (~20-50
centavos por chamada): como o Cão de Guarda dispara **em todo diff automaticamente**
(15-20x/dia num dia puxado), isso batia com a estimativa do próprio usuário de
"uns $5/dia" — caro demais pra um papel de alta frequência. Já o Revisor, por ser
"só quando eu peço" (poucas vezes por dia), absorve bem esse mesmo custo por
chamada. **A lição: o preço por chamada de um motor pago por uso importa menos que
a frequência do papel onde ele é colocado.**

---

## 3. O que foi implementado (`orquestra.py`)

- `muse` registrado como binário reconhecido em **todos** os pontos de despacho:
  `_build_worker_cmd`, `_build_orchestrator_cmd`, `_narrate_muse_line` (parser do
  MSP), `watchdog_verdict` (Cão de Guarda, modo texto puro), menu `/agentes
  trocar` (`_FAMILIA_CHOICES` + `_apply_familia`), e o mapa de fallback
  automático de erro/quota (`fb_map`).
- Toda chamada ao Muse sai com `--approval-mode never` (evita a trava descrita
  acima) e sem `--trust-workspace` (evita carregar o `AGENTS.md` do projeto).
- **Revisor travado em "só sob pedido explícito"** — o prompt do Orquestrador
  (`_ORCH_SYSTEM`) antes sugeria, como exemplo padrão, encadear
  Operador→Revisor automaticamente num `<PIPELINE>`. Reescrito pra deixar
  explícito que o Revisor só entra se o usuário pedir revisão de verdade —
  agora que ele roda numa API paga por uso, encadear "por precaução" custaria
  dinheiro sem necessidade.
- **`_ORCH_CONTEXT_CHARS` (20000, era 3000/4000 espalhado pelo código)** — o
  resultado de um worker que chega até o Orquestrador (reação ao `<DELEGATE>`,
  relay entre etapas de `<PIPELINE>`, histórico salvo) não trunca mais o
  raciocínio/código de respostas longas antes de tempo. Pedido explícito do
  usuário: "o agente orquestrador precisa saber de tudo que está acontecendo...
  pra não ter perda de código e raciocínio".

---

## 4. Erros encontrados e corrigidos

Rodamos `/code-review 1 high` (revisão automatizada) em cima do PR antes do
merge. Achou 3 pontos:

### 🐛 Bug real — corrigido
**`_run_worker_headless` retornava uma tupla solta em vez de `WorkerResult`**
quando o binário não é encontrado no PATH (`except FileNotFoundError: return "",
True, msg`). Todo call site acessa `result.final_text` / `result.is_error` como
**atributo** de um dataclass — recebendo uma tupla, isso quebra com
`AttributeError`, silenciosamente (a tarefa nunca é marcada como erro, nenhum
failover é acionado). O bug já existia antes desta mudança, mas ficou bem mais
exposto agora que um binário novo (`muse`) acabou de entrar — é exatamente o
tipo de caminho que só é testado quando algo dá errado. Corrigido construindo o
`WorkerResult` de verdade nesse except.

### ⚠️ Trade-off aceito, documentado (não é bug)
A trava "Revisor só sob pedido" é só instrução de prompt — não tem gate de
código impedindo o Orquestrador de emitir `<DELEGATE binary="revisor">` mesmo
sem pedido. Isso é consistente com **todo** o resto da política do sistema hoje
(o próprio "espera minha aprovação antes de executar" também não tem checagem
mecânica) — decidimos não criar uma trava técnica nova só pro Revisor, mas
deixamos o motivo documentado no código pra quem ler depois.

### 📋 Lacuna conhecida, não corrigida ainda
`_narrate_muse_line` não preenche `cost_usd`/`tokens_in`/`tokens_out` — a feature
de gasto/uso vai reportar 0 pro Muse por enquanto. Não implementamos uma leitura
"no chute": ainda não confirmamos qual `payload_type` do protocolo MSP carrega
contagem de token de verdade (os testes feitos só mostraram bytes/tempo de
stream, não tokens). Documentado como pendência.

---

## 5. Outras descobertas da sessão

- **DeepSeek mudou de nome de modelo em 14/09/2026 04h UTC:** `deepseek-v4-flash`
  e `deepseek-v4-pro` viraram *aliases de compatibilidade* que agora servem o
  modelo novo (V4.1 Flash) por baixo — inclusive o `v4-pro`, que passou a
  responder como Flash (mais fraco, mais barato), não mais como Pro de verdade.
  Migramos o Revisor pro nome canônico novo (`deepseek-flash`) pra não depender
  de um alias legado que pode ser desativado no futuro.

---

## 6. Status

- ✅ Código implementado, testado (compila, suíte de testes existente
  passando, smoke test isolado do parser/comando do Muse).
- ✅ Revisão automatizada rodada, achados triados, bug real corrigido.
- ✅ Config aplicada em `~/.orquestra.json` com o time final.
- ✅ PR #1 aberto como rascunho, pushado.
- ⏳ Falta: teste manual do usuário delegando uma tarefa real pro Operador (Muse)
  dentro do app de verdade, antes do merge em `master`.
