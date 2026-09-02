# Notas de Desenvolvimento da TUI (Textual)

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
