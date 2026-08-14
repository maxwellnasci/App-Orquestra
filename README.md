# Orquestra — Documentação Completa

O **Orquestra** é uma interface de chat interativa e um roteador inteligente para agentes de IA locais. Ele foi projetado para unificar o acesso a múltiplos modelos e CLIs (como Claude Code e Antigravity) debaixo de um sistema de "Papéis" (Roles), otimizando custos e organizando o fluxo de trabalho.

## 🏗️ Arquitetura e Funcionamento

O sistema é composto por três peças principais:

1. **O Script Central (`orquestra.py`)**: Fica na pasta do projeto e contém toda a lógica de interface (usando `rich` e `prompt_toolkit`).
2. **O Launcher Global (`orquestra`)**: Um executável em `~/.local/bin/orquestra` que permite rodar a ferramenta de qualquer pasta no terminal.
3. **A Configuração de Papéis (`~/.orquestra.json`)**: O coração do sistema. É aqui que definimos os especialistas e quais IAs eles usam.

---

## 🧠 O Roteador Inteligente (Auto-Orquestrador)

Uma das maiores inovações deste projeto é o **Cérebro Roteador**. 
Quando você digita uma frase livre no terminal (exemplo: *"revisa o arquivo de login e busca bugs"*):

1. O Orquestra intercepta a sua mensagem.
2. Ele chama o **Gemini Flash** silenciosamente em background passando o seu pedido.
3. A IA analisa o contexto e decide qual é o melhor **Especialista** para a tarefa.
4. O Orquestra invoca automaticamente a CLI daquele especialista e repassa o seu pedido para ele.

Isso elimina o problema de gastar créditos caros (como Claude Opus) para tarefas simples, garantindo que o agente certo seja chamado para o trabalho certo.

---

## 🎭 Os 5 Especialistas (Papéis)

O sistema vem pré-configurado com 5 papéis fundamentais. Toda a configuração deles fica salva em `~/.orquestra.json`.

| Papel | Descrição | Modelo Padrão | CLI Base |
|---|---|---|---|
| ⚡ **Ajudante** | Dúvidas rápidas, pesquisas e explicações. | Gemini 3.6 Flash | Antigravity (`agy`) |
| 💻 **Implementador** | Codificador principal (escreve e edita arquivos). | DeepSeek v4 Pro | Claude Code (`claude`) |
| 🔎 **Fiscal** | Revisão de código e caça a bugs. | Gemini 3.1 Pro | Antigravity (`agy`) |
| 🛡️ **Segurança** | Análise de vulnerabilidades. | Gemini 3.1 Pro | Antigravity (`agy`) |
| 🧠 **Estrategista** | Arquitetura, planejamento e refatoração estrutural. | Claude Opus 4.6 (Thinking) | Antigravity (`agy`) |
| 🔬 **Analista** | Análise profunda e diagnósticos críticos. | Claude Sonnet 5 (quando disponível) | Claude Code (`claude`) |

---

## 🧠 Memória de Conversa & Resumo Automático

O Orquestra cuida da memória e da continuidade do trabalho para você:

- **Bom Dia Inteligente (Ao abrir):** O Orquestra lê as últimas conversas em background (via Gemini Flash) e abre o terminal com uma mensagem personalizada do especialista ativo dizendo exatamente onde o projeto parou e perguntando como continuar.
- **Despedida Segura (Ao sair):** Ao digitar `/sair` ou pressionar `Ctrl+D`/`Ctrl+C`, o sistema pergunta se você quer gerar um arquivo `RESUMO_DIA.md`. O assistente lê todo o histórico do dia e salva um relatório Markdown completo na pasta do seu projeto.
- **Histórico Persistente:** Salvo automaticamente em `~/.orquestra_chats/`. Use `/historico` para ver ou `/limpar-chat` para resetar.

---

## 🎛️ Menu Interativo de Troca de Agentes (`/agentes trocar`)

Você não precisa mais editar o arquivo JSON na mão para mudar os modelos de IA!

Ao digitar `/agentes trocar` (ou `/swap`), um menu navegável por **setas do teclado** é aberto em 3 passos:

1. **Escolha o Cargo:** Ajudante, Implementador, Fiscal, Segurança, Estrategista ou Analista.
2. **Escolha a Família:** Gemini / Antigravity, Claude Nativo ou Deepseek.
3. **Escolha o Modelo (Motor):** O Orquestra busca os modelos **disponíveis em tempo real** (no Antigravity e via API), trazendo os mais recentes (Gemini 3.6 Flash, Gemini 3.1 Pro, Claude Sonnet 5, Deepseek V4 Pro, etc.).

Ao confirmar, o Orquestra reconfigura o especialista, ajusta o binário correto e salva tudo em `~/.orquestra.json` automaticamente.

---

## ⚙️ Como Editar as Configurações Manuais

Se preferir editar manualmente:
1. Digite `/config` no Orquestra para abrir o `~/.orquestra.json` no editor `nano`.
2. Altere os campos e salve (`Ctrl+O`, `Enter`, `Ctrl+X`).

---

## ⌨️ Comandos Disponíveis na Interface

| Comando | O que faz |
|---|---|
| `<texto livre>` | Passa pelo **Roteador Inteligente** que escolhe o especialista automaticamente. |
| `/agente <nome>` | Força a troca para um especialista específico (ex: `/agente fiscal`). |
| `/agentes` | Lista todos os especialistas da sua equipe. |
| `/agentes trocar` | **Menu interativo:** Troca o cérebro/modelo de qualquer agente usando as setas do teclado! |
| `/config` | Abre o arquivo de configuração manual (`~/.orquestra.json`). |
| `/interativo` | Abre o agente ativo em sessão interativa nativa. |
| `/status` | Exibe qual agente está ativo, modelo e pasta do projeto. |
| `/historico` | Exibe o histórico recente de conversas com o agente ativo. |
| `/limpar-chat` | Reseta a memória do agente ativo. |
| `/bernstein <objetivo>` | Repassa a tarefa para o orquestrador autônomo. |
| `/plan <objetivo>` | Mostra o plano do Bernstein sem executar. |
| `/limpar` | Limpa a tela do terminal. |
| `/sair` | Pergunta se deseja salvar o `RESUMO_DIA.md` e fecha o Orquestra. |

---
*Criado em colaboração com Antigravity.*

