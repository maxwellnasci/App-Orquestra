# Roadmap e Ajustes — Orquestra

Este documento serve como nosso quadro de ideias e documentação viva. 
Conforme você for usando a interface `orquestra` no seu dia a dia, anote aqui o que está achando, o que incomoda e o que poderíamos melhorar. Nós vamos editando e evoluindo a ferramenta a partir daqui!

## 📝 Documentação Atual
A ferramenta funciona como um envelope ("wrapper") para 3 agentes diferentes:
- **deepseek**: Usa a interface do Claude Code, mas com a API do DeepSeek v4 Pro (não gasta seus créditos Anthropic).
- **claude**: Claude Code nativo (Anthropic).
- **agy**: Antigravity CLI oficial.


## 🎯 Backlog de Ideias e Melhorias Concluídas

- [x] **Agentes com Papéis (Roles):** Implementados 6 papéis — Ajudante, Implementador, Fiscal, Segurança, Estrategista, Analista.
- [x] **Roteador Inteligente:** O Gemini Flash analisa o pedido e delega para o especialista correto.
- [x] **Memória de Conversa & Resposta da IA:** Salva tanto os pedidos do usuário quanto a resposta gerada pela IA em `~/.orquestra_chats/`.
- [x] **"Bom Dia" Inteligente (Na Abertura):** O Ajudante analisa silenciosamente o histórico e abre a tela com um resumo de 2 linhas sobre onde o projeto parou.
- [x] **Despedida Segura (`/sair`):** Pergunta se deseja gerar o relatório executivo `RESUMO_DIA.md` na pasta do projeto ao fechar.
- [x] **Menu Interativo TUI (`/agentes trocar`):** Troca de modelos navegando pelas setas do teclado em 3 passos (Papel -> Família -> Modelo).
- [x] **Busca Dinâmica de Modelos:** Consulta a CLI do Antigravity (`agy models`) e APIs em tempo real para exibir modelos novos automaticamente.
- [x] **Interface Unificada sem Telas Pulantes:** `agy` e `claude` rodam centralizados de forma transparente com barra de progresso (spinner).

## 💡 Próximas Ideias Futuras
- [ ] *(Adicione suas ideias aqui para as próximas semanas...)*

## 🐛 Bugs Conhecidos
- *(Nenhum bug reportado até o momento)*

---
*Dica: Quando quiser que eu implemente uma ideia daqui, basta me dizer "Pegue o item X do nosso roadmap e implemente".*

