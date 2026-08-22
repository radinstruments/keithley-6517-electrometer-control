# Fila de erros e sincronização

`SYST:ERR?`/`STAT:QUE?` devolve uma mensagem por consulta. A fila é drenada até código zero, com limite defensivo.

| Código | Significado relevante |
|---:|---|
| `-102` | Syntax Error |
| `-113` | Undefined header |
| `-213` | Init ignored |
| `-410` | Query INTERRUPTED |
| `+313` | Reading out of limit, conforme tabela atual do 6517B |
| `+320` | Buffer and format element mismatch |
| `350`/variante documentada | Queue overflow; preservar resposta bruta e sinal recebido |

## Política

- Não usar `*CLS` antes de cada transação, pois isso esconderia diagnósticos.
- Registrar erros antigos antes de uma limpeza intencional de sessão.
- Drenar erros depois de configuração, trigger, buffer e mudanças na fonte.
- Cada query deve consumir exatamente uma resposta.
- Mensagens compostas são bloqueadas no console para evitar respostas órfãs.
- Registrar comando, resposta, modelo, serial, firmware, estado e erros na ordem observada.

