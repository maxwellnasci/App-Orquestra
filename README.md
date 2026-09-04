# Orquestra — seu cockpit de agentes de IA no terminal 🎼

O **Orquestra** é uma TUI (interface de terminal, estilo Cursor/VS Code) onde você
conversa com um **Orquestrador estrategista** que planeja o trabalho e coordena uma
equipe de agentes. Você não decora flags de CLI nem troca de ferramenta na mão — fala
o que quer e a equipe executa, com você aprovando cada mudança.

## Os 3 agentes + o Cão de Guarda

| Agente | Papel | O que faz | Motor padrão |
|---|---|---|---|
| 🎼 **Orquestrador** | Estrategista | Conversa, pensa a solução, faz o `<PLANO>` e delega. **Não tem ferramentas** — nunca lê/escreve/roda nada sozinho. | DeepSeek v4 Flash (chat direto) |
| 🔨 **Operador** | Executor | Lê o código, escreve, altera, roda comandos, cria arquivos. | Claude Code (`claude-sonnet-5`) |
| 🔍 **Revisor** | Revisão sob demanda | Caça bugs, avalia arquitetura e segurança de um trecho ou de um diff. Você aciona pelo botão "🔍 Revisar". | Claude Code (`claude-opus-5`) |
| 🐕 **Cão de Guarda** | Automático e silencioso | Depois de cada edição, audita o `git diff` no rodapé. Fica verde se está tudo certo, vermelho só se achar bug. | GitHub Copilot |

Troque o motor de qualquer um pelos botões do topo da TUI (`Orq:` / `Op:` / `Rev:`) —
inclusive por modelos chineses via OpenRouter (DeepSeek, Qwen, GLM, Kimi, MiniMax),
usando a CLI `opencode` já logada na máquina.

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

Painel com abas de verdade (um arquivo por aba, sintaxe colorida por
extensão). `Ctrl+S` salva no disco, `Ctrl+W` fecha a aba ativa. Abre sozinho
quando o Operador toca um arquivo (GPS) ou quando o próprio Orquestrador
decide mostrar código real durante a conversa (`<MOSTRAR arquivo="...">`).

## Como funciona um turno

1. Você pede algo. Dúvida conceitual → o Orquestrador responde na hora.
2. Tarefa que mexe em código → ele escreve um `<PLANO>` e **para**. Você clica
   **▶ Executar plano** (ou **✏️ Ajustar**).
3. O Operador executa. Os arquivos tocados abrem no painel Editor em tempo real (GPS).
4. Ele terminou e mexeu em arquivos → barra **✅ Aprovar / ❌ Rejeitar / 🔍 Revisar**.
   - **Aprovar** → commit limpo no Git + o Cão de Guarda roda.
   - **Rejeitar** → `git reset` só dos arquivos daquela tarefa.
   - **Revisar** → o Revisor analisa o diff.

## Instalação

```bash
pip install rich prompt_toolkit cryptography textual textual-image rich-pixels --break-system-packages
# deixar o comando `orquestra` disponível em qualquer pasta:
ln -sf "$PWD/orquestra" ~/.local/bin/orquestra
```

Ferramentas externas (no PATH): `claude`, `agy`, `codex`, `gh` (com `gh copilot`),
`opencode` (opcional — motor OpenRouter/LLMs chinesas, já logado na conta),
e `chromium`/`google-chrome` (renderiza a prévia do Open Design dentro da TUI).
Cérebro do Orquestrador: `~/.claude-deepseek-env` (chave DeepSeek) ou o `kimi` CLI
(Ollama Cloud). Open Design: seção `open_design` no `~/.orquestra.json` (auto-semeada
se o clone existir).

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
| `/modo chat` \| `/modo tecnico` | Bolhas limpas × narração crua de tool calls. |
| `/detalhe minimal` \| `normal` \| `full` | Quanto do trabalho ao vivo aparece. |
| Botões do topo | Trocam o modelo do Orquestrador / Operador / Revisor, ou ligam o `🎓 Aprendizado`. |
| `Ctrl+S` / `Ctrl+W` | Salva / fecha a aba ativa do Editor. |

## Arquivos

| Arquivo | Papel |
|---|---|
| `orquestra` | Launcher (abre a TUI; `--repl` cai no REPL). |
| `orquestra_tui.py` | A interface (Textual). |
| `orquestra.py` | Backend: config, memória, cofre, execução dos operários, Cão de Guarda. |
| `orchestrator_brain.py` | O cérebro do Orquestrador — chat puro, zero ferramentas. |
| `vault.py` | Cofre de credenciais por projeto. |
| `AGENTES.md` | Documentação de arquitetura para contribuir. |
| `~/.orquestra.json` | Configuração (roles, workers, ui). |
| `~/.orquestra_chats/memory.db` | Fila de tarefas + memória de lições (SQLite). |

---
*Python · Textual · prompt_toolkit · sqlite3 · cryptography*
