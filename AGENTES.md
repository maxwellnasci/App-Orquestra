# Orquestra — Documentação para Agentes de IA

> **Leia este arquivo antes de qualquer coisa.** Ele descreve a arquitetura completa,
> os papéis de cada agente, como o sistema funciona e como contribuir com segurança.

---

## O que é o Orquestra

O Orquestra é uma **interface de terminal inteligente multi-agente** que transforma o
fluxo de trabalho de desenvolvimento. Em vez de usar um único modelo de IA, o Orquestra
coordena um time de agentes especializados — cada um com sua ferramenta e modelo ideal —
tudo através de uma conversa natural no terminal.

**Filosofia central:** Uma pessoa comum deve conseguir ter um fluxo de trabalho de nível
profissional, com segurança, qualidade de código e múltiplas perspectivas de IA, sem
precisar conhecer os detalhes técnicos de cada ferramenta.

---

## Arquitetura

```
Usuário (terminal)
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│  orquestra.py — REPL principal                          │
│  • Interpreta comandos e input do usuário               │
│  • Gerencia histórico de conversa                       │
│  • Intercepta comandos de segurança (/senha)            │
│  • Exibe respostas com Rich (UI rica no terminal)       │
└────────────────────┬────────────────────────────────────┘
                     │
          ┌──────────┴───────────┐
          ▼                      ▼
┌──────────────────┐   ┌────────────────────────────────┐
│  vault.py        │   │  Orquestrador (Gemini Flash)   │
│  Cofre AES-256   │   │  Decide o que fazer e          │
│  de credenciais  │   │  emite tags DELEGATE/PIPELINE  │
└──────────────────┘   └──────────────┬─────────────────┘
                                      │
              ┌───────────────────────┼────────────────┐
              ▼                       ▼                 ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │  implementador   │  │  pesquisador     │  │  arquiteto       │
   │  DeepSeek V4 Pro │  │  Copilot (auto)  │  │  DeepSeek V4 Pro │
   │  Escrita de      │  │  Pesquisa + Cão  │  │  Revisão e       │
   │  código pesado   │  │  de Guarda       │  │  arquitetura     │
   └──────────────────┘  └──────────────────┘  └──────────────────┘
```

### Tags de Delegação

O Orquestrador coordena os agentes usando duas tags XML embutidas na resposta:

**`<DELEGATE binary="nome">`** — Roda um agente de forma independente (paralelo):
```
<DELEGATE binary="implementador">
Crie uma função Python que lê um CSV e retorna um DataFrame.
</DELEGATE>
```

**`<PIPELINE>`** — Roda agentes em cadeia (saída de um vira entrada do próximo):
```
<PIPELINE>
<DELEGATE binary="pesquisador">Leia o arquivo auth.py e documente o que ele faz</DELEGATE>
<DELEGATE binary="arquiteto">Revise a segurança da lógica documentada</DELEGATE>
<DELEGATE binary="implementador">Aplique as correções sugeridas pelo revisor</DELEGATE>
</PIPELINE>
```

---

## Agentes Disponíveis

### Roles (acesso direto via `/agentes NOME`)

| Role | Modelo | Especialidade |
|------|--------|--------------|
| `orquestrador` | Gemini 3.7 Flash | Coordenação, conversa, memória |
| `implementador` | DeepSeek V4 Pro | Codificação pesada e refatoração |
| `pesquisador` | Copilot (auto) | Pesquisa rápida e varredura |
| `arquiteto` | DeepSeek V4 Pro | Revisão, segurança, arquitetura |
| `analista` | Claude Sonnet | Análise profunda e troubleshooting |
| `fiscal` | Gemini 3.1 Pro | QA e busca de bugs |
| `segurança` | Gemini 3.1 Pro | Auditoria de segurança |
| `estrategista` | Gemini 3.7 Pro | Planejamento macro de features |
| `ajudante` | Gemini 3.6 Flash | Dúvidas rápidas e conceitos |

### Workers (acionados pelo Orquestrador via DELEGATE/PIPELINE)

| Worker | Binário | Modelo |
|--------|---------|--------|
| `implementador` | claude | claude-sonnet-5 |
| `arquiteto` | claude (deepseek) | deepseek-v4-pro[1m] |
| `pesquisador` | copilot | auto |
| `designer` | agy | gemini-3.7-flash |
| `analista` | codex | gpt-5.6-terra |

---

## Cofre de Segurança (vault.py)

O Orquestra possui um cofre de credenciais criptografado por projeto usando **AES-256**.

### Funcionamento

- Ao abrir o Orquestra em uma pasta pela **primeira vez**, o assistente de configuração
  é exibido automaticamente e cria o cofre com senha mestra.
- Nas **sessões seguintes**, o cofre é desbloqueado com a senha mestra.
- As credenciais são injetadas automaticamente como **variáveis de ambiente** da sessão.
- O arquivo `.orquestra_vault` é **adicionado ao .gitignore automaticamente** — nunca
  vai parar no repositório Git.

### Comandos do Cofre

| Comando | Descrição |
|---------|-----------|
| `/senha NOME valor` | Salva uma credencial criptografada |
| `/senha NOME` | Injeta a credencial no ambiente (sem exibir o valor) |
| `/senhas` | Lista nomes das credenciais salvas |
| `/senha-remove NOME` | Remove uma credencial |
| `/senha-mestra NOVA_SENHA` | Troca a senha mestra do cofre |
| `/cofre-status` | Exibe quantas credenciais estão salvas |

