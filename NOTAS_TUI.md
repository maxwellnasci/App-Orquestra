# Notas de Desenvolvimento da TUI (Textual)

## Editor e Idea Studio saíram do header (set/2026, Palco + Chat)

Desde `PLANO_PALCO_CHAT.md` os botões `📝 Editor` e `🎨 Idea Studio` não
existem mais no header — viraram abas de `#stage-tabs` (o palco), trocadas
por `_stage_show(pane_id)` ou pelos atalhos `Ctrl+1..4`. O header só tem
`📁 Arquivos` e `🎓 Aprendizado` como toggles, além dos botões de modelo
(`Orq:`/`Op:`/`Rev:`) — a nota abaixo sobre espaço/`min-width: 25` continua
valendo (inclusive sobrou mais espaço com menos botões).

## Limitação de Espaço no Header (Botões de Modelos)

**Problema Histórico:**
No desenvolvimento da interface `orquestra_tui.py`, enfrentamos um bug em que o nome do modelo não aparecia no botão após ser selecionado (ex: o modelo `claude-opus-4-6-thinking` ficava em branco, mas o `claude-sonnet-5` aparecia).

**Causa Raiz:**
O framework `Textual` tem um comportamento rigoroso de layout e overflow. Os botões de seleção de modelos (`Orq:`, `Op:`, `Rev:`) na barra superior (header) dividem um espaço horizontal finito com as abas da esquerda (`Arquivos`, `Editor`, etc).
- Se tentarmos forçar a largura dos botões no CSS (`min-width` elevado), eles empurram e "quebram" as abas da esquerda.
- O CSS ideal para manter o layout da barra superior intacto foi fixado em `min-width: 25`.
- Porém, se o texto dentro do botão ultrapassar o espaço disponível (~23 caracteres, considerando padding), **o Textual oculta o texto inteiro (wrap/clip)** em vez de deixar vazar. 

**Solução Permanente (O que fazer e o que não fazer):**
1. **NÃO MUDE O CSS:** Não mexa no `min-width` dos botões `.model-btn` no arquivo `orquestra_tui.py`. O valor correto é `min-width: 25`.
2. **ENCURTAR STRINGS:** A solução correta é sempre aplicar a função `shorten_name(name)` no momento de exibir a string no label do botão.
   - A função `shorten_name` deve retirar prefixos inúteis (ex: `gemini-`, `claude-`) e abreviar sufixos (`-high` -> `-hi`, `-thinking` -> `-thk`).
   - A string de exibição **nunca pode ultrapassar 18 caracteres**. (Para que `Orq: {18_chars}` = 23 caracteres, cabendo no botão de 25 com padding).
3. **BACKEND NÃO É AFETADO:** Essa limitação é **estritamente visual**. O `value` real que circula nas variáveis (ex: `gemini-3.7-flash-high`) permanece intacto para ser passado à API de backend.

## Terminal real (set/2026) — painel lateral 🖥 Shell + shell externo (Ctrl+O)

Duas formas de ter um shell de verdade dentro do Orquestra:

1. **`💻 Shell` (header)** — `action_open_shell` usa `self.suspend()` e roda
   `$SHELL`; a TUI congela até `exit`/Ctrl+D. Terminal 100% real (vim, claude,
   codex, login interativo funcionam sem ressalva). Agora é `Ctrl+O` (o botão
   `💻 Shell` abre o painel lateral). *Bug antigo corrigido:* o handler chamava
   a action `async` sem `await`, então nada acontecia.
2. **Painel lateral `📊 Logs | 🖥 Shell` (Ctrl+E, ou o botão `💻 Shell`)** —
   `orquestra_term.PtyTerminal`: shell num pty (`ptyprocess`) desenhado por
   `pyte` dentro de um widget Textual. Vive no `#terminal-panel` (a coluna da
   direita, 45% da tela, que já mostrava os logs), então o shell fica **ao lado
   da Atividade e acima do chat** — dá pra rodar `claude login`, `export KEY=...`
   ou abrir um agente na pasta vendo o Orquestra trabalhar. Sobe no primeiro
   acesso, no cwd do projeto, com o `os.environ` já preenchido pelo vault.
   **Todas as teclas vão pro shell** (inclusive os atalhos do app); `Ctrl+]`
   devolve o foco ao chat, `Ctrl+T` fecha/abre o painel (o shell segue vivo) e
   `Ctrl+E` volta pro shell. O **✕** no canto do painel fecha com o mouse (mesmo efeito
   do Ctrl+T: só esconde, o shell segue vivo). O shell morre com o app.
   `Ctrl+O` continua abrindo o shell **externo** (suspende a TUI) — o mais fiel
   (copiar/colar, scrollback, apps de tela cheia exigentes).

Decisões/armadilhas (o que não é óbvio no código):
- **`event.character` não é confiável** (None pra Enter/Ctrl+letra no Pilot,
  `\x08` pro Backspace): `key_to_bytes` mapeia pelo *nome* da tecla.
- **pyte não faz tela alternativa** (vim/less/codex) nem aceita `private=` em
  ~20 handlers CSI: `_Screen` implementa 1049/1047/47 e `_ignore_private` evita
  o `TypeError` que matava o parser (vim aparecia em branco).
- **Ctrl+5 = Ctrl+] em terminais sem protocolo kitty** (byte 0x1d) → o atalho
  de abrir é Ctrl+E, não Ctrl+5.
- `_re_enable_input` (fim de tarefa) não rouba o foco de quem digita no terminal.
- **Fechar o painel devolve o foco ao chat** se o foco estava dentro dele — inclusive
  quando é o próprio ✕ (o clique passa o foco pro botão antes do handler rodar).
- **Ctrl+E é binding `priority`**: o `TextArea` do chat liga Ctrl+E a "fim da
  linha" e engolia o atalho justo quando o foco estava no chat. Com o shell em
  foco a action repassa `\x05` ao shell (readline: fim da linha).
- `RichLog` adia a escrita enquanto está oculto: com a aba Shell ativa os logs
  novos só aparecem ao voltar pra aba `📊 Logs` (nada se perde).
- Ao testar a TUI, **nunca digite sem garantir o foco no terminal**: Enter no
  chat dispara chamada real ao Orquestrador (foi assim que um teste quebrado
  falou com o LLM sem querer).
- Chave exportada no shell embutido vale só pra ele; pro Orquestra usar `/senha`.
- **Limites**: sem scrollback, sem mouse, sem seleção/cópia (copie pelo
  `💻 Shell` ou tmux); pyte não cobre 100% do xterm.
- Testes: `python3 test_orquestra_term.py` (Pilot + shell real, 62 checks).
