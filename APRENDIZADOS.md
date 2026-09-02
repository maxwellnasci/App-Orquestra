# Aprendizados — Refatoração de 3 agentes (set/2026)

Registro do que descobrimos ao consolidar o Orquestra na TUI e religar as
conexões. Foco no que **não é óbvio** e no que quebrou. Para a arquitetura em si,
ver `AGENTES.md`; para uso, `README.md`.

---

## 1. Como cada agente CONECTA (a parte que confundiu)

Há **duas formas** de um modelo conectar no Orquestra, e elas não são
intercambiáveis:

### a) Conta logada no terminal (OAuth da CLI) — sem API key

`agy`, `claude` e `codex` já vêm autenticados na máquina (`agy`, `claude`,
`gh copilot`, `codex` no PATH). O Orquestra chama a CLI e ela usa esse login.

| Motor | binary | Usado como |
|---|---|---|
| Antigravity (Gemini) | `agy` | Operador, ou Orquestrador (`--mode plan`) |
| Claude Code | `claude` | Operador/Revisor, ou Orquestrador (`--permission-mode plan`) |
| Codex (OpenAI) | `codex` | Operador, ou Orquestrador (`codex exec --sandbox read-only`) |
| GitHub Copilot | `copilot` (`gh copilot`) | Cão de Guarda |

### b) API key (arquivo ou cofre) — NÃO tem login de CLI

| Motor | Como | Onde |
|---|---|---|
| **DeepSeek** | API key `sk-...` | `~/.claude-deepseek-env` → `ANTHROPIC_API_KEY` (ou `/senha DEEPSEEK_API_KEY`) |
| **Ollama Cloud** (Kimi, gpt-oss, nemotron...) | key do `kimi` CLI | `kimi provider list --json` → `.providers.ollama-cloud.apiKey` |
| Anthropic / OpenAI / Gemini "puros" | API key própria | `/senha ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` |

**"claude-deepseek" é um truque:** roda a CLI do Claude Code (`claude`) com
`ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic` + a API key do DeepSeek.
O DeepSeek fez a API deles compatível com o formato Anthropic, então o `claude`
funciona apontado pra lá. **Não existe "CLI do DeepSeek"** neste projeto.

**Gemini API key ≠ login do `agy`.** A chave do Google AI Studio é uma coisa; o
login do Antigravity CLI é outra. Se você escolher um modelo Gemini pro
Orquestrador, ele vai pelo `agy` (login), não pela API key.

---

## 2. Como o ORQUESTRADOR roda

Sempre em **modo plano / read-only**: pode LER pra dar contexto melhor, mas nunca
escreve arquivo nem roda comando que altera nada. Implementar é sempre delegado.

`_orch_route(modelo)` decide o motor:

```
gemini-*        → agy --mode plan                    (login Antigravity)
claude-*        → claude --permission-mode plan      (login Claude Code)
gpt-* (Codex)   → codex exec --sandbox read-only     (login Codex)
deepseek-v4-*   → brain / provider=deepseek          (API key do arquivo)
kimi/gpt-oss/…  → brain / provider=ollama-cloud      (key do kimi CLI)
```

`binary="brain"` = `orchestrator_brain.py`, chat completions puro (zero
ferramentas por construção). Os outros usam a CLI em modo restrito.

**Por que não deixar o Orquestrador com ferramentas totais:** o `agy`/`claude`
executam comando mesmo pedindo pra não executar (`--disallowedTools` não
segura, spike confirmou). Só o modo **plan** trava a escrita de verdade. E o
`brain` é 100% incapaz de tocar em arquivo.

---

## 3. Bugs encontrados e corrigidos

### 3.1 O `agy` não enxergava a pasta do projeto  *(commit 19902c7)*

`_build_worker_cmd` chamava `agy -p ... --project <hash> --add-dir .`

- `--project <hash>` → o `agy` operava no **workspace-brain dele**
  (`~/.gemini/antigravity-cli/brain/<id>`), não na pasta real. `pwd` dava
  `/home/max/.gemini/...`, `cat arquivo` falhava.
- `--add-dir .` → o `.` era resolvido relativo ao scratch do `agy`, não ao cwd
  do processo.

**Fix:** removido `--project`; `--add-dir` agora é **caminho absoluto**
(`get_project_dir()`). Testado: `agy` lê os arquivos reais e roda comandos na
pasta certa.

> Regra: pro `agy`, `--add-dir` **sempre absoluto**, e **nunca** `--project`
> quando a intenção é trabalhar numa pasta externa.

### 3.2 Orquestrador só funcionava com API key  *(commit 14686b0)*

O refactor tinha trocado o Orquestrador pra `brain` (chat API). Gemini/Claude/GPT
por chat API precisam de API key própria — que o usuário **não tem**. Só DeepSeek
e Ollama funcionavam. As contas `agy`/`claude`/`codex` logadas ficaram sem uso
pelo Orquestrador.

Pior: o picker deixou salvar `{"binary":"brain","provider":"copilot"}` — o
`orchestrator_brain.py` não fala "copilot" → **erro em toda mensagem** =
"nada funciona".

**Fix:** Orquestrador volta a usar as CLIs logadas, em modo plano (ver §2).
`load_config()` conserta `provider` inválido re-roteando pelo nome do modelo.

### 3.3 Modelo de chat escolhido pra Operador/Revisor  *(commit 19902c7)*

O picker deixava escolher `kimi-k3` / `gpt-oss:120b` pro Operador. Esses não têm
CLI de agente → `_build_worker_cmd` rodava `claude --model kimi-...` → o `claude`
rejeitava o modelo → operário "não funcionava" **sem erro claro**.

