# Keithley 6517B - referência SCPI via GPIB

> **Referência legada:** esta consolidação foi produzida a partir da revisão A,
> de junho de 2008. Para implementação e validação, prevalece o
> [Manual de Referência 6517B Rev. F, janeiro de 2024](https://download.tek.com/manual/6517B-901-01F_Jan2024_Ref.pdf)
> e a matriz rastreada em `docs/scpi/`. Divergências não devem ser resolvidas
> por suposição.

## 1. Escopo e fontes

Este documento consolida os comandos dos arquivos `SCPI.pdf` e `SCPI.2.pdf` para controle remoto do eletrômetro Keithley 6517B em modo SCPI pela interface GPIB/IEEE-488.

Incluído:

- configuração e regras de comunicação GPIB;
- comandos de barramento IEEE-488;
- comandos comuns IEEE-488.2;
- comandos orientados a medição;
- todos os subsistemas SCPI descritos na referência do 6517B;
- formatos de resposta, buffer, trigger e estrutura de status.

Excluído por ser exclusivo de RS-232:

- `:SYSTem:LOCal`;
- `:SYSTem:REMote`;
- `:SYSTem:LLOCkout <b>` e `:SYSTem:LLOCkout?`;
- baud rate, bits de dados, paridade, stop bits, flow control e terminadores da porta serial.

Os comandos DDC também não fazem parte deste documento. O instrumento deve estar com `LANGUAGE = SCPI`.

Referências: Manual do Usuário, seção 5; Manual de Referência, seções 11 a 14. Ambos são revisão A, junho de 2008. O 6517B declara conformidade com SCPI 1996.0.

---

## 2. Configuração GPIB do instrumento

- Interface de fábrica: GPIB/IEEE-488.
- Endereço primário de fábrica: `27`.
- Faixa permitida: `0` a `30`.
- Cada equipamento, inclusive o controlador, deve ter endereço único. Controladores costumam usar `0` ou `21`.
- Linguagem: `SCPI`.
- Caminho no painel: `MENU > COMMUNICATION > GPIB` e depois `ADDRESS`/`LANGUAGE`.
- A alteração do endereço é salva em memória não volátil.
- Ao trocar de RS-232 para GPIB, o instrumento volta ao setup de power-on.

Uma identificação inicial segura é:

```scpi
*IDN?
```

Resposta esperada:

```text
KEITHLEY INSTRUMENTS INC., MODEL 6517B, <serial>, <firmware-digital>/<firmware-display>
```

### 2.1 Terminação e troca de mensagens

- Toda mensagem enviada deve terminar em `LF`, `EOI` ou `LF + EOI`. Sem terminação, o barramento pode travar.
- Toda resposta GPIB termina em `LF + EOI`.
- Respostas a várias queries na mesma mensagem são separadas por ponto e vírgula (`;`).
- Itens de uma mesma query são separados por vírgula (`,`).
- Leia a resposta completa antes de enviar outra mensagem de programa.
- Uma query apenas coloca a resposta na output queue; o controlador precisa então endereçar o 6517B como talker. Bibliotecas VISA normalmente fazem isso no método de leitura/query.

Exemplo de mensagem múltipla:

```scpi
:SYSTem:VERSion?;:SYSTem:DATE?;:SYSTem:TIME?
```

### 2.2 Regras de sintaxe SCPI

- Comandos não diferenciam maiúsculas e minúsculas.
- Letras maiúsculas na documentação formam a abreviação válida: `:SYSTem:PRESet` pode ser `:SYST:PRES`.
- Use a forma curta ou a forma longa de cada palavra; formas intermediárias são inválidas.
- Colchetes indicam nós opcionais e nunca são transmitidos: `:INITiate[:IMMediate]` significa `:INITiate` ou `:INITiate:IMMediate`.
- Sinais `<...>` indicam tipos de parâmetro e nunca são transmitidos.
- Deve existir pelo menos um espaço entre comando e parâmetro.
- O `:` inicial é opcional no começo de uma mensagem.
- Separe comandos na mesma mensagem com `;`.
- Depois de `;`, um `:` faz o parser voltar à raiz. Sem esse `:`, o comando continua no nível do caminho anterior.
- Os comandos são executados na ordem. Após um comando inválido, os anteriores já foram executados e os posteriores são ignorados.

Tipos de parâmetro:

| Tipo | Significado |
|---|---|
| `<b>` | Booleano: `0`/`OFF` ou `1`/`ON`. Alguns comandos também aceitam `ONCE`. |
| `<name>` | Uma palavra escolhida entre as opções documentadas. |
| `<NRf>` | Número inteiro, real ou em notação exponencial. |
| `<n>` | `<NRf>` ou, quando aplicável, `DEFault`, `MINimum`, `MAXimum`. |
| `<list>` | Lista de canais, por exemplo `(@1:10)` ou `(@2,4,6)`. |
| `<a>` | Texto ASCII. |

Queries numéricas costumam aceitar `? DEFault`, `? MINimum` e `? MAXimum`. Exemplo:

```scpi
:TRIGger:TIMer? MINimum
```

---

## 3. Comandos do barramento IEEE-488

Estes não são strings SCPI. São operações do controlador GPIB/VISA.

| Comando | Efeito no 6517B |
|---|---|
| `REN` | Habilita remote; entra efetivamente em remote quando o instrumento é endereçado como listener. |
| `IFC` | Coloca o instrumento em local e nos estados talker/listener idle; não altera setup, dados ou registradores. |
| `LLO` | Bloqueia as teclas do painel, exceto POWER; LOCAL deixa de cancelar remote. |
| `GTL` | Retorna o 6517B ao estado local e restaura o painel. |
| `DCL` | Limpa interfaces de todos os dispositivos: input buffer, output queue e comandos pendentes. Não altera setup nem dados armazenados. |
| `SDC` | Mesmo efeito básico de DCL, mas somente nos dispositivos endereçados. |
| `GET` | Gera trigger GPIB. Pode atuar nas camadas arm, scan ou measure e como pretrigger do buffer. |
| `SPE`/`SPD` | Inicia/finaliza serial poll para ler o status byte e localizar quem solicitou SRQ. |

`*TRG` tem o mesmo efeito de trigger de `GET`, mas é enviado como comando IEEE-488.2.

---

## 4. Comandos comuns IEEE-488.2

| Comando | Função e resposta |
|---|---|
| `*CLS` | Limpa event registers e error queue; cancela estados internos de `*OPC`/`*OPC?`. Não limpa enable registers. |
| `*ESE <0..255>` | Programa a máscara do Standard Event Enable Register. |
| `*ESE?` | Retorna a máscara ESE em decimal. |
| `*ESR?` | Retorna o Standard Event Status Register em decimal e o limpa. |
| `*IDN?` | Retorna fabricante, modelo, número de série e revisões de firmware. |
| `*OPC` | Define OPC no ESR quando as operações pendentes terminarem. |
| `*OPC?` | Retorna ASCII `1` quando as operações pendentes terminarem. Enquanto aguarda, bloqueia novos comandos. |
| `*OPT?` | Retorna o código da opção/scanner instalado; retorna `0` se não houver opção. |
| `*RCL <0..9>` | Recupera o setup salvo na posição indicada. |
| `*RST` | Aplica defaults `*RST`, cancela comandos pendentes e coloca o instrumento em idle. |
| `*SAV <0..9>` | Salva o setup atual na posição indicada. |
| `*SRE <0..255>` | Programa a máscara do Service Request Enable Register. |
| `*SRE?` | Retorna a máscara SRE em decimal. |
| `*STB?` | Retorna o status byte em decimal; não o limpa. |
| `*TRG` | Envia trigger de barramento; equivale a GET. |
| `*TST?` | Executa checksum de ROM. `0` = passou; `1` = falhou. |
| `*WAI` | Suspende a execução de comandos posteriores até terminar toda operação overlapped anterior. |

Operações overlapped do 6517B: `:INITiate`, `:INITiate:CONTinuous ON` e `*TRG`.

Alerta: `:INITiate:CONTinuous ON` seguido de `*OPC?` pode bloquear o instrumento indefinidamente, pois a iniciação contínua não volta a idle. A recuperação exige DCL ou SDC. Prefira SRQ, polling de status ou uma sequência finita.

---

## 5. Comandos orientados a medição

| Comando | Função |
|---|---|
| `:FETCh?` | Retorna a última leitura pós-processada. Não dispara nova conversão e pode repetir a mesma leitura. |
| `:CONFigure:<function>` | Configura uma medição one-shot na função escolhida. |
| `:CONFigure?` | Retorna a função configurada. |
| `:READ?` | Executa `:ABORt`, `:INITiate` e `:FETCh?`, nessa ordem. |
| `:MEASure[:<function>]?` | Executa configuração one-shot e leitura. Sem função, usa a atual. |

Valores de `<function>`:

- `VOLTage[:DC]`;
- `CURRent[:DC]`;
- `RESistance`;
- `CHARge`.

Exemplos:

```scpi
:MEASure:VOLTage:DC?
:MEASure:CURRent:DC?
:MEASure:RESistance?
:MEASure:CHARge?
```

Para garantir leitura nova sem reconfigurar a medição, prefira `:SENSe:DATA:FRESh?`. Com CALC1 habilitado, `:CALCulate:DATA:FRESh?` retorna a leitura após o cálculo.

---

## 6. Catálogo completo dos subsistemas SCPI

Nas tabelas abaixo, `comando / comando?` indica a forma de escrita e sua query. Comandos terminados apenas em `?` são somente leitura. Nós entre colchetes são opcionais.

### 6.1 CALCulate - matemática e limites

#### CALCulate1

Prefixo: `:CALCulate[1]`

| Sufixo | Parâmetro/ação |
|---|---|
| `:FORMat <name>` / `:FORMat?` | `NONE`, `POLynomial`, `PERCent`, `RATio`, `DEViation`, `PDEViation`, `LOG10`. Default: `POLynomial`. |
| `:KMATh:MA0Factor <NRf>` / `?` | Coeficiente a0: -9.999999E30 a +9.999999E30. Default 0. |
| `:KMATh:MA1Factor <NRf>` / `?` | Coeficiente a1: -9.999999E20 a +9.999999E20. Default 1. |
| `:KMATh:MA2Factor <NRf>` / `?` | Coeficiente a2: -9.999999E30 a +9.999999E30. Default 0. |
| `:KMATh:PERCent <NRf>` / `?` | Valor alvo do cálculo percentual: -9.999999E35 a +9.999999E35. Default 1. |
| `:KMATh:REFerence <NRf>` / `?` | Referência de ratio/deviation/% deviation: -9.999999E35 a +9.999999E35. Default 1. |
| `:STATe <b>` / `:STATe?` | Habilita/desabilita CALC1. `*RST`: ON; `:SYST:PRES`: OFF. |
| `:DATA[:LATest]?` | Retorna o último resultado calculado. |
| `:DATA:FRESh?` | Aguarda e retorna um novo resultado calculado. |
| `:IMMediate` | Recalcula os dados de entrada. |

#### CALCulate2 - estatística do buffer

Prefixo: `:CALCulate2`

| Sufixo | Parâmetro/ação |
|---|---|
| `:FORMat <name>` / `:FORMat?` | `MEAN`, `SDEViation`, `MAXimum`, `MINimum`, `PKPK`, `NONE`. Default `NONE`. |
| `:STATe <b>` / `:STATe?` | Habilita/desabilita CALC2. `*RST`: ON; preset: OFF. |
| `:IMMediate` | Recalcula os dados brutos do buffer. |
| `:DATA?` | Retorna o resultado CALC2. |

#### CALCulate3 - testes de limite

| Comando | Função |
|---|---|
| `:CALCulate3:LIMit[1]:UPPer[:DATA] <n>` / `...?` | Limite superior 1, -9.999999E35 a +9.999999E35. Default 1. |
| `:CALCulate3:LIMit[1]:UPPer:SOURce <0..15>` / `...?` | Padrão de saída digital para limite superior 1. Default 0. |
| `:CALCulate3:LIMit[1]:LOWer[:DATA] <n>` / `...?` | Limite inferior 1. Default -1. |
| `:CALCulate3:LIMit[1]:LOWer:SOURce <0..15>` / `...?` | Padrão de saída digital para limite inferior 1. Default 0. |
| `:CALCulate3:LIMit[1]:STATe <b>` / `...?` | Habilita o teste LIMIT1. Default OFF. |
| `:CALCulate3:LIMit[1]:FAIL?` | Resultado do teste; o manual documenta `1 = pass`, `0 = fail`. |
| `:CALCulate3:LIMit[1]:CLEar[:IMMediate]` | Limpa a indicação de falha. |
| `:CALCulate3:LIMit[1]:CLEar:AUTO <b>` / `...?` | Auto-clear. Default ON. |
| `:CALCulate3:LIMit2:UPPer[:DATA] <n>` / `...?` | Limite superior 2. Default 1. |
| `:CALCulate3:LIMit2:UPPer:SOURce <0..15>` / `...?` | Padrão digital do limite superior 2. Default 0. |
| `:CALCulate3:LIMit2:LOWer[:DATA] <n>` / `...?` | Limite inferior 2. Default -1. |
| `:CALCulate3:LIMit2:LOWer:SOURce <0..15>` / `...?` | Padrão digital do limite inferior 2. Default 0. |
| `:CALCulate3:LIMit2:STATe <b>` / `...?` | Habilita LIMIT2. Default OFF. |
| `:CALCulate3:LIMit2:FAIL?` | Resultado do teste; `1 = pass`, `0 = fail`, conforme o manual. |
| `:CALCulate3:LIMit2:CLEar[:IMMediate]` | Limpa a indicação de falha. |
| `:CALCulate3:LIMit2:CLEar:AUTO <b>` / `...?` | Auto-clear. Default ON. |
| `:CALCulate3:PASS:SOURce <0..15>` / `...?` | Padrão digital para aprovação. Default 0. |
| `:CALCulate3:CLIMits:FAIL?` | Resultado composto de LIMIT1 e LIMIT2. |
| `:CALCulate3:BSTRobe:STATe <b>` / `...?` | Habilita/desabilita o strobe de binning. Default OFF. |
| `:CALCulate3:IMMediate` | Refaz os testes de limite. |

### 6.2 DISPlay - visor e mensagens

| Comando | Função |
|---|---|
| `:DISPlay[:WINDow[1]]:ATTRibutes?` | Retorna atributos dos caracteres do visor superior; `1` indica blink. |
| `:DISPlay[:WINDow[1]]:TEXT:DATA <a>` / `...?` | Define/lê texto de até 20 caracteres no visor superior. |
| `:DISPlay[:WINDow[1]]:TEXT:STATe <b>` / `...?` | Habilita/desabilita a mensagem superior. |
| `:DISPlay[:WINDow[1]]:DATA?` | Lê os dados mostrados na parte superior. |
| `:DISPlay:WINDow2:ATTRibutes?` | Retorna atributos do visor inferior. |
| `:DISPlay:WINDow2:TEXT:DATA <a>` / `...?` | Define/lê texto de até 32 caracteres no visor inferior. |
| `:DISPlay:WINDow2:TEXT:STATe <b>` / `...?` | Habilita/desabilita a mensagem inferior. |
| `:DISPlay:WINDow2:DATA?` | Lê os dados mostrados na parte inferior. |
| `:DISPlay:CNDisplay` | Cancela operações/mensagens NEXT/PREV e volta ao display normal. |
| `:DISPlay:SMESsage <b>` / `...?` | Controla status-message mode. Default OFF. |
| `:DISPlay:ENABle <b>` / `...?` | Liga/desliga o circuito do display. |

`*RST` e `:SYST:PRES` não apagam textos nem alteram o estado do message mode/display. Power cycle apaga os textos, desabilita message mode e habilita o display.

### 6.3 FORMat - formato das respostas

| Comando | Função |
|---|---|
| `:FORMat[:DATA] <type>[,<length>]` / `...?` | Seleciona `ASCii`, `REAL,32`, `REAL,64`, `SREal` ou `DREal`. Default `ASCii`. |
| `:FORMat:ELEMents <item-list>` / `...?` | Seleciona os elementos enviados: `READing`, `CHANnel`, `RNUMber`, `UNITs`, `TSTamp`, `STATus`, `ETEMperature`, `HUMidity`, `VSOurce`. |
| `:FORMat:BORDer <name>` / `...?` | Ordem de bytes binária: `NORMal` ou `SWAPped`. Default `SWAPped`. |

Defaults dos elementos: todos habilitados, exceto `ETEMperature`, `HUMidity` e `VSOurce`.

### 6.4 OUTPut - saída da fonte e polaridade TTL

| Comando | Função |
|---|---|
| `:OUTPut1[:STATe] <b>` / `...?` | Coloca a fonte de tensão em operate/standby. Default OFF. |
| `:OUTPut1:TTL[1]:LSENse <AHIGh/ALOW>` / `...?` | Polaridade da linha digital 1. Default `AHIGh`. |
| `:OUTPut1:TTL2:LSENse <AHIGh/ALOW>` / `...?` | Polaridade da linha digital 2. |
| `:OUTPut1:TTL3:LSENse <AHIGh/ALOW>` / `...?` | Polaridade da linha digital 3. |
| `:OUTPut1:TTL4:LSENse <AHIGh/ALOW>` / `...?` | Polaridade da linha digital 4. |

### 6.5 ROUTe - scanner e canais

| Comando | Função |
|---|---|
| `:ROUTe:CLOSe <list>` | Fecha os canais indicados. |
| `:ROUTe:CLOSe:STATe?` | Consulta o canal fechado. |
| `:ROUTe:CLOSe? <list>` | Para cada canal, retorna `1 = fechado`, `0 = aberto`. |
| `:ROUTe:OPEN <list>` | Abre os canais indicados. |
| `:ROUTe:OPEN:ALL` | Abre todos os canais. |
| `:ROUTe:OPEN? <list>` | Para cada canal, retorna `1 = aberto`, `0 = fechado`. |
| `:ROUTe:SCAN[:INTernal] <list>` / `...?` | Define/lê a scan list interna, até 10 canais. Default: todos os 10. |
| `:ROUTe:SCAN:EXTernal <n>` / `...?` | Define/lê a quantidade/lista externa, 1 a 400 canais. Default 10. |
| `:ROUTe:SCAN:LSELect <INTernal/EXTernal/NONE>` / `...?` | Seleciona o tipo de scan. Default `NONE`. |
| `:ROUTe:SCAN:STIMe <0..99999.9999>` / `...?` | Settling time da placa interna, em segundos. Default 0. |
| `:ROUTe:SCAN:SMEThod <VOLTage/CURRent>` / `...?` | Método do scan interno. Default `VOLTage`. |
| `:ROUTe:SCAN:VSLimit <b>` / `...?` | Habilita limite de 200 V da placa interna. Default ON. |

Os comandos internos dependem da placa scanner instalada.

### 6.6 SENSe1 - seleção e configuração de medição

#### Função e aquisição

| Comando | Função |
|---|---|
| `[:SENSe[1]]:FUNCtion <name>` / `...?` | Seleciona `'VOLTage[:DC]'`, `'CURRent[:DC]'`, `'RESistance'` ou `'CHARge'`. Default `'VOLT:DC'`. |
| `[:SENSe[1]]:DATA[:LATest]?` | Retorna a última leitura. |
| `[:SENSe[1]]:DATA:FRESh?` | Aguarda uma leitura que ainda não tenha sido retornada. |

#### Comandos comuns por função

Substitua `<func>` por `VOLTage[:DC]`, `CURRent[:DC]`, `RESistance` ou `CHARge`, observando as exceções e faixas abaixo.

| Sufixo sob `[:SENSe[1]]:<func>` | Função |
|---|---|
| `:APERture <166.67E-6..200E-3>` / `?` | Tempo de integração em segundos. Default: 16,67 ms em 60 Hz ou 20 ms em 50 Hz. |
| `:APERture:AUTO <b>` ou `ONCE` / `?` | Auto aperture. Default OFF. |
| `:NPLCycles <0.01..10>` / `?` | Integração em ciclos de rede. Default 1. |
| `:NPLCycles:AUTO <b>` ou `ONCE` / `?` | Auto NPLC. Default OFF. |
| `:RANGe[:UPPer] <n>` / `?` | Seleciona a faixa de medição. |
| `:RANGe:AUTO <b>` ou `ONCE` / `?` | Autorange. `*RST`: ON; preset: OFF. |
| `:RANGe:AUTO:ULIMit <n>` / `?` | Limite superior do autorange, quando disponível. |
| `:RANGe:AUTO:LLIMit <n>` / `?` | Limite inferior do autorange, quando disponível. |
| `:REFerence <n>` / `?` | Define referência relativa. |
| `:REFerence:STATe <b>` / `?` | Habilita referência. Default OFF. |
| `:REFerence:ACQuire` | Usa o sinal de entrada como referência. |
| `:DIGits <4..7>` / `?` | Resolução. Default 6. |
| `:DIGits:AUTO <b>` ou `ONCE` / `?` | Resolução automática. |
| `:AVERage:TYPE <NONE/SCALar/ADVanced>` / `?` | Tipo de filtro digital. Default normalmente `SCALar`; em carga, `NONE`. |
| `:AVERage:TCONtrol <MOVing/REPeat>` / `?` | Método do filtro. `*RST`: REPeat; preset: MOVing. |
| `:AVERage:COUNt <1..100>` / `?` | Número de amostras. Default 10. |
| `:AVERage:ADVanced:NTOLerance <0..100>` / `?` | Tolerância de ruído em %. Default 1. |
| `:AVERage[:STATe] <b>` / `?` | Habilita/desabilita o filtro digital. Default OFF. |
| `:MEDian[:STATe] <b>` / `?` | Habilita/desabilita filtro mediana. Default ON. |
| `:MEDian:RANK <1..5>` / `?` | Rank da mediana. Default 1. |

Faixas específicas:

| Função | Range | Referência | Particularidades |
|---|---:|---:|---|
| `VOLTage[:DC]` | 0 a 210 V; default 200 V. Auto ULIM 200 V, LLIM 2 V. | -210 a +210 V | `:GUARd` e `:XFEedback`. |
| `CURRent[:DC]` | 0 a 21E-3 A; default 20 mA. Auto ULIM 20 mA, LLIM 2 pA. | -21 mA a +21 mA | `:DAMPing`. |
| `RESistance` | Auto V-source: 0 a 100E18 ohm; default 2 Mohm. Auto ULIM 200 Tohm, LLIM 2 Mohm. | -100E18 a +100E18 ohm | Modos auto/manual, resistividade e `:DAMPing`. |
| `CHARge` | 0 a 2.1E-6 C; default 2 uC. | -2.1E-6 a +2.1E-6 C | Auto range por grupo e auto-discharge. |

#### VOLTage - comandos adicionais

| Comando | Função |
|---|---|
| `:SENSe:VOLTage[:DC]:GUARd <b>` / `...?` | Habilita/desabilita guard. Default OFF. |
| `:SENSe:VOLTage[:DC]:XFEedback <b>` / `...?` | Habilita/desabilita feedback externo. Default OFF. |

#### CURRent - comando adicional

| Comando | Função |
|---|---|
| `:SENSe:CURRent[:DC]:DAMPing <b>` / `...?` | Habilita/desabilita damping. Default OFF. |

#### RESistance - Auto/Manual V-source e resistividade

| Comando | Função |
|---|---|
| `:SENSe:RESistance[:AUTO]:RANGe[:UPPer] <0..100E18>` / `...?` | Faixa de resistência no modo Auto V-source. |
| `:SENSe:RESistance[:AUTO]:RANGe:AUTO <b>` ou `ONCE` / `...?` | Autorange de resistência. |
| `:SENSe:RESistance[:AUTO]:RANGe:AUTO:ULIMit <n>` / `...?` | Limite superior do autorange. |
| `:SENSe:RESistance[:AUTO]:RANGe:AUTO:LLIMit <n>` / `...?` | Limite inferior do autorange. |
| `:SENSe:RESistance:MANual:CRANge[:UPPer] <0..21E-3>` / `...?` | Range de corrente para ohms com V-source manual. Default 20 mA. |
| `:SENSe:RESistance:MANual:CRANge:AUTO <b>` ou `ONCE` / `...?` | Autorange de corrente no modo manual. |
| `:SENSe:RESistance:MANual:VSOurce[:AMPLitude] <0..1000>` / `...?` | Nível da fonte para medição manual. Default 100 V. |
| `:SENSe:RESistance:MANual:VSOurce:RANGe <n>` / `...?` | `<=100` seleciona range 100 V; `>100` seleciona range 1000 V. |
| `:SENSe:RESistance:MANual:VSOurce:OPERate <b>` / `...?` | Coloca a fonte em operate/standby. Default OFF. |
| `:SENSe:RESistance:IREFerence <b>` / `...?` | Habilita/desabilita a referência de corrente. |
| `:SENSe:RESistance:DAMPing <b>` / `...?` | Habilita/desabilita damping. Default OFF. |
| `:SENSe:RESistance:VSControl <MANual/AUTO>` / `...?` | Seleciona o modo da fonte. Default `MANual`. |
| `:SENSe:RESistance:MSELect <NORMal/RESistivity>` / `...?` | Seleciona resistência normal ou resistividade. Default `NORMal`. |
| `:SENSe:RESistance:RESistivity:STHickness <0.0001..99.9999>` / `...?` | Espessura da amostra em mm. Default 1 mm. |
| `:SENSe:RESistance:RESistivity:FSELect <M8009/USER>` / `...?` | Seleciona fixture. Default `M8009`. |
| `:SENSe:RESistance:RESistivity:M8009:RSWitch?` | Lê o switch do 8009: surface/volume. |
| `:SENSe:RESistance:RESistivity:USER:RSELect <SURFace/VOLume>` / `...?` | Tipo da medição no fixture customizado. Default `SURFace`. |
| `:SENSe:RESistance:RESistivity:USER:KSURface <0.001..999.999>` / `...?` | Constante de superfície Ks. Default 1.000. |
| `:SENSe:RESistance:RESistivity:USER:KVOLume <0.001..999.999>` / `...?` | Constante de volume Kv. Default 1.000. |

#### CHARge - comandos adicionais

| Comando | Função |
|---|---|
| `:SENSe:CHARge:RANGe:AUTO:LGRoup <HIGH/LOW>` / `...?` | Seleciona o grupo-limite do autorange. Default `HIGH`. |
| `:SENSe:CHARge:ADIScharge[:STATe] <b>` / `...?` | Habilita/desabilita descarga automática. Default OFF. |
| `:SENSe:CHARge:ADIScharge:LEVel <NRf>` / `...?` | Nível de auto-discharge. A tabela imprime -2.2E6 a +2.2E6, mas isso conflita com o range de carga e com o default 2E-6 C; o intervalo provavelmente pretendido é -2.2E-6 a +2.2E-6 C. Validar no firmware. |

### 6.7 SOURce - fonte de tensão, compliance e saídas digitais

| Comando | Função |
|---|---|
| `:SOURce:TTL[1][:LEVel] <b>` / `...?` | Estado da linha digital 1. |
| `:SOURce:TTL2[:LEVel] <b>` / `...?` | Estado da linha digital 2. |
| `:SOURce:TTL3[:LEVel] <b>` / `...?` | Estado da linha digital 3. |
| `:SOURce:TTL4[:LEVel] <b>` / `...?` | Estado da linha digital 4. |
| `:SOURce:VOLTage[:LEVel][:IMMediate][:AMPLitude] <-1000..+1000>` / `...?` | Programa/lê a tensão da fonte. Default 0 V. |
| `:SOURce:VOLTage:RANGe <n>` / `...?` | `<=100` seleciona 100 V; `>100` seleciona 1000 V. Default 100 V. |
| `:SOURce:VOLTage:LIMit[:AMPLitude] <0..1000>` / `...?` | Programa o limite de tensão. Default 1000 V. |
| `:SOURce:VOLTage:LIMit:STATe <b>` / `...?` | Habilita o limite de tensão. Default OFF. |
| `:SOURce:VOLTage:MCONnect <b>` / `...?` | Conecta/desconecta V-source LO ao ammeter LO. Default OFF. |
| `:SOURce:CURRent:RLIMit:STATe <b>` / `...?` | Habilita/desabilita limite resistivo de corrente. Default OFF. |
| `:SOURce:CURRent:LIMit[:STATe]?` | Lê o estado de compliance de corrente. |

Para aplicar alta tensão, em geral configure o nível/range e só então envie `:OUTPut1 ON`. Antes de desligar cabos ou abrir fixtures, envie `:OUTPut1 OFF` e confirme o estado/interlock apropriado.

### 6.8 STATus - registradores e fila de erros

Os seguintes caminhos implementam a mesma família de comandos:

- `:STATus:MEASurement`;
- `:STATus:OPERation`;
- `:STATus:OPERation:ARM`;
- `:STATus:OPERation:ARM:SEQuence`;
- `:STATus:OPERation:TRIGger`;
- `:STATus:QUEStionable`.

Para cada `<path>` acima:

| Comando | Função |
|---|---|
| `<path>[:EVENt]?` | Lê e limpa o event register. |
| `<path>:ENABle <NRf>` / `...?` | Programa/lê a máscara que propaga eventos ao próximo nível. |
| `<path>:PTRansition <NRf>` / `...?` | Programa/lê transições positivas 0->1 que geram evento. |
| `<path>:NTRansition <NRf>` / `...?` | Programa/lê transições negativas 1->0 que geram evento. |
| `<path>:CONDition?` | Lê o estado atual, sem limpar. |

Outros comandos:

| Comando | Função |
|---|---|
| `:STATus:PRESet` | Zera enable e NTR; seta todos os bits PTR; não limpa event registers nem error queue. |
| `:STATus:QUEue[:NEXT]?` | Lê e remove a mensagem mais antiga da fila. Equivale a `:SYSTem:ERRor?`. |
| `:STATus:QUEue:ENABle <list>` / `...?` | Habilita números de mensagens para entrada na fila. |
| `:STATus:QUEue:DISable <list>` / `...?` | Desabilita números de mensagens. |
| `:STATus:QUEue:CLEar` | Limpa a fila de erros. |

Eventos padrão ao ligar: event registers e fila limpos; enable e NTR zerados; PTR com todos os bits habilitados. `*CLS` limpa event registers e fila, mas não enable/PTR/NTR.

### 6.9 SYSTem - utilidades, relógio, zero e trigger básico

| Comando | Função |
|---|---|
| `:SYSTem:PRESet` | Aplica defaults otimizados para operação de painel. |
| `:SYSTem:POSetup <RST/PRESet/SAV0..SAV9>` / `...?` | Seleciona o setup usado no power-on. |
| `:SYSTem:VERSion?` | Retorna a versão SCPI; resposta documentada: `1996.0`. |
| `:SYSTem:ERRor?` | Lê e remove o erro FIFO mais antigo. Fila de 10 mensagens. |
| `:SYSTem:LSYNc:STATe <b>` / `...?` | Sincroniza o início da integração ao próximo zero-crossing da rede. Default OFF. |
| `:SYSTem:KEY <1..31>` / `:SYSTem:KEY?` | Simula uma tecla / lê a última tecla física ou simulada. |
| `:SYSTem:CLEar` | Limpa a fila de erros. |
| `:SYSTem:DATE <year>,<month>,<day>` / `...?` | Relógio: ano 2005..2104, mês 1..12, dia 1..31. |
| `:SYSTem:TIME <hour>,<minute>,<second>` / `...?` | Hora 24 h: 0..23, 0..59, 0.00..59.9. Query retorna centésimos. |
| `:SYSTem:TSTamp:TYPE <RELative/RTClock>` / `...?` | Seleciona timestamp relativo ou relógio de tempo real. |
| `:SYSTem:TSTamp:RELative:RESet` | Zera o timestamp relativo. |
| `:SYSTem:RNUMber:RESet` | Zera o número sequencial de leitura. |
| `:SYSTem:ZCHeck <b>` / `...?` | Controla zero check. |
| `:SYSTem:ZCORrect[:STATe] <b>` / `...?` | Controla zero correct. Default OFF. |
| `:SYSTem:ZCORrect:ACQuire` | Captura o valor de zero correct; exige zero check ON. |
| `:SYSTem:ARSPeed <FAST/NORMal>` / `...?` | Velocidade do autorange. Default `FAST`. |
| `:SYSTem:TSControl <b>` / `...?` | Controla leitura do sensor externo de temperatura. Default ON. |
| `:SYSTem:HSControl <b>` / `...?` | Controla leitura do sensor de umidade. |
| `:SYSTem:HLControl <b>` / `...?` | Controla o limite de hardware do A/D e aviso OutOfLimit. Default OFF. |
| `:SYSTem:MACRo:TRIGger[:EXECute]` | Sai do trigger avançado e seleciona trigger básico. |
| `:SYSTem:MACRo:TRIGger:MODE <CONTinuous/ONEShot>` | Seleciona o modo básico. Default `CONTinuous`. |
| `:SYSTem:MACRo:TRIGger:SOURce <IMMediate/MANual/BUS/EXTernal/TIMer>` | Fonte do trigger básico. Default `IMMediate`. |
| `:SYSTem:MACRo:TRIGger:TIMer <0.001..99999.999>` | Intervalo do timer, em segundos. Default 0.1 s. |
| `:SYSTem:INTerlock?` | `1`: cabo ligado à fixture ou não conectado ao 6517B; `0`: cabo no 6517B mas fixture desconectada/tampa aberta. |

Códigos de `:SYSTem:KEY`:

| Código | Tecla | Código | Tecla |
|---:|---|---:|---|
| 1 | Range Up | 16 | NEXT |
| 2 | V-source Up | 17 | Range Down |
| 3 | Left | 18 | ENTER |
| 4 | MENU | 19 | OPER |
| 5 | Q | 20 | TRIG |
| 6 | FILTER | 21 | RECALL |
| 7 | LOCAL | 22 | I |
| 8 | PREV | 23 | Z-CHK |
| 9 | AUTO | 26 | V-source Down |
| 10 | Right | 27 | SEQ |
| 11 | EXIT | 28 | CONFIG |
| 12 | CARD | 29 | R |
| 13 | MATH | 30 | REL |
| 14 | STORE | 31 | INFO |
| 15 | V |  |  |

### 6.10 TRACe/DATA - buffer

`TRACe` e `DATA` são aliases de raiz neste subsistema. Exemplo: `:TRACe:CLEar` e `:DATA:CLEar` são equivalentes.

| Comando | Função |
|---|---|
| `:TRACe:CLEar` | Limpa as leituras do buffer. |
| `:TRACe:FREE?` | Retorna `<bytes-livres>,<bytes-reservados>`. |
| `:TRACe:POINts <1..50000>` / `...?` | Tamanho do buffer. Default 100; o 6517B aceita até 50.000 pontos. |
| `:TRACe:POINts:AUTO <b>` / `...?` | Auto buffer sizing. |
| `:TRACe:POINts:ACTual?` | Quantidade de leituras atualmente armazenadas. |
| `:TRACe:FEED:PRETrigger:AMOunt[:PERCent] <0..100>` / `...?` | Pretrigger como porcentagem do buffer. |
| `:TRACe:FEED:PRETrigger:AMOunt:READings <n>` / `...?` | Pretrigger como número de leituras. |
| `:TRACe:FEED:PRETrigger:SOURce <EXTernal/TLINk/BUS/MANual>` / `...?` | Evento que encerra o trecho pré-trigger. |
| `:TRACe:FEED:CONTrol <NEVer/NEXT/ALWays/PRETrigger>` / `...?` | Modo/estado de alimentação do buffer. |
| `:TRACe:DATA?` | Retorna todas as leituras do buffer. |
| `:TRACe:LAST?` | Retorna a última leitura gravada pelo teste Alternating Polarity. |
| `:TRACe:TSTamp:FORMat <ABSolute/DELTa>` / `...?` | Formato de timestamp das leituras do buffer. |
| `:TRACe:ELEMents <TSTamp/HUMidity/CHANnel/ETEMperature/VSOurce/NONE>` | Seleciona os elementos auxiliares armazenados. |

Antes de uma nova aquisição, limpe o buffer. Caso contrário, uma aquisição abortada pode deixar leituras antigas misturadas às novas.

### 6.11 INITiate, ABORt, ARM e TRIGger - trigger avançado

#### Iniciação

| Comando | Função |
|---|---|
| `:INITiate[:IMMediate]` | Inicia um ciclo do trigger model. |
| `:INITiate:CONTinuous <b>` / `...?` | Iniciação contínua. `*RST`: OFF; preset: ON. |
| `:INITiate:POFLag <INCLude/EXCLude>` / `...?` | Inclui/exclui a flag no-operation-pending para comandos initiate. Default `INCLude`. |
| `:ABORt` | Reseta o trigger system e leva o instrumento a idle quando continuous está OFF. |

#### ARM Layer 1

Prefixo: `:ARM[:SEQuence[1]][:LAYer[1]]`

| Sufixo | Função |
|---|---|
| `:IMMediate` | Contorna a fonte de controle. |
| `:COUNt <1..99999/INF>` / `?` | Arm count. Default 1. |
| `:SOURce <HOLD/IMMediate/RTCLock/MANual/BUS/TLINk/EXTernal>` / `?` | Fonte de controle. Default `IMMediate`. |
| `:SIGNal` | Força o evento/contorna a fonte. |
| `:TCONfigure:DIRection <SOURce/ACCeptor>` / `?` | Direção do Trigger Link. Default `ACCeptor`. |
| `:TCONfigure:ASYNchronous:ILINe <1..6>` / `?` | Linha de entrada. Default 2. |
| `:TCONfigure:ASYNchronous:OLINe <1..6>` / `?` | Linha de saída. Default 1. |
| `:RTCLock:DATE <year>,<month>,<day>` / `?` | Data do evento do relógio. |
| `:RTCLock:TIME <hour>,<minute>,<second>` / `?` | Hora do evento do relógio. |

#### ARM Layer 2 - scan layer

Prefixo: `:ARM[:SEQuence[1]]:LAYer2`

| Sufixo | Função |
|---|---|
| `:IMMediate` | Contorna a fonte de controle. |
| `:COUNt <1..99999/INF>` / `?` | Scan count. `*RST`: 1; preset: INF. |
| `:DELay <0..999999.999>` / `?` | Delay em segundos. Default 0. |
| `:SOURce <HOLD/IMMediate/TIMer/MANual/BUS/TLINk/EXTernal>` / `?` | Fonte de controle. |
| `:TIMer <0..999999.999>` / `?` | Intervalo do timer. Default 0.1 s. |
| `:SIGNal` | Força o evento/contorna a fonte. |
| `:TCONfigure:DIRection <SOURce/ACCeptor>` / `?` | Direção Trigger Link. Default `ACCeptor`. |
| `:TCONfigure:ASYNchronous:ILINe <1..6>` / `?` | Linha de entrada. Default 2. |
| `:TCONfigure:ASYNchronous:OLINe <1..6>` / `?` | Linha de saída. Default 1. |

#### TRIGger layer - measure layer

Prefixo: `:TRIGger[:SEQuence[1]]`

| Sufixo | Função |
|---|---|
| `:IMMediate` | Contorna a fonte de controle. |
| `:COUNt <1..99999/INF>` / `?` | Quantidade de medições. `*RST`: 1; preset: INF. |
| `:DELay <0..999999.999>` / `?` | Delay em segundos. Default 0. |
| `:SOURce <HOLD/IMMediate/TIMer/MANual/BUS/TLINk/EXTernal>` / `?` | Fonte de controle. Default `IMMediate`. |
| `:TIMer <0..999999.999>` / `?` | Intervalo do timer. Default 0.1 s. |
| `:SIGNal` | Força o evento/contorna a fonte. |
| `:TCONfigure:PROTocol <ASYNchronous/SSYNchronous>` / `?` | Protocolo Trigger Link. Default `ASYNchronous`. |
| `:TCONfigure:DIRection <SOURce/ACCeptor>` / `?` | Direção. Default `ACCeptor`. |
| `:TCONfigure:ASYNchronous:ILINe <1..6>` / `?` | Linha de entrada. Default 2. |
| `:TCONfigure:ASYNchronous:OLINe <1..6>` / `?` | Linha de saída. Default 1. |
| `:TCONfigure:SSYNchronous:LINE <1..6>` / `?` | Linha semi-síncrona. Default 1. |

Quando a fonte for `BUS`, satisfaça o evento com `*TRG` ou GET.

### 6.12 TSEQuence - sequências de teste

#### Controle comum

| Comando | Função |
|---|---|
| `:TSEQuence:ARM` | Arma a sequência selecionada. |
| `:TSEQuence:ABORt` | Interrompe a sequência. |
| `:TSEQuence:TYPE <name>` / `...?` | `DLEakage`, `CLEakage`, `CIResistance`, `RVCoefficient`, `SRESistivity`, `VRESistivity`, `SIResistance`, `SQSWeep`, `STSWeep`, `ALTPolarity`. Default `DLEakage`. |
| `:TSEQuence:TSOurce <MANual/IMMediate/BUS/TLINk/EXTernal/LCLosure>` / `...?` | Fonte de trigger da sequência. Default `MANual`. |
| `:TSEQuence:TLINe <1..6>` / `...?` | Linha Trigger Link. Default 1. |

#### Diode leakage - `:TSEQuence:DLEakage`

| Sufixo | Faixa/default |
|---|---|
| `:STARt <NRf>` / `?` | -1000 a +1000 V; default +1 V. |
| `:STOP <NRf>` / `?` | -1000 a +1000 V; default +10 V. |
| `:STEP <NRf>` / `?` | -1000 a +1000 V; default +1 V. |
| `:MDELay <NRf>` / `?` | 0 a 10000.0 s; default 1 s. |

#### Capacitor leakage - `:TSEQuence:CLEakage`

| Sufixo | Faixa/default |
|---|---|
| `:SVOLtage <NRf>` / `?` | Bias -1000 a +1000 V; default +1 V. |
| `:SPOints <NRf>` / `?` | 1 até o máximo do buffer; default 10. |
| `:SPINterval <NRf>` / `?` | 0 a 99999.9 s; default 1 s. |

#### Cable insulation resistance - `:TSEQuence:CIResistance`

| Sufixo | Faixa/default |
|---|---|
| `:SVOLtage <NRf>` / `?` | Bias -1000 a +1000 V; default +1 V. |
| `:SPOints <NRf>` / `?` | 1 até o máximo do buffer; default 5. |
| `:SPINterval <NRf>` / `?` | 0 a 99999.9 s; default 1 s. |

#### Resistor voltage coefficient - `:TSEQuence:RVCoefficient`

| Sufixo | Faixa/default |
|---|---|
| `:SVOLtage[1] <NRf>` / `?` | Tensão 1: -1000 a +1000 V; default +1 V. |
| `:MDELay[1] <NRf>` / `?` | Delay 1: 0 a 99999.9 s; default 1 s. |
| `:SVOLtage2 <NRf>` / `?` | Tensão 2: -1000 a +1000 V; default +2 V. |
| `:MDELay2 <NRf>` / `?` | Delay 2: 0 a 99999.9 s; default 1 s. |

#### Surface resistivity - `:TSEQuence:SRESistivity`

| Sufixo | Faixa/default |
|---|---|
| `:PDTime <NRf>` / `?` | Pré-descarga 0 a 9999.9 s; default 0.2 s. |
| `:SVOLtage <NRf>` / `?` | Bias -1000 a +1000 V; default +500 V. |
| `:STIME <NRf>` / `?` | Tempo de bias 0 a 99999.9 s; default 1 s. |
| `:MVOLtage <NRf>` / `?` | Tensão de medição -1000 a +1000 V; default +500 V. |
| `:MTIMe <NRf>` / `?` | Tempo de medição 0 a 9999.9 s; default 1 s. |
| `:DTIMe <NRf>` / `?` | Descarga 0 a 99999.9 s; default 2 s. |

#### Volume resistivity - `:TSEQuence:VRESistivity`

| Sufixo | Faixa/default |
|---|---|
| `:PDTime <NRf>` / `?` | Pré-descarga 0 a 99999.9 s; default 10 s. |
| `:SVOLtage <NRf>` / `?` | Bias -1000 a +1000 V; default +500 V. |
| `:STIME <NRf>` / `?` | Tempo de bias 0 a 99999.9 s; default 1 s. |
| `:MVOLtage <NRf>` / `?` | Tensão de medição -1000 a +1000 V; default +500 V. |
| `:MTIMe <NRf>` / `?` | Tempo de medição 0 a 9999.9 s; default 1 s. |
| `:DTIMe <NRf>` / `?` | Descarga 0 a 99999.9 s; default 2 s. |

#### Alternating polarity - `:TSEQuence:ALTPolarity`

| Sufixo | Faixa/default |
|---|---|
| `:OFSVoltage <NRf>` / `?` | Offset -1000 a +1000 V; default 0 V. |
| `:ALTVoltage <NRf>` / `?` | Tensão alternada -1000 a +1000 V; default 10 V. |
| `:MTIMe <NRf>` / `?` | Tempo de medição 0.5 a 9999.9 s; default 15 s. |
| `:DISCard <NRf>` / `?` | Leituras iniciais descartadas, 0 a 9999; default 3. |
| `:READings <NRf>` / `?` | Número de leituras armazenadas; default 1. |

#### Surface insulation resistance - `:TSEQuence:SIResistance`

| Sufixo | Faixa/default |
|---|---|
| `:SVOLtage <NRf>` / `?` | Bias documentado: -1000 a +100 V; default +50 V. |
| `:STIME <NRf>` / `?` | Tempo de bias 0 a 99999.9 s; default 1 s. |
| `:MVOLtage <NRf>` / `?` | Tensão de medição -1000 a +1000 V; default +100 V. |
| `:MTIMe <NRf>` / `?` | Tempo de medição 0 a 9999.9 s; default 1 s. |

#### Square-wave sweep - `:TSEQuence:SQSWeep`

| Sufixo | Faixa/default |
|---|---|
| `:HLEVel <NRf>` / `?` | Nível alto -1000 a +1000 V; default +1 V. |
| `:HTIMe <NRf>` / `?` | Tempo alto 0 a 9999.9 s; default 1 s. |
| `:LLEVel <NRf>` / `?` | Nível baixo -1000 a +1000 V; default -1 V. |
| `:LTIMe <NRf>` / `?` | Tempo baixo 0 a 9999.9 s; default 1 s. |
| `:COUNt <NRf>` / `?` | Número de ciclos. |

#### Staircase sweep - `:TSEQuence:STSWeep`

| Sufixo | Faixa/default |
|---|---|
| `:STARt <NRf>` / `?` | Início -1000 a +1000 V; default +1 V. |
| `:STOP <NRf>` / `?` | Fim -1000 a +1000 V; default +10 V. |
| `:STEP <NRf>` / `?` | Passo -1000 a +1000 V; default +1 V. |
| `:STIME <NRf>` / `?` | Tempo por passo 0 a 9999.9 s; default 1 s. |

### 6.13 UNIT - unidade de temperatura

| Comando | Função |
|---|---|
| `:UNIT:TEMPerature <C/CEL/F/FAR/K>` / `...?` | Seleciona/lê Celsius, Fahrenheit ou kelvin. Default `C`. |

---

## 7. Estrutura de status e SRQ

### 7.1 Standard Event Status Register - ESR/ESE

| Bit | Peso | Evento |
|---:|---:|---|
| 0 | 1 | OPC - Operation Complete |
| 1 | 2 | Não usado |
| 2 | 4 | QYE - Query Error |
| 3 | 8 | DDE - Device-dependent Error |
| 4 | 16 | EXE - Execution Error |
| 5 | 32 | CME - Command Error |
| 6 | 64 | URQ - User Request/LOCAL |
| 7 | 128 | PON - Power On |

`*ESR?` lê e limpa o ESR. `*ESE <mask>` decide quais bits podem levantar ESB no status byte.

### 7.2 Status Byte - STB/SRE

| Bit | Peso | Evento |
|---:|---:|---|
| 0 | 1 | MSB - Measurement Summary Bit |
| 1 | 2 | Não usado |
| 2 | 4 | EAV - Error Available |
| 3 | 8 | QSB - Questionable Summary Bit |
| 4 | 16 | MAV - Message Available |
| 5 | 32 | ESB - Event Summary Bit |
| 6 | 64 | MSS/RQS - Master Summary/Request Service |
| 7 | 128 | OSB - Operation Summary Bit |

`*STB?` não limpa o status byte. O bit 6 não deve ser habilitado em `*SRE`; ele é o resumo das demais fontes habilitadas.

Correção de erro tipográfico do manual: para habilitar ESB (32) e MAV (16), use `*SRE 48`. A referência imprime `*SSE 34` em um exemplo, mas também mostra os pesos 32 e 16 e a soma correta 48.

### 7.3 Measurement Status Register

| Bit | Peso | Evento |
|---:|---:|---|
| 0 | 1 | Reading overflow |
| 1 | 2 | Low Limit 1 |
| 2 | 4 | High Limit 1 |
| 3 | 8 | Low Limit 2 |
| 4 | 16 | High Limit 2 |
| 5 | 32 | Reading available |
| 6 | 64 | Reading underflow |
| 7 | 128 | Buffer available |
| 8 | 256 | Buffer half-full |
| 9 | 512 | Buffer full |
| 10 | 1024 | Sequence reading available |
| 11 | 2048 | Buffer pretriggered |
| 12 | 4096 | Out of limits |
| 13 | 8192 | Fixture lid closed |
| 14 | 16384 | V-source compliance |

Exemplo: habilitar SRQ quando o buffer encher:

```scpi
*CLS
:STATus:MEASurement:ENABle 512
*SRE 1
```

Quando houver SRQ, faça serial poll ou leia `*STB?`, depois confirme com:

```scpi
:STATus:MEASurement:EVENt?
```

A leitura do event register o limpa.

### 7.4 Operation Status Register

| Bit | Peso | Condição/evento |
|---:|---:|---|
| 0 | 1 | Calibrating |
| 5 | 32 | Waiting for trigger |
| 6 | 64 | Waiting for arm |
| 9 | 512 | Calculating |
| 10 | 1024 | Idle |
| 11 | 2048 | Sequence test running |

Nos registradores específicos: `OPERation:TRIGger` usa bit 1 (peso 2) para Sequence 1 na trigger layer; `OPERation:ARM` usa bit 1 para Sequence 1 nas arm layers; `OPERation:ARM:SEQuence` usa bit 1 para Layer 1 e bit 2 (peso 4) para Layer 2.

### 7.5 Questionable Status Register

| Bit | Peso | Condição |
|---:|---:|---|
| 0 | 1 | Medição de tensão inválida |
| 1 | 2 | Medição de corrente inválida |
| 4 | 16 | Temperatura externa inválida |
| 8 | 256 | Calibração inválida |
| 9 | 512 | Umidade inválida |
| 10 | 1024 | Resistência inválida |
| 11 | 2048 | Carga inválida |
| 12 | 4096 | Resultado de sequência inválido |
| 14 | 16384 | Command warning |

---

## 8. Fila de erros

A fila é FIFO e armazena até 10 mensagens. Leia até receber `0, "No error"`:

```scpi
:SYSTem:ERRor?
```

ou:

```scpi
:STATus:QUEue:NEXT?
```

Erros SCPI padronizados usam números negativos; mensagens Keithley usam números positivos. Quando a fila estoura, a última posição recebe `350, "Queue Overflow"`.

Durante desenvolvimento, uma rotina útil após cada bloco de configuração é drenar a fila inteira. Isso encontra rapidamente comandos com caminho, parâmetro ou estado incompatível.

---

## 9. Sequências práticas via GPIB

### 9.1 Inicialização defensiva

```scpi
*CLS
*RST
:SYSTem:ZCHeck ON
:FORMat:DATA ASCii
:FORMat:ELEMents READing,UNITs,STATus
```

### 9.2 Medição one-shot de tensão

```scpi
*CLS
:SENSe:FUNCtion 'VOLTage:DC'
:SENSe:VOLTage:DC:RANGe:AUTO ON
:SENSe:VOLTage:DC:NPLCycles 1
:READ?
```

### 9.3 Medição one-shot de corrente com zero check desligado

```scpi
*CLS
:SENSe:FUNCtion 'CURRent:DC'
:SENSe:CURRent:DC:RANGe:AUTO ON
:SENSe:CURRent:DC:NPLCycles 1
:SYSTem:ZCHeck OFF
:READ?
```

### 9.4 Captura finita no buffer com trigger imediato

```scpi
*CLS
:ABORt
:TRACe:CLEar
:TRACe:POINts 100
:TRACe:FEED:CONTrol NEXT
:ARM:LAYer1:COUNt 1
:ARM:LAYer1:SOURce IMMediate
:ARM:LAYer2:COUNt 1
:ARM:LAYer2:SOURce IMMediate
:TRIGger:COUNt 100
:TRIGger:SOURce IMMediate
:INITiate
*WAI
:TRACe:DATA?
```

### 9.5 Trigger por barramento

```scpi
*CLS
:ABORt
:TRIGger:COUNt 1
:TRIGger:SOURce BUS
:INITiate
*TRG
:FETCh?
```

### 9.6 Programar a fonte de tensão

```scpi
*CLS
:SOURce:VOLTage:RANGe 100
:SOURce:VOLTage 10
:OUTPut1 ON
```

Desligamento:

```scpi
:OUTPut1 OFF
:SOURce:VOLTage 0
```

A fonte do 6517B pode chegar a 1000 V. O software deve iniciar em output OFF, validar interlock/fixture e aplicar limites de tensão antes de habilitar a saída.

---

## 10. Checklist para o software

1. Abrir o recurso VISA no endereço GPIB configurado no painel.
2. Configurar write termination como LF e permitir EOI; leitura deve aceitar LF/EOI.
3. Executar `*IDN?` e rejeitar modelo diferente de 6517B, se isso for requisito do aplicativo.
4. Enviar `*CLS` antes de uma nova operação.
5. Escolher conscientemente entre `*RST` e `:SYSTem:PRESet`, pois os defaults são diferentes.
6. Nunca enviar nova mensagem antes de consumir integralmente a resposta da query anterior.
7. Não usar `*OPC?` com iniciação contínua.
8. Drenar `:SYSTem:ERRor?` após blocos de configuração e em qualquer exceção.
9. Em aquisições longas, preferir SRQ/serial poll a loops agressivos de polling.
10. Ao encerrar ou em falha, executar uma rotina segura: `:OUTPut1 OFF`, `:ABORt` e, se adequado, `:SYSTem:ZCHeck ON`.

---

## 11. Índice rápido por objetivo

| Objetivo | Comando principal |
|---|---|
| Identificar o instrumento | `*IDN?` |
| Limpar status/erros | `*CLS` |
| Restaurar defaults remotos | `*RST` |
| Ler erro mais antigo | `:SYSTem:ERRor?` |
| Selecionar função | `:SENSe:FUNCtion` |
| Obter leitura one-shot | `:READ?` ou `:MEASure?` |
| Obter leitura nova sem reconfigurar | `:SENSe:DATA:FRESh?` |
| Obter última leitura | `:FETCh?` ou `:SENSe:DATA:LATest?` |
| Configurar faixa | `:SENSe:<func>:RANGe` |
| Configurar integração | `:SENSe:<func>:NPLCycles` |
| Configurar formato | `:FORMat` |
| Limpar/configurar/ler buffer | `:TRACe:CLEar`, `:TRACe:POINts`, `:TRACe:DATA?` |
| Iniciar/parar trigger | `:INITiate`, `:ABORt` |
| Trigger GPIB | `*TRG` ou GET |
| Programar tensão | `:SOURce:VOLTage` |
| Habilitar/desabilitar HV | `:OUTPut1 ON/OFF` |
| Verificar compliance | `:SOURce:CURRent:LIMit?` |
| Verificar interlock | `:SYSTem:INTerlock?` |
| Configurar SRQ | `:STATus:...:ENABle`, `*ESE`, `*SRE` |

---

## 12. Observações sobre inconsistências encontradas

- No exemplo de Service Request Enable, o manual imprime `*SSE 34`, mas os próprios pesos listados são ESB = 32 e MAV = 16. O comando e a soma corretos são `*SRE 48`.
- A tabela de `:CALCulate3:...:FAIL?` descreve `1 = pass` e `0 = fail`, apesar do nome `FAIL?`. O software não deve inferir a polaridade pelo nome; confirme no instrumento/firmware usado.
- Em `:SENSe:CHARge:ADIScharge:LEVel`, a tabela imprime uma faixa de -2.2E6 a +2.2E6, enquanto o default é 2E-6 C e o range de carga termina em 2.1E-6 C. Isso indica provável perda do sinal negativo do expoente na faixa; valide a aceitação de +/-2.2E-6 no firmware.
- A tabela-resumo traz alguns erros tipográficos menores de diagramação. Os caminhos deste documento foram normalizados usando as descrições detalhadas da seção 14.
- `:SYSTem:INTerlock? = 1` também ocorre quando o cabo não está conectado ao 6517B; portanto, sozinho, esse valor não comprova que uma fixture está fechada. Use uma política de segurança adequada ao hardware real.
