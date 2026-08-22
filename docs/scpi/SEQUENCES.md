# Receitas transacionais SCPI

Estas sequências descrevem intenção e ordem. O spelling final vem do perfil detectado.

## Conexão

1. Abrir o recurso sem enviar IFC ao barramento por padrão.
2. Consultar `*IDN?`.
3. Validar fabricante, modelo esperado e modelo detectado antes de qualquer configuração.
4. Registrar serial e firmware.
5. Colocar a fonte em standby, zerar seu nível, desligar fonte manual de resistência, parar trigger/trace e ligar zero check.
6. Consultar/drenar a fila de erros.
7. Consultar `:SYST:VERS?`.

Se o 6517A estiver em modo GPIB DDC, orientar o operador a selecionar `LANGUAGE=SCPI` no painel. Não enviar comandos DDC por tentativa.

## Configuração de medição

1. `INIT:CONT OFF`.
2. `ABOR`.
3. `SYST:ZCH ON`.
4. Definir `SENS:FUNC`.
5. Para resistência, definir explicitamente `SENS:RES:VSC AUTO|MAN` e consultar o modo.
6. Definir autorange/faixa, NPLC e dígitos.
7. Aplicar a sequência específica de zero check/zero correct/referência de carga.
8. Configurar `FORM:DATA ASCII`, `FORM:ELEM` e `TRAC:ELEM`.
9. Ler de volta os elementos e drenar erros.

## LIVE

1. Configurar trigger finito enquanto `INIT:CONT` está desligado.
2. Usar `*OPC?` somente antes de habilitar contínuo.
3. `INIT:CONT ON` uma vez.
4. Obter novas amostras com `SENS:DATA:FRESH?`.
5. No encerramento: `INIT:CONT OFF`, depois `ABOR`.

Nunca enviar `*OPC?` depois de `INIT:CONT ON`.

## BUFFER

1. Parar trigger e `TRAC:FEED:CONT NEVER`.
2. Limpar o trace.
3. Aplicar e confirmar FORMAT/TRACE.
4. Programar `TRAC:POIN <número>`; no 6517B usar `50000`, não `MAX`, quando esse tamanho for necessário.
5. Definir timestamp, camadas ARM e TRIG.
6. `TRAC:FEED:CONT NEXT`.
7. `INIT`.
8. Acompanhar `TRAC:POIN:ACT?` até o alvo ou cancelamento.
9. Transferir `TRAC:DATA?` com timeout proporcional.
10. Fazer parsing pelo esquema confirmado e drenar erros.

## Fonte de tensão

Configuração em standby:

1. `OUTP1 OFF`.
2. Programar nível zero antes de reduzir a faixa.
3. Selecionar 100 V ou 1000 V.
4. Programar e habilitar limite de tensão.
5. Programar o nível desejado.
6. Ler de volta faixa, limite, estado do limite e nível.

Ativação:

1. Exigir confirmação física independente.
2. Consultar o interlock e bloquear se retornar 0.
3. Não interpretar retorno 1 como prova positiva.
4. Confirmar limite ativo e nível dentro do limite.
5. `OUTP1 ON`, drenar erros e ler o estado.

Desligamento não pede confirmação: `OUTP1 OFF`, nível zero, leitura de volta.

