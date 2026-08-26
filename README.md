# Orquestra — O Cockpit de Agentes de IA 🚀

O **Orquestra** (V3) é uma interface de chat interativa de terminal e um roteador inteligente para agentes de IA locais. Projetado para unificar o acesso a múltiplos modelos e CLIs (como Claude Code, DeepSeek, Antigravity) debaixo de um sistema unificado, ele traz a fluidez das interfaces gráficas direto para o console raiz.

## 🏗️ O que há de novo no V3?

### O TUI Cockpit (Terminal Vitaminado)
Você não precisa sair do terminal para ser produtivo:
- **Barra de Status Inferior:** O rodapé do seu console sempre exibe o modelo ativo.
- **Teclas Rápidas:** Use `F1` (Comandos), `F2` (Trocar Combustível) e `F3` (Fila de Tarefas) direto no seu teclado, sem interromper o fluxo de digitação.
- **Contexto Mágico (`@`):** Digite `@` no prompt para ativar o autocompletar de arquivos da sua pasta atual. Ao pressionar Enter, os arquivos selecionados têm seu conteúdo injetado automaticamente como contexto para a IA, num formato que economiza tokens.

### Modo `/goal` (Estrategista e Pipelines Automáticos)
Inspirado na arquitetura do Manus AI, o Orquestra agora suporta planejamento de longo prazo autônomo.
- Digite `/goal <sua meta macro>` (ex: `/goal fazer um sistema de login`).
- O motor intercepta, vira o **Estrategista** e quebra o pedido complexo em um **Pipeline Iterativo** (uma série de tarefas atômicas encadeadas).
- O Orquestra joga tudo no banco SQLite local (`~/.orquestra_chats/memory.db`) e roda as tarefas em sequência, injetando o contexto do passo anterior no próximo.

### Human-in-the-Loop Nativo (Fast-Diff)
O agente nunca mais vai quebrar o seu código sem você ver:
- Se um agente alterar, deletar ou criar arquivos locais, a Tarefa entra no status `⚠️ Aguardando Aprovação`.
- Você confere as mudanças na tela. Se estiver feliz, digite `/aprovar [id]`. O Orquestra cria um commit no Git de forma limpa.
- Se a IA errou rude, digite `/rejeitar [id]`. O Orquestra roda um `git reset --hard` para você e volta o estado original. Zero dor de cabeça.

---

## 🧠 O Roteador Inteligente (Auto-Orquestrador)

Quando você digita uma frase livre no terminal (ex: *"revisa o arquivo de login e busca bugs"*):
1. O Orquestra intercepta a sua mensagem.
2. Ele chama um modelo ultraleve em background passando o seu pedido.
3. A IA decide qual é o melhor **Especialista** para a tarefa.
4. O Orquestra invoca o operário e delega o trabalho (em `PIPELINE` ou múltiplos operários em `DELEGATE` paralelos!).

Isso elimina o problema de gastar créditos caros para tarefas simples, garantindo que o agente certo seja chamado para o trabalho certo.

---

## 🎭 Os Especialistas Padrões

Toda a configuração fica salva em `~/.orquestra.json`.

| Papel | Descrição | Modelo Padrão | CLI Base |
|---|---|---|---|
| ⚡ **Ajudante** | Dúvidas rápidas, pesquisas e explicações. | Gemini 3.6 Flash | Antigravity (`agy`) |
| 💻 **Implementador** | Codificador principal (escreve e edita arquivos). | DeepSeek v4 Pro | Claude Code (`claude`) |
| 🔎 **Fiscal** | Revisão de código e caça a bugs. | Gemini 3.1 Pro | Antigravity (`agy`) |
| 🛡️ **Segurança** | Análise de vulnerabilidades. | Gemini 3.1 Pro | Antigravity (`agy`) |
| 🧠 **Estrategista** | Arquitetura, planejamento e modo `/goal`. | Claude Opus 4.6 | Antigravity (`agy`) |
| 🔬 **Analista** | Análise profunda e diagnósticos críticos. | Claude Sonnet 5 | Claude Code (`claude`) |

---

## 🎛️ Comandos Disponíveis na Interface

| Comando | O que faz |
|---|---|
| `<texto livre>` | Roteador Inteligente: escolhe o especialista automaticamente. |
| `@arquivo` | Autocompleta nomes de arquivo para injeção automática de contexto. |
| `/goal <texto>` | Inicia o Estrategista para quebrar a meta em múltiplas Tasks e executá-las no Pipeline. |
| `/tarefas` | (Ou `F3`) Mostra a Fila de Tarefas (Histórico do SQLite) na tela. |
| `/aprovar [id]` | Aceita o diff gerado por uma task pendente (salva no Git). |
| `/rejeitar [id]` | Recusa as alterações e limpa (reset) a árvore de trabalho. |
| `/agentes trocar`| (Ou `F2`) Menu interativo TUI para trocar o modelo/motor de qualquer agente em tempo real. |
| `/agente <nome>` | Força a troca para um especialista específico para o chat livre. |
| `/status` | Exibe qual agente está ativo. |
| `/historico` | Exibe o histórico recente de conversas. |
| `/sair` | Fecha o Orquestra com a opção de criar o `RESUMO_DIA.md`. |

---
*Construído com Python, prompt_toolkit e sqlite3.*
