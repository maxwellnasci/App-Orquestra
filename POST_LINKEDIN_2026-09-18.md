# Rascunho — post LinkedIn (2026-09-18)

Hoje eu integrei o Muse Code (o agente de terminal que a Meta lançou em beta há
pouco) como um novo "operário" dentro de um orquestrador multi-agente que venho
construindo — um sistema onde um agente estrategista decide o que fazer e delega
pra agentes especializados (implementar, revisar, auditar segurança), cada um
rodando em cima de um provedor de IA diferente (Claude, Gemini, DeepSeek e agora
Meta).

Depois de implementar a integração, rodei uma revisão de código automatizada em
cima do diff antes de mergear. Ela encontrou isto:

```python
except FileNotFoundError as e:
    section.close(ok=False, error_detail=f"binário não encontrado: {e}")
    return "", True, f"binário não encontrado: {e}"
```

Parece inofensivo. O problema: toda a função onde esse trecho vive tem contrato
de retorno `WorkerResult` (uma dataclass) — e todo lugar que chama essa função
acessa o resultado como `resultado.final_text`, `resultado.is_error`. Uma tupla
solta não tem esses atributos. Ou seja: se o binário de qualquer um dos agentes
não estivesse instalado no servidor (justamente o cenário mais provável logo
depois de plugar um agente novo), o sistema quebraria com um `AttributeError` —
silenciosamente, sem marcar a tarefa como erro, sem acionar o failover que já
existe pra esse tipo de falha.

O bug já existia antes de eu tocar nesse código. Só ficou exposto porque acabei
de adicionar um caminho novo (justamente o mais provável de não estar
configurado direito ainda).

A lição que fico: **integração de agente novo é o melhor momento pra expor
bug antigo** — não porque o código novo está errado, mas porque ele testa,
pela primeira vez de verdade, um caminho de erro que sempre existiu e nunca
tinha sido exercitado. E ter uma segunda camada de revisão automatizada antes
do merge — que não é a mesma "cabeça" que escreveu o código — pegou isso antes
de virar problema em produção.

Corrigido, testado, documentado. Seguindo pros próximos testes. 🚀

#IA #EngenhariaDeIA #MultiAgente #ClaudeCode #CodeReview
