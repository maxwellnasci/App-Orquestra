# Orquestra — seu cockpit de agentes de IA no terminal 🎼

O **Orquestra** é uma TUI (interface de terminal, estilo Cursor/VS Code) onde você
conversa com um **Orquestrador estrategista** que planeja o trabalho e coordena uma
equipe de agentes. Você não decora flags de CLI nem troca de ferramenta na mão — fala
o que quer e a equipe executa, com você aprovando cada mudança.

## Os 3 agentes + o Cão de Guarda

| Agente | Papel | O que faz | Motor padrão |
|---|---|---|---|
| 🎼 **Orquestrador** | Estrategista | Conversa, pensa a solução como um pair, faz o `<PLANO>` e delega. **Nunca escreve nem roda comando que altere nada** — implementar é sempre do Operador. | Codex / Claude / Antigravity em **modo plano** (lê o projeto pra raciocinar) — ou um cérebro de chat puro (DeepSeek/Kimi, zero ferramentas) |
| 🔨 **Operador** | Executor | Lê o código, escreve, altera, roda comandos, cria arquivos. | Claude Code (`claude-sonnet-5`) |
| 🔍 **Revisor** | Revisão sob demanda | Caça bugs, avalia arquitetura e segurança de um trecho ou de um diff. Você aciona pelo botão "🔍 Revisar". | Claude Code (`claude-opus-5`) |
| 🐕 **Cão de Guarda** | Automático e silencioso | Depois de cada edição, lê o codebase ao redor do diff (bugs, lógica, segurança), reporta no rodapé e no chat do Orquestrador. Verde se está tudo certo, vermelho só se achar algo. | DeepSeek v4 Pro (read-only) |

Troque o motor de qualquer um pelos botões do topo da TUI (`Orq:` / `Op:` / `Rev:`) —
inclusive por modelos chineses via OpenRouter (DeepSeek, Qwen, GLM, Kimi, MiniMax),
usando a CLI `opencode` já logada na máquina.

## A tela: Palco + Painel lateral + Chat

