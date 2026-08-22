# Controlador Keithley 6517A/6517B

## Escopo

O projeto mantém a interface Tkinter existente e concentra a estabilização na
camada de controle. A implementação ativa está em:

- `src/keithley_6517_driver.py`: perfis, estados, receitas, parser SCPI e worker VISA;
- `src/keithley_6517_controle.py`: GUI completa, sem acesso direto à sessão VISA;
- `src/keithley_6517_comunicacao.py`: identificação simples usando o mesmo controlador;
- `tests/test_keithley_6517_driver.py`: instrumento VISA simulado.

## Arquitetura

```text
GUI
  -> KeithleyApplicationController / KeithleyController
    -> InstrumentStateMachine
      -> MeasurementRecipe / ChargeMeasurementRecipe
        -> SCPICommandBuilder
          -> VisaWorker (uma thread e uma fila FIFO)
            -> sessão NI-VISA
              -> Keithley 6517A ou 6517B
```

`VisaWorker` é o único objeto autorizado a criar ou acessar o
`ResourceManager` e a sessão do instrumento. Escrita, query, abertura,
fechamento e IFC são executados na thread proprietária. As threads de GUI,
aquisição e console somente submetem operações à fila.

Lotes de console e receitas são transações indivisíveis no controlador. Isso
impede que uma configuração seja intercalada com uma leitura ou outro comando.

## Detecção e perfis

Ao conectar, o worker executa `*IDN?`. O controlador aceita somente respostas
Keithley contendo `MODEL 6517A` ou `MODEL 6517B`; qualquer outro equipamento é
fechado e rejeitado.

Os perfis são instâncias independentes de `InstrumentProfile`. Mesmo quando um
comando atual coincide nos dois modelos, ele é selecionado pelo perfil
detectado. Diferenças já representadas:

| Item | 6517A | 6517B |
|---|---:|---:|
| máximo de pontos com timestamp usado pelo software | 10.470 | 50.000 |
| referência local | manual de referência 6517A | `docs/comunicação_modeloB/SCPI_GPIB_Keithley_6517B.md` |

## Máquina de estados

Estados explícitos:

- `Disconnected`
- `Connected`
- `Safe`
- `Configured`
- `Armed`
- `Acquiring`
- `HV Enabled`
- `Error`

Cada método valida o estado antes de enviar SCPI. Falhas de comunicação ou
erros SCPI em uma receita levam a `Error`. `safe_shutdown()` tenta desligar a
fonte, interromper o trigger, bloquear o buffer e habilitar Zero Check antes de
voltar a `Safe`.

Sequência de parada segura:

```scpi
:OUTPut1 OFF
:SENSe:RESistance:MANual:VSOurce:OPERate OFF
:SOURce:VOLTage 0
:INITiate:CONTinuous OFF
:ABORt
:TRACe:FEED:CONTrol NEVer
:SYSTem:ZCHeck ON
```

`INITiate:CONTinuous OFF` sempre precede `ABORt`, inclusive quando `:ABORt` é
digitado no console.

## Modos de aquisição

### ONE-SHOT

API: `one_shot_read()`.

```scpi
:INITiate:CONTinuous OFF
:ABORt
:READ?
```

`READ?` é permitido somente neste modo e na conversão única usada para
compensar o Zero Check Hop da função Charge.

### LIVE

API: `start_live()` e `read_live()`. O modo `continuo` da GUI corresponde a
LIVE.

O trigger é configurado e iniciado uma única vez com
`:INITiate:CONTinuous ON`. O laço consulta somente:

```scpi
:SENSe:DATA:FRESh?
```

O controlador não usa `*OPC?` durante iniciação contínua.

### BUFFER

API: `prepare_buffer()`, `start_buffer()`, `wait_buffer_complete()` e
`read_buffer_readings()`.

O fluxo:

1. entra em idle com `INIT:CONT OFF` e `ABORt`;
2. desabilita alimentação e limpa o buffer;
3. configura pontos, timestamp e as camadas ARM/TRIGger;
4. arma com `:TRACe:FEED:CONTrol NEXT`;
5. inicia uma vez com `:INITiate`;
6. consulta `:TRACe:POINts:ACTual?` com polling limitado;
7. lê `:TRACe:DATA?` somente ao completar;
8. executa parada segura.

O comando de feed source não documentado do protótipo foi removido.

## Formato e classificação

O formato de barramento e os elementos armazenados são sincronizados:

```scpi
:FORMat:DATA ASCii
:FORMat:ELEMents READing,TSTamp,STATus
:TRACe:ELEMents TSTamp
```

No 6517, `READing` e `STATus` são elementos sempre presentes no registro do
buffer; `TSTamp` é o elemento auxiliar correspondente. Essa combinação evita
`+320 Buffer & format element mismatch` no manual atual do 6517B. O código
`+313` corresponde a `Reading out of Limit` nessa revisão.