**Fix:** picker do Operador/Revisor **não mostra mais Ollama Cloud**. Se um
modelo de chat puro for escolhido, avisa e não troca. `_build_worker_cmd` só
passa `--model` pro `claude` se for `claude-*`/`deepseek-*`.

### 3.4 O Cão de Guarda era código morto  *(commit aaf42d4)*

`_run_copilot_watchdog()` definia a thread interna e **nunca a disparava**. A
auditoria automática do `git diff` nunca rodou. **Fix:** dispara a thread de
verdade, 1x por turno (cooldown de 15s), com o `git diff` já pronto no prompt.

### 3.5 Texto alucinado depois de `<DELEGATE>`  *(commit 6b129a1)*

O cérebro às vezes escreve um "resultado" falso depois da tag `<DELEGATE>`.
**Fix:** só mostra o texto que vem **antes da 1ª tag** (`<PLANO>`/`<PIPELINE>`/
`<DELEGATE>`).

### 3.6 O `orquestra` abria o Cursor no boot  *(commit bdbaee0)*

`_startup()` da TUI abria `.orquestra_logs.md` no Cursor e subia um bridge HTTP —
sobras do REPL antigo. A TUI tem painel de log/editor próprios. **Fix:** removido.
`orquestra` abre **só a TUI**.

### 3.7 `__pycache__` / `*preview*.html` na aprovação  *(commits 6b129a1, 8a2ba1d)*

Artefatos que o operário/Open Design geram de passagem apareciam como "arquivos
alterados". **Fix:** `_is_noise_path()` filtra `__pycache__`, `*.pyc`,
`.orquestra_preview.*`, `*preview*.html`, `.pytest_cache`.

---

## 4. Open Design

- **Nunca tinha sido ligado.** A seção `open_design` não estava no
  `~/.orquestra.json` (nem no backup pré-refactor). As validações antigas foram
  feitas chamando as funções Python direto num teste, não pelo fluxo normal.
- **Agora:** seção `open_design` no `DEFAULT_CONFIG` (auto-semeada se o clone
  existir em `~/Documentos/Kali Linux/open-design`), `<DELEGATE binary="open-design">`
  e `/design` funcionando na TUI.
- **Agente que o Open Design usa:** **Claude Code** (`--agent claude`), forçado
  pelo Orquestra. O default de fábrica é `antigravity`/Gemini, que inventa
  paletas genéricas em vez de ler o design system real (lição de 08/2026).
- **`linkedDirs` NÃO é read-only** — o Open Design às vezes escreve nos arquivos
  reais mesmo em "prévia". O Orquestra tira snapshot `git` antes/depois e avisa;
  na TUI cai no gate Aprovar/Rejeitar.
- **Prévia renderizada dentro do Orquestra:** o Open Design salva um
  `.orquestra_preview.html` autossuficiente → `chromium --headless --screenshot`
  → imagem na aba Idea Studio via `textual-image`. Fidelidade depende do terminal
  (protocolo de imagem no Kitty/WezTerm/Ghostty/Konsole/terminal do Cursor;
  half-blocks no qterminal). Cópia em `.orquestra_preview.png`.
- **Daemon:** sobe sozinho no 1º `/design` da sessão (~15s, ~970MB RAM), fica de
  pé. Parar: `pnpm tools-dev stop` na pasta do clone (com o Node oficial no PATH:
  `~/.local/opt/node-open-design/bin`).

---

## 5. Gotchas para lembrar

- **`agy --add-dir` sempre absoluto**, nunca `--project` pra pasta externa.
- **`agy --mode plan`** e **`claude --permission-mode plan`** = read-only de
  verdade (testado: não escrevem). `--disallowedTools` NÃO segura.
- **`codex exec`** precisa de `-o <arquivo>` pra pegar a resposta final de forma
  confiável (o `--json` dele é best-effort).
- **`gh copilot -p`** devolve texto puro + rodapé de métricas ("AI Credits",
  "Tokens"...). O parser filtra esses prefixos.
- **Ollama Cloud não roda como agente** — só como cérebro de chat (Orquestrador).
- **DeepSeek depende do arquivo `~/.claude-deepseek-env`** — se a chave expirar,
  trocar lá ou via `/senha DEEPSEEK_API_KEY`.
- **`_build_orchestrator_cmd` retorna 3-tupla** `(cmd, binary, output_file)` —
  todo caller precisa desempacotar os 3.
- **TUI e REPL compartilham** `orquestra.py` — mudou assinatura de
  `_run_worker_headless`/`_resolve_worker`/`_build_*`, atualiza os dois.
- **`save_screenshot()` da Textual não captura `textual-image`** — o teste
  headless mostra a imagem como pontos; num terminal de verdade é imagem real.

---

## 6. Backups / rollback

- Config antiga: `~/.orquestra.json.bak-pre-refactor`
- Restaurar: `cp ~/.orquestra.json.bak-pre-refactor ~/.orquestra.json`
- REPL antigo: `orquestra --repl`
- Código aposentado: `_archive/` (gitignored) — `llm_orchestrator.py`, roles v1

---

## 7. Ordem dos commits (set-out/2026)

| Commit | O quê |
|---|---|
| `aaf42d4` | Arquitetura de 3 agentes + Cão de Guarda, TUI oficial |
| `29126ec` | Open Design religado e ligado na TUI |
| `6b129a1` | Prévia do Open Design renderizada dentro do Orquestra |
| `bdbaee0` | Não abrir o Cursor no boot |
| `8a2ba1d` | Orquestrador aceita todas as empresas + `/senha` na TUI |
| `19902c7` | `agy` não enxergava a pasta + validação de modelo dos operários |
| `14686b0` | Orquestrador usa as contas LOGADAS no terminal (agy/claude/codex) |