```
┌ 🎼 ORQUESTRA  [📁 Arquivos] [📊 Logs] [💻 Shell] [🎓 Aprendizado]  Orq:|Op:|Rev: ┐
├──────────┬───────────────────────────────────────┬──────────────────────────┤
│ 📁       │ ⚡ Atividade │ ± Mudanças │ 📝 Editor …  │ 📊 Logs │ 🖥 Shell     [✕] │
│ Arquivos │───────────────────────────────────────│──────────────────────────│
│ (toggle) │ narração ao vivo de cada agente,      │ ┌──(max㉿kali)-[projeto]  │
│          │ com timestamp e cor                   │ └─$ claude login          │
├──────────┴───────────────────────────────────────┴──────────────────────────┤
│ 💬 Orquestrador                    ⠹ 🔨 Operador editando... 12s            │  ← chat
│ conversa limpa — sem narração crua de worker                                │
│ [✅ Aprovar] [❌ Rejeitar] [🔍 Revisar]                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

No meio, um **palco** com 4 abas: **⚡ Atividade** (o que a equipe está
fazendo agora — prompt completo, texto ao vivo, tool calls), **± Mudanças**
(diff colorido do que ainda não foi aprovado), **📝 Editor**, **🎨 Idea
Studio**. Embaixo, um **chat** fino de largura cheia — só conversa, nunca a
narração crua de um worker. O palco pula sozinho pra Atividade quando uma
tarefa começa (`/seguir off` desliga isso); se você trocar de aba na mão
enquanto algo roda, ele respeita e avisa quando terminar.

À direita fica o **painel lateral** (escondido até você abrir), com duas abas:
**📊 Logs** (monitor dos agentes — abre sozinho quando um agente começa) e
**🖥 Shell** — um **terminal de verdade dentro do Orquestra**, na pasta do projeto e
com as chaves do cofre já no ambiente. Serve pra o que só você faz: `claude login`,
`export CHAVE=...`, abrir um agente qualquer na pasta, rodar `git`/`vim`/`less` —
sem sair da tela e vendo a Atividade e o chat ao mesmo tempo.

- `Ctrl+E` (ou o botão `💻 Shell`) abre o painel já no shell, com o foco nele.
- Com o foco no shell, **todas as teclas vão pra ele** (inclusive os atalhos do app);
  `Ctrl+]` devolve o foco ao chat.
- `Ctrl+T`, o botão `📊 Logs` ou o **✕** do painel fecham — só escondem; o shell
  continua vivo e volta igual (com o histórico) ao reabrir.
- `Ctrl+O` abre o **shell externo** (a TUI é suspensa e você cai num bash nativo,
  `exit` volta). É o mais fiel: use pra copiar/colar, rolar o histórico ou apps de
  tela cheia exigentes.
- **Limites do painel:** sem scrollback, sem mouse e sem seleção/cópia; é um emulador
  em Python (`pyte`), não idêntico ao seu terminal em apps de tela cheia mais exóticos.

## 🎓 Modo Aprendizado

Um segundo jeito de trabalhar: em vez de delegar tudo pro Operador, você mesmo
escreve o código no painel Editor e o Revisor entra como mentor.

- Liga no botão **🎓 Aprendizado** no topo.
- `Ctrl+S` salva o arquivo **e** dispara uma revisão automática em background
  (cooldown de 20s), 100% em modo leitura — o Revisor comenta, nunca escreve.
- Botão **🔍 Revisão profunda** (aparece no painel Editor com o modo ligado)
  pede uma revisão completa do arquivo aberto, sob demanda.
- Quando o Revisor aponta uma correção objetiva, ela vira um **cartão** no
  Editor com **✅ Aceitar** (aplica de verdade no arquivo) / **❌ Recusar**
  — por trecho, não por diff inteiro.

## 📝 Editor

Aba do palco (não painel lateral mais) com abas de verdade dentro dela — um
arquivo por aba, sintaxe colorida por extensão. `Ctrl+S` salva no disco,
`Ctrl+W` fecha a aba ativa. Abre sozinho quando o Operador toca um arquivo
(GPS, sem puxar o palco pra lá enquanto uma tarefa roda) ou quando você
clica na árvore ou o próprio Orquestrador mostra código durante a conversa
(`<MOSTRAR arquivo="...">`).

## Como funciona um turno

1. Você pede algo. Dúvida conceitual ou conversa de análise ("o que tá rolando
   no meu servidor?", "vale a pena isso aqui?") → o Orquestrador **lê o código
   relevante** (ele roda em modo plano/read-only) e responde como um pair: a
   leitura dele, as opções, os trade-offs, a recomendação. Sem `<PLANO>`.
2. Tarefa que mexe em código → ele escreve um `<PLANO>` e **para**. Você clica
   **▶ Executar plano** (ou **✏️ Ajustar**). Se ele tentar delegar **sem** um
   plano aprovado, aparece uma barra **▶ Rodar agora / ✋ Cancelar** com o que
   vai rodar — o operário tem permissão total, então delegação "por fora" pede
   1 confirmação (`/auto on` desliga isso num projeto seu de confiança).
3. O Operador executa — acompanhe em **⚡ Atividade**. Os arquivos tocados
   abrem no Editor em tempo real (GPS).
   - **Gate de verificação:** quando ele termina, o Orquestra roda o teste/build
     do projeto sozinho (`pytest`/`npm test`/`tsc`/`cargo`/`go`, ou o comando do
     arquivo `.orquestra_gate`). Se quebrar, o log volta pro **mesmo Operador**
     consertar (até 2 tentativas) antes de você ver. Projeto sem teste detectável
     = não faz nada. Desliga com `"gate": {"enabled": false}` no config.
4. Ele termina → o Orquestrador comenta o resultado no chat (`/relato off`
   desliga isso e volta a mostrar a resposta crua do Operador). Se mexeu em
   arquivo → barra **✅ Aprovar / ❌ Rejeitar / 🔍 Revisar**, com o diff
   colorido em **± Mudanças**.
   - **Aprovar** → commit limpo no Git + o Cão de Guarda roda (DeepSeek lê o
     codebase ao redor do diff; o achado vai pro rodapé e pro chat do Orquestrador).
   - **Rejeitar** → reverte **só os arquivos daquela tarefa** — edição manual
     sua e o trabalho de outra tarefa pendente ficam intactos.
   - **Revisar** → o Revisor analisa o diff.

## Instalação

```bash
pip install rich prompt_toolkit cryptography textual textual-image rich-pixels pyte ptyprocess --break-system-packages
# deixar o comando `orquestra` disponível em qualquer pasta:
ln -sf "$PWD/orquestra" ~/.local/bin/orquestra
```

Ferramentas externas (no PATH): `claude`, `agy`, `codex`, `gh` (com `gh copilot`),
`opencode` (opcional — motor OpenRouter/LLMs chinesas, já logado na conta),
e `chromium`/`google-chrome` (renderiza a prévia do Open Design dentro da TUI).
Cérebro do Orquestrador **e o Cão de Guarda**: `~/.claude-deepseek-env` (chave
DeepSeek, com saldo). O Orquestrador aceita também o `kimi` CLI (Ollama Cloud).
Open Design: seção `open_design` no `~/.orquestra.json` (auto-semeada se o clone
existir).

**Prévia do Open Design dentro da TUI:** a imagem é renderizada na aba 🎨 Idea Studio.
A nitidez depende do terminal — num terminal com protocolo de imagem (Kitty, WezTerm,
Ghostty, Konsole, ou o terminal integrado do Cursor/VS Code com imagens ligadas) é uma
imagem de verdade; num terminal simples (qterminal) vira blocos coloridos. Em qualquer
caso, uma cópia `.orquestra_preview.png` fica na pasta do projeto.

## Uso

```bash
cd /caminho/do/seu/projeto
orquestra          # abre a TUI, a pasta atual vira o projeto
orquestra --repl   # REPL antigo (headless/debug)
```

Na primeira vez em cada projeto, o **cofre** (`AES/Fernet`, por pasta) pede uma senha
mestra e injeta suas credenciais como variáveis de ambiente da sessão. Comandos:
`/senha NOME valor`, `/senhas`, `/senha-remove NOME`.

## Comandos na TUI

| Comando | O que faz |
|---|---|
| `<texto livre>` | Fala com o Orquestrador. |
| `/detalhe minimal` \| `normal` \| `full` | Quanto do trabalho ao vivo aparece em ⚡ Atividade (o `/modo chat`\|`tecnico` do README antigo é só do REPL `--repl`, não afeta a TUI). |
| `/relato on` \| `off` | O Orquestrador comenta o resultado do Operador/pipeline no chat (liga por padrão). |
| `/seguir on` \| `off` | O palco pula sozinho pra ⚡ Atividade a cada tarefa nova (liga por padrão). |
| `/risco on` \| `off` | Gate por risco — pula a pausa de Aprovar se nada bater em padrão sensível. |
| `/auto on` \| `off` | `on` = delegação avulsa (fora de um `<PLANO>` aprovado) roda sem pedir confirmação. Nasce `off`. |
| `/quem <arquivo>` | Quem foi o último agente a editar aquele arquivo (autoria por IA). |
| `/uso` \| `/saldo` | Gasto/tokens da sessão · saldo real da DeepSeek. |
| Botões do topo | Trocam o modelo do Orquestrador / Operador / Revisor, ou ligam o `🎓 Aprendizado`. |

### Atalhos de teclado

| Atalho | O que faz |
|---|---|
| `Ctrl+1` … `Ctrl+4` | Abre a aba do palco: Atividade, Mudanças, Editor, Idea Studio. |
| `Ctrl+B` | Mostra/oculta o painel Arquivos. |
| `Ctrl+E` | Abre o painel lateral direto no 🖥 Shell (funciona mesmo com o foco no chat). |
| `Ctrl+]` | Sai do shell embutido e volta o foco pro chat (dentro dele, tudo vai pro shell). |
| `Ctrl+T` | Abre/fecha o painel lateral (o shell embutido continua vivo). |
| `Ctrl+O` | Shell **externo**: suspende a TUI e abre um bash nativo (`exit` volta). |
| `Ctrl+↑` / `Ctrl+↓` | Tamanho do chat: compact ↔ normal ↔ tall (fica salvo). |
| `Ctrl+K` | Limpa a aba Atividade (não mexe no chat nem no histórico). |
| `Ctrl+L` | Foco direto no campo de texto do chat. |
| `Ctrl+N` | Cria um arquivo novo no Editor (nasce no disco no primeiro `Ctrl+S`). |
| `Ctrl+S` / `Ctrl+W` | Salva / fecha a aba ativa do Editor. |

## Arquivos

| Arquivo | Papel |
|---|---|
| `orquestra` | Launcher (abre a TUI; `--repl` cai no REPL). |
| `orquestra_tui.py` | A interface (Textual). |
| `orquestra_term.py` | `PtyTerminal`: o shell real embutido no painel lateral (pty + pyte). |
| `orquestra.py` | Backend: config, memória, cofre, execução dos operários, Cão de Guarda. |
| `orchestrator_brain.py` | O cérebro do Orquestrador — chat puro, zero ferramentas. |
| `vault.py` | Cofre de credenciais por projeto. |
| `AGENTES.md` | Documentação de arquitetura para contribuir. |
| `NOTAS_TUI.md` | Armadilhas de layout da TUI + como o terminal real foi feito. |
| `APRENDIZADOS.md` | Erros e acertos de cada sessão (a §12 é a do terminal real). |
| `test_orquestra.py` · `test_orquestra_term.py` | Testes sem rede (`python3 <arquivo>`). |
| `PLANO_PALCO_CHAT.md` | Plano do layout Palco + Chat (histórico, 6 etapas, todas feitas). |
| `~/.orquestra.json` | Configuração (roles, workers, ui). |
| `~/.orquestra_chats/memory.db` | Fila de tarefas + memória de lições (SQLite). |

---
*Python · Textual · prompt_toolkit · sqlite3 · cryptography*