### Segurança técnica

- Criptografia: **AES-256-GCM** via biblioteca `cryptography` (Python)
- Derivação de chave: **PBKDF2-SHA256** com 600.000 iterações
- Salt aleatório de 16 bytes por projeto
- Cada credencial é criptografada individualmente
- O valor nunca aparece no chat, nos logs ou no histórico do terminal

---

## Watchdog (Cão de Guarda)

Após cada código gerado por qualquer agente, o **Copilot é acionado silenciosamente**
em background para auditar o `git diff` e verificar se há bugs ou lógica errada.

- Se o código estiver **perfeito**: o watchdog não aparece na tela (silencioso).
- Se encontrar **problema**: exibe um alerta `🐕 [Watchdog Copilot]` com o diagnóstico.

Implementação: função `_run_copilot_watchdog()` em `orquestra.py` (linha ~2134).

---

## Fluxo de Trabalho Recomendado

O Orquestra foi projetado para funcionar da seguinte forma em cada projeto:

1. **Abra o terminal na pasta do projeto**
2. **Digite `orquestra`** — o cofre é verificado/desbloqueado automaticamente
3. **Converse naturalmente** — o Orquestrador (Gemini) entende sua intenção
4. **Para tarefas práticas**, o Orquestrador exibe um **plano resumido** antes de executar
5. **Confirme** — os agentes trabalham em paralelo ou em cadeia conforme o plano
6. **O Watchdog** escaneia silenciosamente o código gerado

### Exemplo de sessão

```
orquestra → cria uma função de login com JWT

🎼 Orquestrador:
Plano:
• pesquisador: lê o projeto para entender o padrão atual de auth
• implementador: cria a função JWT seguindo o padrão encontrado
• arquiteto: revisa a segurança da implementação

Confirma? (responda 'sim' ou detalhe alguma mudança)

orquestra → sim

⏺ 🔎 Pesquisador (buscando...)
⏺ 💻 Implementador (codificando...)
🐕 [Watchdog Copilot] — nenhum problema encontrado
```

---

## Arquivos do Projeto

```
App Orquestra/
├── orquestra.py          ← REPL principal (~3000 linhas)
├── vault.py              ← Módulo do cofre de credenciais
├── AGENTES.md            ← Este arquivo (documentação)
├── .orquestra_vault      ← [GERADO] Cofre criptografado (não versionar)
├── .orquestra_salt       ← [GERADO] Salt do cofre (não versionar)
└── ~/.orquestra.json     ← Configuração global (roles e workers)
```

---

## Configuração (~/.orquestra.json)

O arquivo de configuração global define os agentes disponíveis. Estrutura:

```json
{
  "active_role": "orquestrador",
  "roles": {
    "NOME_DO_ROLE": {
      "binary": "agy|claude|copilot|codex",
      "model": "nome-do-modelo",
      "env_file": "/path/para/.env-opcional",
      "icon": "emoji",
      "color": "cor-rich",
      "description": "O que este agente faz"
    }
  },
  "workers": {
    "NOME_DO_WORKER": {
      "binary": "agy|claude|copilot|codex",
      "model": "nome-do-modelo",
      "env_file": "/path/para/.env-opcional",
      "description": "O que este worker faz"
    }
  }
}
```

### Binários suportados

| Binário | Ferramenta | Modelos |
|---------|-----------|---------|
| `agy` | Antigravity CLI | gemini-* |
| `claude` | Claude Code CLI | claude-* ou deepseek-* (via env_file) |
| `copilot` | GitHub Copilot CLI | auto |
| `codex` | OpenAI Codex CLI | gpt-* |

---

## Comandos do Terminal

| Comando | Descrição |
|---------|-----------|
| `/ajuda` | Lista todos os comandos |
| `/agentes` | Lista os agentes disponíveis |
| `/agentes NOME` | Troca o agente ativo |
| `/agentes trocar` | Abre menu interativo |
| `/tarefas` | Lista tarefas recentes |
| `/aprovar ID` | Aprova e commita uma tarefa |
| `/goal OBJETIVO` | Planeja e executa um objetivo macro |
| `/senha NOME valor` | Salva credencial no cofre |
| `/senhas` | Lista credenciais do cofre |
| `/sair` | Fecha o Orquestra |
| `F1` | Atalho para /ajuda |
| `F2` | Atalho para /agentes trocar |
| `F3` | Atalho para /tarefas |

---

## Dependências

```bash
pip install rich prompt_toolkit cryptography
```

Ferramentas externas necessárias:
- `agy` — Antigravity CLI (Google Deepmind)
- `claude` — Claude Code CLI (Anthropic)
- `gh copilot` — GitHub Copilot CLI
- `codex` — OpenAI Codex CLI (opcional)

---

## Contribuindo

Ao modificar o `orquestra.py`:
1. Mantenha os comentários existentes
2. O Watchdog (`_run_copilot_watchdog`) deve sempre ser chamado após código gerado
3. Comandos `/senha*` devem sempre ser interceptados **antes** de chegar ao Orquestrador
4. O `vault.py` não deve ter dependências além de `cryptography` e stdlib Python
