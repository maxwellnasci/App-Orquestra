# Validação do Open Design — 2026-08-15

Registro do que foi feito para colocar o [Open Design](https://github.com/nexu-io/open-design) funcionando de verdade (com o `agy`/Antigravity real, não um stub) antes de decidir como integrá-lo ao Orquestra.

## Contexto

O objetivo era só **validar** o Open Design — confirmar que ele gera artefatos de verdade usando o CLI de IA que já temos instalado e autenticado (`agy`, Antigravity CLI) — antes de pensar em como encaixá-lo no Orquestra. Este documento é o registro dessa validação, não uma decisão de arquitetura de integração (isso fica para depois).

## Repositório

Clone local em `/home/max/Documentos/Kali Linux/open-design` (upstream: `nexu-io/open-design`).

---

## 1. O problema inicial: Docker com CLI falso

O Open Design já estava rodando via Docker (`ghcr.io/nexu-io/od:latest`, serviço `open-design` do `deploy/docker-compose.yml`) em `localhost:7456`. A interface parecia funcionar, mas **qualquer prompt enviado ao agente sempre retornava a mesma frase fixa**: `"Agent connected successfully."`, sem nunca gerar um artefato real (`artifactCount: 0` em toda run).

**Causa raiz:** dentro do container, `/bin/agy` não era o CLI real do Google — era um script shell forjado (adicionado manualmente ao container em execução, fora da imagem oficial, confirmado via `docker diff`) que só simulava a interface esperada:

```sh
#!/bin/sh
if [ "$1" = "--version" ]; then echo "1.0.0"; exit 0; fi
...
echo "Agent connected successfully."
exit 0
```

Achamos também que `deploy/docker-compose.yml` tinha uma alteração local não commitada (`read_only: true` → `false`) — exatamente o que seria necessário para conseguir escrever esse stub dentro do container. Ou seja: alguém (uma sessão anterior) tinha tentado ligar o `agy` real ao Docker e parado no meio do caminho, deixando esse stub no lugar.

**Ação:** removido o container quebrado (`docker compose down`) e revertido o `docker-compose.yml` (`git checkout`). O volume `open-design_open_design_data` (dados dos projetos de teste feitos via Docker) foi mantido, não apagado.

---

## 2. Por que trocamos de Docker para execução nativa

O Open Design é desenhado para **delegar todo o loop de agente ao CLI que o usuário já tem instalado e autenticado no PATH** — ele não reimplementa nada, só detecta, invoca e faz streaming da saída. Isso é ótimo para uso nativo (desktop), mas o Docker isola o container do host, então ele nunca teria acesso ao `agy` real (`/home/max/.local/bin/agy`, v1.1.13, já autenticado) nem ao estado de sessão OAuth em `~/.gemini/antigravity-cli` e `~/.gemini/antigravity`.

Em vez de montar o binário + tokens de sessão reais dentro do container (arriscado: dois processos — o `agy` nativo do terminal e uma cópia rodando dentro do Docker — disputando e possivelmente invalidando o mesmo token OAuth), optamos por **rodar o Open Design nativamente no host**, que é o modo de uso principal documentado pelo próprio projeto (Docker é descrito como "o caminho mais fácil para iniciantes", não o modo primário).

---

## 3. Obstáculos para rodar nativo (e como foram resolvidos)

### 3.1 Node do Kali sem suporte a TypeScript

O `nodejs` do repositório do Kali/Debian (`24.18.0+dfsg+~cs24.13.2-1`) é recompilado **sem o módulo `amaro`** (type-stripping do TypeScript nativo do Node 24), por política de "free software guidelines" do Debian (o `+dfsg` na versão indica isso). O `postinstall` do Open Design usa `node --experimental-strip-types` e falhava com `ERR_NO_TYPESCRIPT`.

**Fix:** baixado o binário oficial do Node 24.18.0 direto de `nodejs.org` para uso **só deste projeto** (sem alterar o `nodejs` do sistema):

```bash
curl -fsSL -o node.tar.xz https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-x64.tar.xz
mkdir -p node-official && tar -xJf node.tar.xz -C node-official --strip-components=1
export PATH="<caminho>/node-official/bin:$PATH"
```

> Esse Node oficial ficou em `/tmp/claude-.../scratchpad/node-official` (pasta de sessão temporária). **Se essa pasta for limpa, é só baixar de novo** — não precisa fazer nada além de repetir o comando acima antes de rodar `pnpm install`/`pnpm tools-dev`.

### 3.2 `node-pty` (dependência nativa do daemon) sem build aprovado

O gate interativo do pnpm (`pnpm approve-builds`) para permitir scripts de build de dependências nativas não cooperou via terminal não-interativo. Compilamos manualmente:

```bash
cd node_modules/.pnpm/node-pty@1.1.0/node_modules/node-pty
node-gyp rebuild
```

### 3.3 Onboarding trava em login na nuvem

A tela inicial (`/onboarding`) exige login na "Open Design Cloud" (serviço próprio deles, nome interno "Vela"/AMR) antes mesmo de mostrar a opção de usar um CLI local — e esse login estava falhando (`[amr-login] startVelaLogin failed`). Isso é irrelevante para o nosso caso de uso (queremos só o CLI local), mas o fluxo de onboarding não deixa pular direto.

**Fix:** como já existia um `.od/app-config.json` de uma tentativa nativa anterior (mesma pessoa/sessão que mexeu no Docker), completamos os dois campos que a própria tela gravaria ao terminar o onboarding normalmente:

```json
{
  "onboardingCompleted": true,
  "agentId": "antigravity"
}
```

Local do arquivo: `/home/max/Documentos/Kali Linux/open-design/.od/app-config.json` (esse é o "data dir" do modo dev — `OD_DATA_DIR` sobrescreve, senão é `<repo>/.od`).

---

## 4. O bug real do adaptador `antigravity` (encontrado e corrigido)

Mesmo com o `agy` real conectado (autenticado, respondendo com texto genuíno em vez do stub), o Open Design **não conseguia gerar artefatos** — o agente sempre respondia com uma saudação genérica ("Hello! How can I help you today?"), nunca escrevia arquivo.

Isolamos o problema rodando `agy` manualmente no terminal, do jeito exato que o Open Design invoca:

| Teste | Comando | Resultado |
|---|---|---|
| 1 | `echo "<prompt>" \| agy -p -` (stdin) | Saudação genérica, nenhum arquivo |
| 2 | `agy --dangerously-skip-permissions -p -` (stdin + skip perms) | Saudação genérica, nenhum arquivo |
| 3 | `agy --dangerously-skip-permissions -p "<prompt>"` (**argumento direto**) | ✅ Gerou o arquivo — mas na pasta scratch interna do `agy` (`~/.gemini/antigravity-cli/scratch/...`), não no `cwd` |
| 4 | `agy --dangerously-skip-permissions --add-dir <cwd> -p "<prompt>"` | ✅ **Gerou o arquivo no diretório certo** |

**Duas causas, dois fixes**, aplicados em `apps/daemon/src/runtimes/defs/antigravity.ts` (função `buildArgs` do `antigravityAgentDef`):

1. **Stdin quebrado nesta build do `agy` (v1.1.13).** O código original do Open Design mandava o prompt via `agy -p -` (lendo de um pipe/stdin) — documentado no próprio código-fonte como o jeito certo para a v1.0.3, mas não funciona na v1.1.13 instalada aqui: o processo nunca lê o conteúdo do pipe e sempre cai na saudação padrão. **Fix:** o prompt passou a ir como valor direto do argumento `-p` (`args.push('-p', prompt)`), e `promptViaStdin` foi trocado de `true` para `false`.

2. **Workspace do `agy` não segue o `cwd` do processo.** Mesmo recebendo o prompt certo, o `agy` grava arquivos na pasta interna dele por padrão, ignorando o diretório de trabalho do processo-filho que o Open Design cria (que já era o diretório correto do projeto). **Fix:** adicionado `--add-dir <cwd>` no `buildArgs`, usando o `runtimeContext.cwd` que o Open Design já calcula.

Também foi necessário adicionar `--dangerously-skip-permissions`: em modo headless (sem terminal interativo) o `agy` tenta pedir confirmação antes de escrever arquivos; sem essa flag, a escrita é cancelada silenciosamente e o processo ainda assim termina com sucesso (`exit 0`) — mesmo padrão de bug já documentado no código de outro adaptador do próprio Open Design (`grok-build.ts`, que usa `--always-approve` pelo mesmo motivo).

> ⚠️ **Nota de segurança:** `--dangerously-skip-permissions` faz o `agy` aprovar sozinho qualquer ação de ferramenta (escrever arquivo, rodar comando, etc.) sem pedir confirmação — é o mesmo trade-off que já existe em outros adaptadores deste projeto para viabilizar execução headless. Vale ter em mente ao decidir a superfície de exposição quando isso for integrado ao Orquestra (ex: rodar em ambiente isolado/sandboxed, limitar diretórios via `--add-dir`, etc.).

**Resultado do teste final:** pedimos "Gere e salve agora um arquivo index.html com uma landing page completa (hero, features, CTA) para um app de lista de tarefas..." e o Open Design gerou de fato uma landing page completa e funcional ("TaskPulse" — hero, CTAs, mockup interativo, seções de recursos/demonstração/resultados), salva como `index.html` (21.2 KB) dentro do projeto, visível e navegável na própria interface.

---

## 5. Estado atual (ago/2026)

- **Servidor nativo rodando em background**, iniciado com `pnpm tools-dev` dentro de `/home/max/Documentos/Kali Linux/open-design`.
- ⚠️ **A porta muda a cada `pnpm tools-dev` novo** (escolhida livre dinamicamente). O terminal onde ele roda imprime a URL assim que sobe (`- web: started · running · http://127.0.0.1:<porta>`).
- **Node oficial (necessário pro TypeScript funcionar, seção 3.1) foi movido pra fora do `/tmp`**, que é `tmpfs` nessa máquina e é apagado a cada reboot. Ele agora mora em `~/.local/opt/node-open-design` (permanente).

### Como usar amanhã (ou depois de qualquer reboot)

Criei um launcher em `~/.local/bin/open-design-dev` (mesmo padrão do launcher `orquestra` do seu projeto). Depois de ligar a máquina, é só abrir um terminal e rodar:

```bash
open-design-dev
```

Isso entra na pasta certa, usa o Node oficial certo e sobe `pnpm tools-dev`. Ele fica **rodando em primeiro plano** nesse terminal — deixe a janela aberta, e a URL aparece impressa assim que o daemon/web sobem (algo como `http://127.0.0.1:<porta>`). Pra parar, `Ctrl+C` nesse terminal.

Se quiser rodar em background em vez de deixar o terminal ocupado:
```bash
nohup open-design-dev > /tmp/od-dev.log 2>&1 &
tail -f /tmp/od-dev.log   # acompanha até a URL aparecer, depois Ctrl+C só do tail
```
- **Docker do Open Design removido** (`docker compose down`), `docker-compose.yml` revertido ao original do git. O volume de dados (`open-design_open_design_data`) continua existindo caso queira voltar a usar essa via depois — mas lembre que ela **não** teria acesso ao `agy` real sem repetir algum tipo de bind-mount (não fizemos isso, optamos pelo nativo).
- **Config local não versionada** em `.od/app-config.json` (dentro do clone do open-design) tem `onboardingCompleted: true` e `agentId: "antigravity"` — não é um arquivo do git, é estado local de máquina.
- **Patch aplicado e salvo** em `apps/daemon/src/runtimes/defs/antigravity.ts` (não commitado ainda — é uma modificação local no clone). Vale considerar depois se abrimos um PR pro upstream (`nexu-io/open-design`) com esse fix, já que é um bug real e reproduzível na build v1.1.13 do `agy`.

---

## 6. Segunda rodada de testes (15/ago, à tarde) — dois bugs achados e corrigidos

Continuando o "testar à vontade" do item 6 original: usando o Open Design nativo pra valorar a ideia, apareceram dois problemas reais (um na interface, outro no adaptador do `agy`). Ambos foram corrigidos no clone local. **Nenhum commit foi feito** — combinado com o usuário manter só documentado aqui em `.md` por enquanto, já que este projeto ainda é um teste (a intenção é dobrar o Open Design pra dentro do Orquestra depois, e só aí pensar em GitHub/commits de verdade).

### 6.1 Crash de React ao trocar projeto/skill com preview aberto

**Sintoma:** console do navegador mostrava `Cannot update a component (ForwardRef(ClientPooledIframe)) while rendering a different component (AppInner)`, com a stack apontando pra `invalidateKey` → `removeEntry` → `evictProject` → `handleProjectChange` → `AppInner`.

**Causa raiz:** em `apps/web/src/App.tsx`, `handleProjectChange` chamava `iframeKeepAlivePool.evictProject(...)` **dentro da função updater passada a `setProjects`**. Updaters de `useState` precisam ser puros — o React pode invocá-los durante a própria fase de render, e isso disparava, em cascata e de forma síncrona, os listeners do `useSyncExternalStore` dentro de `ClientPooledIframe` (o preview em iframe) tentando se atualizar no meio do render de `AppInner`.

**Fix:** movida a leitura do projeto anterior e a decisão de evicção pra fora do updater, usando o `projectsRef` que o próprio arquivo já mantém sincronizado com o estado (`App.tsx:1036-1038`). `evictProject` agora roda como efeito colateral normal antes de `setProjects`, e o updater ficou puro.

**Verificação:** `tsc -b --noEmit` limpo em `apps/web`; reiniciei `daemon`+`web`+`desktop` via `tools-dev restart` e conferi o log (`.tmp/tools-dev/default/logs/web/latest.log`) depois do restart — sem nenhuma recorrência do erro.

### 6.2 Lista de modelos do Gemini congelada desde maio (Flash novo não aparecia)

**Sintoma:** o usuário notou que um Gemini Flash mais novo não aparecia no seletor de modelo do Open Design.

**Causa raiz:** `apps/daemon/src/runtimes/defs/antigravity.ts` mantinha os modelos como uma **lista fixa escrita à mão no código** (`fallbackModels`), copiada do TUI do `agy` em 2026-05-28, comentário no próprio arquivo confirmando isso. Não havia nenhuma consulta ao vivo — só esse snapshot congelado.

**Descoberta:** o `agy` instalado (v1.1.13) já ganhou, depois de maio, um subcomando real `agy models` (resolve o issue upstream #35 que o comentário antigo citava como pendente), incluindo saída em JSON (`agy --output-format json models`) com id curto + label. Rodando na hora: 15 modelos disponíveis hoje, incluindo `Gemini 3.7 Flash` e `Gemini 3.6 Flash` (nenhum dos dois estava na lista velha de 8 modelos).

**Fix:** adicionado `listModels` ao `antigravityAgentDef` (roda `agy --output-format json models` e faz parse do JSON), com `fallbackModels` só como rede de segurança pra quando a consulta ao vivo falhar (atualizado pro snapshot de hoje, 15 modelos). A picker agora busca a lista real a cada consulta em vez de depender de alguém lembrar de editar o código toda vez que o Google lança modelo novo.

**Bug secundário achado no caminho:** a consulta ao vivo (`listModels`) estava caindo em fallback **silenciosamente**, sem log de erro. Causa: `execAgentFile` (helper genérico em `apps/daemon/src/runtimes/invocation.ts`, usado por todos os adaptadores pra rodar `--version`/`listModels`/etc.) deixa o stdin do processo filho aberto como pipe, sem nunca fechar. O `agy models` (diferente do `agy --version`) trava tentando ler stdin antes de responder — sem EOF chegando nunca, o probe ficava pendurado até estourar o timeout (15s) e cair no fallback. Fechar o stdin do filho imediatamente após o spawn resolveu — a chamada volta em ~3s. Esse fix beneficia **todos** os adaptadores que usam esse helper (reparei que `opencode` também passou a vir com `modelsSource: "live"` depois do fix, onde antes possivelmente também caía em fallback pelo mesmo motivo).

**Verificação:** `tsc --noEmit` limpo no `daemon`; reiniciei tudo via `tools-dev restart` e bati direto em `GET http://127.0.0.1:<porta-daemon>/api/agents` — confirmado `"modelsSource": "live"` com os 15 modelos certos pro `antigravity`.

### 6.3 Claude Code — mesmo sintoma, causa diferente (não tem como "puxar automático")

Depois de conectar o Claude Code no Open Design, o usuário pediu pra checar se a mesma coisa acontecia aqui. Investigado e é um caso **diferente**, não um bug:

- `claude --help` não tem subcomando `models` (confirmado rodando na mão) — ao contrário do `agy`, não existe fonte programática pra listar modelos. O próprio código do Open Design já documenta isso em `claude.ts:40` ("claude has no list-models subcommand").
- O `fetchModels` que o adaptador já tinha (`loadMmdRouteModels`) não é uma listagem ao vivo da Anthropic — é outra coisa: lê um arquivo opcional `~/.config/mms/model-routes.json` com rotas de modelos de terceiros via proxy (ex: usar Claude Code apontando pra um backend compatível tipo DeepSeek). Sem esse arquivo configurado, cai no fallback estático mesmo — comportamento correto, não bug.
- Login do usuário no `claude` é via OAuth do `claude.ai` (assinatura Pro), não API key — então também não dá pra simplesmente chamar `models.list()` da API da Anthropic como alternativa, essa rota pede API key.

**Fix aplicado:** só atualizada a lista estática `CLAUDE_FALLBACK_MODELS` (`apps/daemon/src/runtimes/defs/claude.ts:6-16`), que estava na geração antiga (`claude-opus-4-5` / `claude-sonnet-4-5` / `claude-haiku-4-5`, sem alias "fable"). Trocado pro snapshot atual: aliases `sonnet` / `opus` / `haiku` / `fable`, mais `claude-sonnet-5`, `claude-opus-5`, `claude-fable-5`, `claude-haiku-4-5-20251001`.

**Diferença importante pro item 6.2:** aqui não existe jeito de "puxar automático" — sem subcomando de listagem no CLI, essa lista vai ficar desatualizada nas próximas gerações de modelo de novo, do mesmo jeito, e só dá pra corrigir editando o código na mão quando alguém notar.

**Verificação:** `tsc --noEmit` limpo no `daemon`; reiniciei tudo via `tools-dev restart` e conferi em `GET /api/agents` — `claude` aparece com `modelsSource: "fallback"` (esperado) e a lista nova de 9 modelos.

### 6.4 Estado dos arquivos

Nenhum commit feito. Alterações continuam só no working tree do clone local (`/home/max/Documentos/Kali Linux/open-design`), em quatro arquivos (os três de antes + `claude.ts`):
- `apps/web/src/App.tsx` (fix do crash de React, item 6.1)
- `apps/daemon/src/runtimes/defs/antigravity.ts` (lista de modelos ao vivo, item 6.2, + patch de execução real do `agy` do item 4, ainda também não commitado)
- `apps/daemon/src/runtimes/invocation.ts` (fix do stdin travado, item 6.2)
- `apps/daemon/src/runtimes/defs/claude.ts` (snapshot da lista de modelos atualizado, item 6.3)

Repo não tem identidade git configurada (nem local nem global) — decisão consciente de não configurar agora, dado que o projeto ainda é exploratório.

---

## 7. Próximos passos (combinado com o usuário)

1. Continuar testando o Open Design nativo à vontade (interface em `http://127.0.0.1:<porta-atual>`).
2. Depois de terminar os testes: montar um **scanner completo do Orquestra** (tarefa separada, ainda não iniciada).
3. Só então definir a forma "mais profissional e ousada" de integrar o Open Design ao Orquestra — ainda em aberto, nenhuma decisão de arquitetura tomada até aqui.
4. Quando o usuário conectar outros CLIs (Claude Code, "claude-deepseek"): considerar aplicar o mesmo padrão do item 6.2 (`listModels` ao vivo em vez de lista fixa) nos adaptadores correspondentes, se ainda não tiverem.

---

## 8. Primeiro passo real de integração: comando `/design` no Orquestra (15/ago, fim da tarde)

Depois de discutir a visão geral (Orquestra centraliza tudo, cada ferramenta é um "agente especial", tudo amarrado à pasta aberta, resultado sempre volta pra conversa/histórico do Orquestra pra ele ir aprendendo), o primeiro pedaço concreto implementado foi: **o Orquestra sabe abrir o Open Design já ligado à pasta do projeto atual.**

### 8.1 O mecanismo (descoberto, não inventado)

O Open Design já tem exatamente o hook necessário — não foi preciso construir nada novo do lado do Open Design:

- CLI própria do daemon: `apps/daemon/bin/od.mjs`, comando `od project create --metadata-json <arquivo>`. Passando `{"linkedDirs": ["<pasta>"]}` no JSON, o projeto criado **lê** aquela pasta local (vira `--add-dir` pro agente, acesso read-only) mas **escreve** no sandbox próprio do Open Design (`.od/projects/<id>/`), não na pasta principal — exatamente o comportamento que o usuário testou na mão antes e gostou ("ver o resultado do lado antes de implementar").
- ⚠️ **O binário se chama `od`, mas colide com `/usr/bin/od`** (octal dump do coreutils, vem primeiro no PATH). Chamado sempre via caminho completo: `node "<repo>/apps/daemon/bin/od.mjs" ...` — nunca `od` "pelado".
- Descoberta da URL do daemon/web (a porta muda a cada `tools-dev` novo, ver item 5): `pnpm exec tools-dev status --json`, rodado dentro do clone do open-design com o Node oficial no PATH (mesma exigência de TS-stripping do item 3.1 — `od.mjs` não precisa disso porque só importa o `dist/cli.js` já compilado, mas `tools-dev status` roda TS fonte direto).

### 8.2 Implementado em `orquestra.py`

- Config nova em `~/.orquestra.json`: seção `open_design` com `repo_path` (clone do Open Design) e `node_bin_dir` (Node oficial) — merge automático em `load_config()`, igual já acontecia pra `workers`.
- Funções novas (seção "Integração: Open Design" no `orquestra.py`, logo antes de "Utilitários"): `open_design_tools_dev_status`, `open_design_cli`, `load_open_design_cache`/`save_open_design_cache`, `open_design_open_for_project`.
- **Cache por pasta**: reaproveita o mesmo diretório por-projeto que o histórico de chat já usa (`get_project_chat_dir()`, hash da pasta atual), num arquivo `open_design.json` guardando `{project_dir, project_id}`. Primeira vez que roda `/design` numa pasta: cria o projeto no Open Design. Da próxima vez: abre o mesmo direto (valida antes com `od project info` se ainda existe).
- Comando novo: `/design` (alias `/open-design`), registrado no loop principal e no `/ajuda`. Se o `daemon`/`web` do Open Design não estiverem rodando, avisa pra rodar `open-design-dev` primeiro em vez de travar.
- Ao abrir, salva uma linha no histórico do "orquestrador" (`save_chat_message`) com a URL — mesmo mecanismo que os operários de CLI (tmux) já usam pra trazer resposta de volta pra conversa.

### 8.3 Bug achado no caminho (não relacionado ao Open Design): servidor tmux quebrado

No primeiro teste real (`orquestra` na pasta `~/Documentos/Kali Linux/arbo`), o launcher abriu e fechou na hora, mostrando só `[exited]` sem nenhum erro visível — porque o `orquestra` (launcher bash) roda `python3 orquestra.py` **dentro de uma sessão tmux nova**, e se o processo crasha, o tmux mata o pane junto, levando o traceback junto.

Reproduzido rodando com `remain-on-exit on`: `FileNotFoundError: [Errno 2] No such file or directory` em `os.getcwd()`, dentro de `print_banner()` — ou seja, quebrava antes mesmo do usuário poder digitar qualquer coisa, incluindo `/design`. Isolado: **não era da pasta `arbo`, nem do `orquestra.py`, nem do Open Design** — reproduzido até com `python3 -c "import os; os.getcwd()"` puro, em pastas totalmente normais tipo `/home/max`. O servidor **tmux** em si (processo persistente, sobrevive entre sessões) estava com o contexto de working-directory corrompido — qualquer painel novo que ele criava herdava isso.

**Fix:** `tmux kill-server` (autorizado pelo usuário antes — só existia uma sessão quebrada rodando, nada de trabalho sendo perdido). Servidor novo resolve `cwd` normalmente. Confirmado com o mesmo teste (`os.getcwd()` limpo) antes de repetir o teste real do `/design`.

### 8.4 Teste real, de ponta a ponta

Com o tmux limpo, rodado o `orquestra` de verdade (via tmux, não só chamando a função Python direto) na pasta `arbo`, mandando `/design` com `tmux send-keys` (simulando o usuário digitando):

1. 1ª chamada: `Nenhum projeto do Open Design ligado a esta pasta ainda — criando...` → criou e abriu `http://127.0.0.1:35665/projects/ef0f5cd9-2dde-4d20-b423-757fa53d2648`.
2. 2ª chamada (mesma pasta): reaproveitou o mesmo projeto direto, sem duplicar.
3. Saída limpa com `/sair` → `n` (não gerar resumo) → tmux encerrou sozinho.

**Nota:** a pasta `arbo` já tinha um projeto "Web Prototype" (`99636ba0-...`) criado manualmente pelo usuário antes, também com `linkedDirs` apontando pra lá. Agora existem dois projetos do Open Design ligados à mesma pasta — o cache do Orquestra só conhece o que ele mesmo cria, não detecta projetos pré-existentes feitos direto pela UI. Nenhum dos dois foi apagado; decisão de consolidar ou não fica pro usuário.

### 8.5 Estado dos arquivos

Nenhum commit feito (mesma decisão do item 6.3/[[feedback-exploratory-projects-document-in-md]]). Mudado nesta rodada:
- `apps/daemon/src/runtimes/defs/claude.ts` (open-design — snapshot de modelos, já registrado no item 6.3)
- `orquestra.py` (App Orquestra — comando `/design` + integração completa descrita acima)

---

## 9. `<DELEGATE binary="open-design">` — o orquestrador consegue mesmo "falar" com o Open Design (15/ago, fim da tarde/noite)

Pedido do usuário: fazer o **orquestrador** (o papel de chat fixo, hoje rodando em Gemini 3.7 Flash via `agy`) conseguir decidir sozinho, no meio de uma conversa normal, delegar uma tarefa pro Open Design — igual já faz hoje com `<DELEGATE binary="claude">`/`<DELEGATE binary="agy">` — e trazer o resultado de volta pro histórico da conversa.

### 9.1 Como funciona

Sem painel tmux dessa vez (o Open Design não é uma CLI de terminal) — usa a API de automação headless do próprio Open Design, achada e validada na conversa anterior (`od automation`, documentada como "designed so external agents... can drive the full automation lifecycle headlessly"):

1. `open_design_resolve_project` (refatorado a partir do que o `/design` já fazia) garante que existe um projeto do Open Design ligado à pasta atual.
2. `open_design_run_task(config, instrucao)`: monta um prompt que **reforça explicitamente qual é a pasta de trabalho real** (achado do item anterior — sem isso o agente tende a explorar o repo do próprio Open Design) e roda:
   - `od automation create --target reuse=<projectId> --prompt-file <tmp> --disabled` (não agenda nada, é só o veículo pra rodar uma vez)
   - `od automation run <id>` — dispara, devolve `conversationId` + `agentRunId`
   - polling em `od automation runs <id>` até `status` sair de `running` (timeout de 10min)
   - `GET /api/projects/:id/conversations/:cid/messages` — lê o texto real da última mensagem do assistente
   - `od automation delete <id>` no `finally` — limpa a automação depois (é descartável, uso único)
3. No dispatcher de `<DELEGATE>` do `orquestra.py`: novo branch pra `binary="open-design"` (aceita variações `opendesign`/`open_design`), que roda `open_design_run_task` numa thread separada (não trava a conversa), salva a resposta no histórico do orquestrador (`[Resposta do Open Design]: ...`) e notifica via `tmux display-message` quando termina — mesmo padrão dos operários de CLI.
4. Prompt de sistema do orquestrador atualizado pra ele saber que `binary="open-design"` existe e quando usar (pedidos de interface/design visual).

### 9.2 Achado importante: `linkedDirs` não é só leitura

O comentário do código do Open Design (`app-config.ts:128`) chama `linkedDirs` de "read-only `--add-dir` awareness". **Na prática, não é bem assim**: no teste real (pedido "crie uma landing page simples pro projeto arbo"), o Claude Code rodando dentro da automação **escreveu de verdade** um arquivo (`arbo/arbo-landing-page.html`, 21KB/474 linhas) direto na pasta real do projeto, não num sandbox separado — porque `--add-dir` (o mecanismo por trás de `linkedDirs`) dá acesso de ferramenta completo (leitura E escrita) ao diretório, não só leitura.

**Isso muda a percepção de segurança** que guiou o item 8 (a suposição de "ele só lê a pasta, escreve isolado, e só depois eu decido trazer pra pasta real" não se sustenta pra automações via `od automation` + `linkedDirs` — o agente pode escrever direto na pasta real desde o primeiro disparo). Vale ter isso em mente antes de expor esse `<DELEGATE binary="open-design">` pra tarefas mais sensíveis — hoje não tem nenhum "aprova antes de escrever" no meio do caminho.

### 9.3 Teste real de ponta a ponta

Rodado o `orquestra` de verdade (via tmux) na pasta `arbo`, mensagem livre digitada como usuário faria: *"peça pro Open Design criar uma landing page simples de exemplo pra esse projeto"*.

- O orquestrador (Gemini Flash) decidiu sozinho gerar `<DELEGATE binary="open-design">` com uma instrução bem elaborada (estrutura de header/hero/recursos/footer).
- A automação rodou de verdade por **~11 minutos** (tarefa pesada — não é instantâneo, o polling aguardou até completar).
- Resultado: Claude Code leu o design system real do `arbo` (`src/index.css`, screenshots reais), construiu a landing page reaproveitando a marca de verdade (paleta, fontes, screenshot do dashboard), **encontrou e corrigiu sozinho** um bug de atributo duplicado e um problema de contraste WCAG (3.71:1 → 4.99:1) no botão primário, e devolveu um relatório detalhado do que fez — tudo isso apareceu no histórico do "orquestrador" como uma mensagem normal da conversa.
- Confirmado: `arbo/arbo-landing-page.html` existe de verdade no disco, criado pela automação.

Também confirmado: a automação foi apagada sozinha depois (`od automation list` → `{"routines": []}`), sem lixo acumulando.

### 9.4-bis Descoberta em 16/08: essa integração tinha sumido do `orquestra.py`

Numa sessão seguinte (16/08), o usuário relatou que o Orquestrador tinha ficado mudo (respostas vazias) depois de pedir uma prévia "usando o open design". Investigando, dois problemas distintos apareceram:

1. **Bug real, sem relação com Open Design:** `main()` chamava `subprocess.run` sem timeout e sem checar stdout vazio — quando o `agy` no modelo `gemini-3.7-flash-high` sofreu uma instabilidade momentânea (confirmado reproduzindo na mão: `Error: timeout waiting for response`, some minutos depois voltou ao normal sozinho), o Orquestra salvava e mostrava uma resposta em branco pra sempre, sem avisar ninguém. Corrigido: timeout de 150s + detecção de saída vazia, sem salvar nada no histórico quando falha.
2. **A "prévia" que o usuário viu não veio do Open Design.** Era um link markdown (`![...](/home/max/.gemini/antigravity-cli/brain/.../*.jpg)`) — uma imagem gerada pelo próprio Antigravity (`agy`) via delegação normal `binary="agy"`, sem nenhuma relação com o Open Design. Corrigido também: o terminal/tmux não renderiza markdown nem abre imagem por link, então respostas agora passam por `_auto_open_images()`, que detecta `![]()`/caminhos de imagem locais existentes e abre no visualizador padrão (`xdg-open`) automaticamente.
3. **A causa raiz de (2):** `grep -in "design" orquestra.py` não achou **nenhuma ocorrência** — tudo que as seções 8 e 9 deste documento descrevem (`/design`, `open_design_resolve_project`, `open_design_run_task`, o branch `binary="open-design"` no dispatcher de `<DELEGATE>`) nunca chegou a ficar salvo no arquivo que roda de verdade. Não há commit, stash, nem entrada de reflog mostrando essa versão em algum momento — o trabalho foi feito e documentado aqui, mas se perdeu antes de ser persistido (provável sessão que reescreveu o arquivo a partir de uma base mais antiga). `git log --all` só mostra o commit inicial de 14/08.

### 9.5 Reimplementação + regra obrigatória de prévia (16/08)

Pedido do usuário: reimplementar a integração perdida e, dessa vez, tornar o uso do Open Design **obrigatório** — sempre que o pedido envolver qualquer coisa visual/de interface, o Orquestrador deve gerar a prévia primeiro e só liberar a implementação real depois que o usuário aprovar explicitamente. Objetivo declarado: transformar o Orquestra numa ferramenta de "gerar do zero com prévia antes de aprovar", no mesmo espírito de bolt.new/Lovable/Base44 — mas usando o design system real do projeto do usuário (o Claude sozinho gerando imagem "ficava feio"; o Open Design, lendo o projeto de verdade, conseguiu replicar o visual).

**Reimplementado em `orquestra.py`** (seção "Integração: Open Design", antes de "Utilitários" — mesmo lugar de antes): `_open_design_env`, `open_design_tools_dev_status`, `open_design_cli`, `load_open_design_cache`/`save_open_design_cache`, `open_design_resolve_project`, `open_design_run_task`, comando `/design` (+ alias `/open-design`), e o branch `binary in ("open-design","opendesign","open_design")` no dispatcher de `<DELEGATE>` (roda numa thread separada, não trava a conversa).

**Diferença desta vez — a spec da CLI foi validada linha a linha contra o código-fonte do Open Design** (não só copiada do texto da seção 8/9 anterior), porque o `od automation create` mudou desde então: agora exige `--schedule` mesmo com `--disabled` (testado, `hourly:0` funciona como placeholder inofensivo já que nunca dispara sozinho). Conferido direto em `apps/daemon/src/cli.ts`: `runProject` (`od project create/info`, escreve em `body.metadata`), `parseAutomationTarget` (`--target reuse=<id>`), `RoutineRunStatus` (`queued|running|succeeded|failed|canceled`), e a rota `GET /api/projects/:id/conversations/:cid/messages` (`apps/daemon/src/routes/project/conversations.ts:261`).

**Regra obrigatória** adicionada ao prompt de sistema do orquestrador (`main()`, junto da "REGRA DE OURO" que já existia): pra qualquer pedido com componente visual, ele não pode delegar implementação real (`claude`/`agy` escrevendo arquivo) sem antes delegar `open-design` pra prévia e esperar aprovação explícita numa mensagem seguinte. É uma regra de prompt (o modelo pode desobedecer, mesma limitação que já valia pra "não alucinar código"), não um gate de código — não dá pra garantir isso de forma determinística sem reimplementar o roteamento inteiro fora do LLM, e não foi isso que foi pedido.

**Rede de segurança nova, motivada pelo achado do item 9.2 (linkedDirs não é read-only):** `open_design_run_task` agora tira um snapshot de `git status --porcelain` na pasta do projeto antes e depois de cada tarefa. Se algum arquivo mudou (mesmo o prompt pedindo explicitamente "só prévia, não escreva nada"), a resposta que volta pro usuário vem com um aviso em destaque no topo listando os arquivos tocados, pra nunca passar despercebido que o agente escreveu na pasta real durante o que devia ser só uma prévia.

**Validação de ponta a ponta (16/08, contra o daemon real rodando via `open-design-dev`, projeto `arbo` já existente `ef0f5cd9-...`):**
- `open_design_resolve_project`: reaproveitou o projeto cacheado existente sem duplicar (confirmado com `od project info`).
- `open_design_run_task` rodado 2x via a função Python de verdade (não só a CLI na mão): perguntou "quantos `.tsx` tem em `src/pages/aluno`" → respondeu 7, batendo exatamente com `ls | wc -l`; perguntou a cor de `--bg-primary` em `src/index.css` → respondeu `#0d0d0d`/`#f4f4f5`, batendo com o arquivo real linha 5/62/128.
- Confirmado que a automação é apagada sozinha em ambos os casos (`od automation list` → `[]` depois).
- Confirmado que o aviso de "escreveu na pasta real" **não** dispara em falso: havia arquivos untracked pré-existentes na pasta (`arbo-landing-page.html`, `ideias de design/`, do teste da seção 9), e o diff antes/depois corretamente não os contou por já estarem sujos antes da tarefa rodar.

### 9.6 Estado ao final desta rodada

`open-design-dev` foi deixado rodando em background (daemon `http://127.0.0.1:36309`, web `http://127.0.0.1:40199`) pro usuário poder testar `/design` e o `<DELEGATE binary="open-design">` direto. Nenhum commit feito em `orquestra.py` nem no clone do Open Design — mesma decisão de sempre enquanto o projeto for exploratório.

### 9.4 Estado dos arquivos

Nenhum commit feito. Mudado: `orquestra.py` (App Orquestra) — `open_design_resolve_project`, `open_design_run_task`, branch novo no dispatcher de `<DELEGATE>`, prompt de sistema do orquestrador atualizado. O arquivo `arbo/arbo-landing-page.html` foi deixado no lugar (é o resultado real do teste, não lixo de sessão) — o usuário decide se mantém ou apaga.

### 9.7 Auto-start sob demanda (16/08, seguida da 9.6)

Pergunta do usuário: precisa rodar `open-design-dev` manualmente toda vez? Se sim, dá pra subir sozinho — mas isso gasta alguma coisa se ele não chegar a usar?

**Resposta medida, não estimada:** rodando só `daemon`+`web` (sem o `desktop`/Electron, que a gente nunca usa — o preview abre no navegador de verdade via `xdg-open`, e as automações rodam headless pela API), o footprint logo após subir é de **~970MB de RAM**, quase nada de CPU parado. Cold start medido do zero até `daemon`+`web` ambos `"running"`: **~8 segundos**.

Como ~1GB não é desprezível, a decisão foi **não** subir em todo arranque do `orquestra.py` (pagaria o custo até em sessões 100% de código, sem nada visual). Em vez disso: `open_design_ensure_running(config)` — chamada de dentro de `open_design_resolve_project`, ou seja, só dispara quando alguém de fato pede uma prévia (`/design` ou `<DELEGATE binary="open-design">`) — checa `tools-dev status`, e se não estiver com `daemon` E `web` ambos `"running"` com URL, sobe sozinho via `pnpm exec tools-dev start web` em background (`start_new_session=True`, sobrevive ao processo do Orquestra) e faz polling (3 em 3s, até 40s) até ficar pronto.

**Bug pego no teste:** a primeira versão só esperava o `daemon` ficar `"running"` antes de devolver — só que `tools-dev start web` sobe os dois processos, mas o `web` (esbuild/vite) demora mais que o `daemon` pra terminar de subir. Resultado: `web_url` voltava `None` na primeira chamada depois de um cold start. Corrigido esperando os dois (`daemon` e `web`) ficarem `"running"` com `url` preenchida antes de retornar.

**Validado**, do zero (`tools-dev stop` antes de cada teste):
- Chamada fria (nada rodando): subiu sozinho, achou os dois apps prontos em 7.8s, `web_url` já vindo preenchida.
- Chamada seguinte (já rodando): 0.7s, não tentou subir de novo, reaproveitou o mesmo projeto cacheado.

Depois de usado uma vez, o `daemon`+`web` ficam rodando em background até o usuário mandar parar (`tools-dev stop` dentro do clone do open-design) — não foi implementado nenhum auto-shutdown (não foi pedido, e desligar sozinho um processo que o usuário pode estar usando é mais arriscado que deixar rodando).

---

## 10. Investigação do "parou de responder" + prévia com cores erradas (17/ago)

Usuário relatou, no dia seguinte (16/08 à noite → relato feito em 17/08), dois problemas no teste real feito na pasta `arbo`: (1) o Orquestrador ficou mudo no meio da conversa, mesmo trocando de modelo, e (2) quando a prévia via Open Design finalmente saiu, as cores/formato não batiam com o projeto original.

### 10.1 Investigação: de onde veio o relato

Em vez de tentar reproduzir do zero, foi lido o histórico real salvo da conversa (`~/.orquestra_chats/arbo_16fcb306/orquestrador.json`, cache por pasta descrito no item 8.2) e comparadas as cores dos artefatos HTML gerados (`.od/projects/ef0f5cd9-.../*.html`) contra o `arbo/src/index.css` real.

### 10.2 "Parou de responder" — já corrigido, história é de uma versão anterior ao fix

O histórico mostra várias respostas do orquestrador salvas como string vazia (`"content": ""`) logo em seguida de o usuário pedir a prévia, forçando o usuário a mandar "?" repetidas vezes até uma resposta real voltar. Isso é o mesmo padrão do bug de instabilidade do `agy`/Gemini já documentado e corrigido no item 9.4-bis (timeout de 150s + não salvar nada no histórico quando a resposta vem vazia, em vez de gravar um vazio).

**Conferido contra o arquivo real (não só o que este `.md` afirma):** o `orquestra.py` que roda hoje tem esse guard — quando `clean_output` vem vazio, o código faz `continue` sem chamar `save_chat_message` (main(), por volta da linha 1328). Com esse código, não é possível persistir um `{"content": ""}` no histórico como o que apareceu ontem. Conclusão: a conversa problemática rodou numa versão do arquivo anterior ao fix (aplicado mais tarde no mesmo dia, 16/08) — não uma regressão nova. Nenhuma mudança de código foi necessária aqui; é um caso de "o fix existe e é posterior ao teste que falhou", não um bug ainda presente.

### 10.3 Cores/formato errados na prévia — causa raiz achada e corrigida

Comparação direta das cores geradas vs. reais:

| Fonte | Cores |
|---|---|
| `arbo/src/index.css` (real) | `--bg-primary: #0d0d0d`, `--bg-hero: #0d2818`, `--bg-surface: #131313` |
| 1ª tentativa do Open Design | `#10b981` / `#1e2736` / `#141b26` (paleta genérica verde-esmeralda/slate) |
| 2ª tentativa (prompt já dizia "É PROIBIDO INVENTAR CORES, leia os arquivos reais") | `#1b4332` / `#f0fdf4` / `#2d6a4f` (outra paleta genérica, verde-floresta/creme) |

Nenhuma das duas bateu com o projeto real, mesmo a segunda tentativa tendo instrução explícita e enfática para ler os arquivos reais antes de gerar.

**Causa raiz:** `.od/app-config.json` (config global do Open Design, seção 3.3) tem `"agentId": "antigravity"` como padrão — ou seja, essas prévias rodaram com o agente **Antigravity/Gemini**, não Claude Code. Isso contrasta com o único teste anterior que replicou fielmente o visual real (item 9.2-9.3): naquele caso o agente por trás da automação era o **Claude Code**, que leu `index.css` de verdade, reaproveitou a paleta real e até corrigiu sozinho um bug de contraste WCAG.

**Descoberta no código-fonte do Open Design:** `apps/daemon/src/cli.ts` — o comando `automation create` (exatamente o que `open_design_run_task` usa) aceita um flag `--agent <id>` (`if (flags.agent) body.agentId = String(flags.agent)`, por volta da linha 11252) que não estava sendo passado. Sem esse flag, a automação sempre usa o `agentId` padrão do projeto/app (antigravity).

**Fix aplicado em `orquestra.py`** (`open_design_run_task`): adicionado `--agent claude` na chamada de `automation create`, lido de `config.get("open_design", {}).get("agent", "claude")` — configurável em `~/.orquestra.json` (`open_design.agent`) se quiser trocar de volta, mas o padrão agora é Claude Code, não Gemini. `python3 -m py_compile orquestra.py` limpo depois da mudança.

**Resposta à pergunta do usuário** ("Gemini dá conta ou preciso trocar pelo Claude dentro do Open Design?"): sim, precisa — e a troca já foi automatizada no código, não requer mexer na config global do Open Design manualmente pela UI.

### 10.4 Sobre a arquitetura "clonar e mostrar a mudança numa cópia antes de aprovar"

Confirmado que isso já é essencialmente o que acontece hoje: a prévia é gerada como artefato HTML autocontido dentro do sandbox do Open Design (`.od/projects/<id>/*.html`), não editando os arquivos reais do `arbo` diretamente. A ressalva que já estava documentada (item 9.2) continua valendo e já tem mitigação: `linkedDirs` tecnicamente dá acesso de escrita à pasta real também (não é um sandbox 100% isolado como o nome sugere), mas `open_design_run_task` já compara `git status` antes/depois e avisa em destaque se algo na pasta real for tocado durante uma prévia.

### 10.5 Estado dos arquivos

Nenhum commit feito (mesma decisão de sempre para este projeto exploratório, [[feedback-exploratory-projects-document-in-md]]). Mudado: `orquestra.py` — `open_design_run_task` agora passa `--agent claude` (ou `config["open_design"]["agent"]` se definido) na criação da automação.

---

## 11. Modo chat (limpo, estilo WhatsApp) — 17/ago

Pedido do usuário depois de validar o `/design` funcionando: uma segunda forma de exibição, mais limpa, pra quem só quer ver "o que foi pedido → o que ficou pronto → link do resultado", sem a narração técnica de tool call que o modo padrão sempre mostrou. Requisito explícito: o modo técnico não podia regredir em nada.

**Implementado como toggle aditivo**, não como reescrita: `/modo chat` e `/modo tecnico`, persistido em `~/.orquestra.json` (`config["ui"]["mode"]`, padrão `"tecnico"` — comportamento de hoje sem nenhuma mudança até o usuário optar). Cada ponto de exibição no `main()` virou um `if chat_mode: <novo> else: <código de sempre>`. Detalhe técnico completo (funções, linhas, decisões) registrado em memória do projeto (`project_orquestra_modo_chat.md`), não repetido aqui.

Principais peças novas: `_print_chat_bubble` (painel Rich + Markdown), `_chat_clean_text` (colapsa blocos de código e o HTML do artefato do Open Design numa linha curta — só filtra o que é exibido, o histórico salvo continua completo), `_auto_open_links` (abre a prévia do Open Design direto no navegador em modo chat), e `quiet=True` em `_narrate_claude_line`/`_narrate_agy_line`/`_run_worker_headless` pra silenciar a narração linha-a-linha sem tocar no caminho técnico (parâmetro novo, default `False`).

**Validado duas vezes:** primeiro num teste real via tmux (pasta de teste, `<DELEGATE binary="agy">` disparado nos dois modos — chat saiu limpo com bolha final, técnico saiu idêntico ao de sempre). Depois o próprio usuário testou por conta própria, abrindo o Orquestra num segundo projeto real e usando `/modo chat` ao vivo — feedback direto: **"deu certo, ficou bem melhor... muito mais limpo e ele organiza pra trazer, ficou bom."**

Nenhum commit feito (mesma decisão de sempre). Mudado: `orquestra.py` — ver seção 5 do `project_orquestra_modo_chat.md` na memória do projeto pra lista completa de funções tocadas.
