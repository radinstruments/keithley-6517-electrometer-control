# Contrato FORMAT × TRACE

O contrato padrão da aplicação é ASCII com os campos retornados:

```text
READing,TSTamp,STATus
```

O trace armazena `TSTamp` como elemento opcional. Elementos básicos do registro do instrumento e os elementos retornados por `FORMat:ELEMents` são conceitos relacionados, mas não idênticos.

## Invariantes

- `FORMat:DATA ASCii` é aplicado explicitamente.
- `FORMat:ELEMents` e `TRACe:ELEMents` são programados na mesma transação.
- `FORMat:ELEMents?` deve confirmar `READING`, `TSTAMP` e `STATUS`.
- `TRACe:ELEMents?` deve confirmar `TSTAMP`.
- Campos vazios não são removidos.
- O número de tokens deve ser múltiplo do tamanho do esquema confirmado.
- O erro `+320` significa incompatibilidade entre buffer e formato no manual atual do 6517B.

## Compliance

Uma consulta de compliance depois da transferência representa somente o estado observado ao final do lote. Ela não pode marcar todas as amostras históricas como se compliance tivesse ocorrido em cada ponto. O driver guarda esse valor separadamente como `last_buffer_compliance_final`.

