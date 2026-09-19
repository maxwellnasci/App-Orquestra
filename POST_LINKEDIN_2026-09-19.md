# Rascunho — post LinkedIn (2026-09-19) — série "erros e acertos"

Fonte dos fatos: `APRENDIZADOS.md` §12 (todos os erros abaixo aconteceram e estão
documentados lá, com sintoma → causa → correção). Sugestão de imagem: print da tela do
Orquestra com o shell no painel da direita, a Atividade à esquerda e o chat embaixo.

---

## Versão principal

Hoje eu coloquei um terminal de verdade dentro do meu orquestrador de agentes de IA.
Os testes passaram. E o vim aparecia em branco.

O Orquestra é um cockpit multi-agente que roda no terminal: um agente estrategista
planeja, outros executam e revisam. Faltava uma coisa: eu precisava de um shell na
MESMA tela, pra logar num agente, exportar uma chave, abrir uma ferramenta na pasta —
o que só eu faço — sem perder de vista o que a equipe está fazendo.

Três erros do dia, na ordem em que doeram:

**1. O botão "Shell" já existia — e não fazia nada.**

```python
self.action_open_shell()                  # ❌ função async, sem await
self.call_later(self.action_open_shell)   # ✅
```

O Python cria a corrotina e joga fora. Sem erro, sem aviso. Um botão morto em silêncio.

**2. Testes verdes, vim em branco.**

O emulador de terminal que usei (pyte) levanta uma exceção numa sequência de escape
que o vim envia ao abrir. O `less` passava nos testes; o vim não desenhava nada. Só
apareceu quando rodei o app dentro do tmux, com teclado e clique de verdade.

**3. Um teste quebrado conversou com o LLM de verdade. Duas vezes.**

O campo de texto do chat "engolia" o meu atalho (Ctrl+E = fim da linha). O foco não
foi pro terminal, o teste digitou no chat e apertou Enter: chamada real ao modelo.
Agora o teste só digita se tiver certeza de onde está o foco.

O que deu certo: validar em 3 camadas (unitária → shell real no Pilot do Textual →
app real no tmux), entregar em 3 PRs pequenos, e escrever no repositório o que eu
**não** validei (ainda não testei o `claude` e o `codex` de tela cheia lá dentro).

A lição que fico: **teste verde prova que o teste passa. Só o app rodando de verdade
prova que o produto funciona.** E sempre que uma função `async` for chamada sem `await`,
o bug é silencioso.

Os 16 erros do dia (e os acertos) estão documentados no repositório. Próxima parada:
scrollback e copiar/colar. 🚀

#IA #EngenhariaDeIA #MultiAgente #ClaudeCode #Python #Textual #ErrosEAcertos

---

## Versão curta (se quiser algo mais rápido de ler)

Hoje coloquei um terminal de verdade dentro do meu orquestrador de agentes.
Três erros, um por camada:

❌ O botão "Shell" não fazia nada: `async` chamado sem `await`. Python não avisa.
❌ Testes verdes, mas o vim aparecia em branco. Só o app rodando no tmux mostrou.
❌ Um teste quebrado falou com o LLM de verdade — duas vezes.

✅ Validei em 3 camadas, entreguei em 3 PRs pequenos e documentei o que NÃO testei.

Lição: teste verde prova que o teste passa. Só o app rodando prova que o produto funciona.

#IA #EngenhariaDeIA #ClaudeCode #ErrosEAcertos

---

## Notas pra você antes de postar

- Se preferir não citar "LLM de verdade", troque por "um serviço externo" — mas o
  detalhe de que foi uma chamada REAL é o que dá graça ao erro nº 3 (não sei o custo — foram
  2 mensagens curtas —, então não cite valor).
- Não afirmei nada que não aconteceu: o "não validei `claude`/`codex` lá dentro" é literal
  (está em `APRENDIZADOS.md` §12, "O que continua NÃO validado").
- O "16 erros" é a contagem dos itens numerados de erros/armadilhas da §12 (os acertos
  ficam num bloco à parte). Alguns são armadilhas pequenas — se quiser, diga só "16
  aprendizados".