Cada `MeasurementReading` recebe uma classificação:

- `OK`
- `OVERLOAD`
- `UNDERFLOW`
- `COMPLIANCE`
- `INVALID`
- `ERROR`

O parser usa o elemento `STATus` antes do valor numérico. Isso distingue
overload de Zero Check, que podem usar valores próximos de `+9.91E+37`. Sem
status explícito, valores com módulo a partir de `9.9E37` são classificados
como overload. Leituras de resistência também consultam o estado de compliance.

O CSV é aberto em modo exclusivo e validado antes da primeira configuração do
instrumento. Formato:

```csv
valor,tempo,status
+1.000000E-12,0.100000,OK
+9.910000E+37,0.200000,OVERLOAD
```

Em erro de aquisição, o software tenta acrescentar uma linha `nan,0,ERROR` e
sempre executa a parada segura.

## Receita Charge

`ChargeMeasurementRecipe` implementa:

1. `INIT:CONT OFF` e `ABORt`;
2. Zero Check ON;
3. referência Charge OFF;
4. seleção de `CHARge`, range, NPLC e dígitos;
5. auto-discharge explicitamente OFF por padrão;
6. Zero Check OFF;
7. uma conversão one-shot para capturar o salto;
8. `:SENSe:CHARge:REFerence:ACQuire`;
9. `:SENSe:CHARge:REFerence:STATe ON`.

Antes da receita, a GUI orienta manter o circuito desconectado e a entrada
aberta. Depois do REL, ela pausa com uma confirmação para a conexão física do
circuito. Cancelar nessa etapa executa a parada segura.

## Controle dedicado de alta tensão

A aba **Alta tensão** separa programação, ativação e desligamento da fonte. O
fluxo é deliberadamente sequencial:

1. informar tensão entre -1000 V e +1000 V;
2. informar um limite absoluto igual ou maior que o módulo da tensão;
3. aplicar a configuração, que envia `:OUTPut1 OFF` antes de alterar a fonte;
4. confirmar circuito, fixture e área controlada;
5. confirmar novamente o nível e o limite no diálogo de ativação;
6. consultar `:SYSTem:INTerlock?` e só então enviar `:OUTPut1 ON`.

A faixa de 100 V é escolhida sempre que a tensão solicitada cabe nela; nos
demais casos é usada a faixa de 1000 V. O limite programável fica sempre
habilitado, e o menor valor entre faixa e limite determina a proteção efetiva.
O painel exibe nível, faixa, limite, interlock, estado da saída e conformidade
de corrente. O limite nominal depende da faixa: aproximadamente 10 mA na faixa
de 100 V e 1 mA na faixa de 1000 V.

O botão **DESLIGAR AGORA** permanece disponível durante a aquisição. Ele coloca
a saída em standby e zera o nível programado. Encerrar, cancelar ou falhar uma
aquisição também executa a parada segura e desliga a fonte.

Fora da aquisição, enquanto a saída está ativa, o painel atualiza o estado a
cada dois segundos. Se a leitura indicar interlock aberto com a saída ainda em
operate, o software solicita automaticamente o desligamento. As três
confirmações do checklist são limpas depois de cada ativação.

Para adquirir com HV ativa, aplique primeiro a configuração de medição na aba
Aquisição, depois ative a fonte e então inicie a aquisição sem alterar os
campos. Isso evita reconfigurar o eletrômetro com a saída energizada.

## Console e alta tensão

O console fica bloqueado do estado `Armed` até o fim da aquisição. Cada lote é
executado de forma serial e indivisível.

O parser separa mensagens por `;`, respeita strings entre aspas e reconhece
formas curtas e longas. Entre os padrões protegidos estão:

```scpi
:OUTP ON
:OUTPut1:STATe 1
CMD1;:OUTP ON
:SENSe:RESistance:MANual:VSOurce:OPERate ON
:SOURce:VOLTage 500
:TSEQuence:ARM
```

Configuração ou ativação HV exige confirmação explícita. Antes de ativar, o
controlador consulta `:SYSTem:INTerlock?`. Essa consulta é uma proteção
adicional, mas não substitui inspeção física: no 6517B, resposta `1` também pode
ocorrer quando o cabo não está conectado ao instrumento.

## Testes

Executar sem hardware:

```powershell
python -m unittest discover -s tests -v
```

A simulação valida perfis, ordem de comandos, trigger, estados, Charge, buffer,
overload, underflow, compliance, parser HV, erros, timeout e concorrência. Os
testes também confirmam que todos os acessos ao instrumento ocorrem no mesmo ID
de thread do worker VISA.

O smoke test real deve começar sem nenhuma fonte de alta tensão habilitada e
deve se limitar a: listar recursos, identificar, confirmar modelo 6517A, aplicar
parada segura, consultar a fila de erros e desconectar.
