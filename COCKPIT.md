# Cockpit — uma tela, todos os agentes

Evolução do script `~/.local/bin/cockpit` (tmux). Substitui o Orquestrador intermediário da TUI
(arquivada na tag `arquivo/tui-v7`): **você fala direto com cada CLI**, logada na sua conta, como
nos apps separados — só que tudo numa janela só.

## Uso

```
cockpit            # escolhe um projeto em ~/Documentos/Kali Linux (n = novo, q = sair)
cockpit .          # abre na pasta atual
```

| Aba | Agente | Atalho |
|---|---|---|
| 1 | claude (Claude Code) | Alt+1 |
| 2 | antigravity (`agy`) | Alt+2 |
| 3 | testes (shell) | Alt+3 |
| 4 | codex | Alt+4 |
| 5 | deepseek (`claude` com `~/.claude-deepseek-env`) | Alt+5 |
| 6 | opencode | Alt+6 |

- Cada CLI abre com o **modelo padrão dela** (nada de `--model`): o que você escolher lá dentro vale.
- Se um binário não existe, a aba é pulada e o cockpit avisa no terminal.
- Projeto com `.venv` ativa o venv em todas as abas, como antes.
- Sessão que já existe só é reaberta (não recria abas).

## Passar contexto entre agentes — `Ctrl+B` e depois `P`

Abre um menu na aba atual:

1. **diff do git** da pasta (`git status --short` + `git diff HEAD`)
2. **últimas 200 linhas da tela** desta aba (ex.: a resposta do Claude)

Depois você escolhe a aba de destino. O texto é **colado sem Enter**, com um cabeçalho dizendo de
onde veio — você escreve o pedido ("revisa isso", "continua daqui") e envia quando quiser.

- Pasta sem git ou sem mudança: o menu **avisa e não cola nada** (nunca falha em silêncio).
- Mais de 300 linhas: salva em `~/.cache/cockpit/<sessão>/` e cola só "Leia o arquivo: …".
  Nada é escrito dentro do seu projeto.

## Notas

- `Alt+4..9` e `Ctrl+B P` são aplicados ao servidor tmux sempre que o cockpit roda (valem em
  qualquer sessão tmux aberta, não mexem no `~/.tmux.conf`).
- Na primeira vez numa pasta nova, cada agente pergunta se confia nela — é dele, não do cockpit.
- Trocar abas: crie `~/.cockpit.json` com `{"janelas": [{"nome", "binario", "cmd"}]}`.
- Testes: `python3 test_cockpit.py` (usa um servidor tmux isolado e agentes falsos).
- Achado do tmux 3.7c: `display-popup` não expande `#{...}` no comando — por isso o atalho passa
  por `run-shell`, que chama `cockpit popup ...`.
