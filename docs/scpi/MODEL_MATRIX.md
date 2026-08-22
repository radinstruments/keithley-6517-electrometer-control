# Matriz Keithley 6517A × 6517B

| Capacidade | 6517A | 6517B | Regra do software |
|---|---:|---:|---|
| Modelo em `*IDN?` | `MODEL 6517A` | `MODEL 6517B` | Correspondência exata; divergência fecha a sessão |
| `:SYST:VERS?` esperado | `1991.0` | `1996.0` | Registrar a resposta real |
| Buffer com timestamp | 10.470 | 50.000 | Limite do perfil detectado |
| `TRAC:POIN MAX` | máximo dependente dos elementos | máximo legado compatível com A | Para 50.000 no B, enviar `50000` explicitamente |
| Fonte — faixa 100 V | ±100 V / ±10 mA | ±100 V / ±10 mA | Mostrar 10 mA, não 1 mA |
| Fonte — faixa 1000 V | ±1000 V / ±1 mA | ±1000 V / ±1 mA | Mostrar 1 mA |
| RS-232 máximo | 19.200 baud | 115.200 baud | Capacidade do perfil |
| GPIB legado | pode estar em DDC/617 | compatibilidades legadas dependem da revisão | Não tentar DDC automaticamente |

## Seleção de modelo

A interface oferece `Automático`, `Keithley 6517A` e `Keithley 6517B`. A seleção manual é somente o modelo esperado. `*IDN?` é a autoridade e não existe opção para ignorar `MODEL_MISMATCH`.

## Funções de medição comuns

- `VOLTage:DC`: faixa máxima programável de aproximadamente 210 V.
- `CURRent:DC`: até aproximadamente 21 mA.
- `RESistance`: até 100 EΩ.
- `CHARge`: até aproximadamente 2,1 µC.

A capacidade de medir tensão não deve ser confundida com a fonte bipolar interna de até ±1000 V.

## Firmware do 6517B

Há famílias de firmware A e B para unidades 6517B. A aplicação registra a revisão e não atualiza firmware. A página oficial do B03 alerta para não instalá-lo em unidades com firmware da família A.

