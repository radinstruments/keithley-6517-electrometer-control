# Relatório de validação — sincronização Keithley 6517

**Data:** 24 de agosto de 2026
**Status:** implementação e validação funcional concluídas na unidade 6517A conectada
**Observação de escopo:** o equipamento encontrado nesta sessão é um **Keithley 6517A**, não um 6517B. A implementação permanece compatível com os perfis 6517A e 6517B, mas os resultados físicos abaixo qualificam somente a unidade 6517A identificada.

## Estado inicial do repositório

- branch: `main`, acompanhando `origin/main`;
- nenhuma modificação rastreada existia antes do trabalho;
- já existiam arquivos não rastreados em `assets/branding` e em `proximos_passos`; eles foram preservados;
- nenhum commit e nenhum push foram realizados.

## Identificação do equipamento

| Item | Valor confirmado |
|---|---|
| Recurso VISA | `GPIB0::27::INSTR` |
| `*IDN?` | `KEITHLEY INSTRUMENTS INC.,MODEL 6517A,0972959,C05  /A02` |
| Modelo | Keithley 6517A |
| Número de série | `0972959` |
| Firmware | `C05 / A02` |
| Versão SCPI | `1994.0` |
| Opções (`*OPT?`) | `0` |

## Teste 1 — conexão observadora e adoção do painel

**Estado inicial:** instrumento previamente configurado pelo operador e fonte de alta tensão em standby.

**Ação:** abrir o recurso pelo único `VisaWorker`, identificar o modelo e ler dois snapshots completos. Em seguida, fechar a sessão.

**Comandos/consultas relevantes:**

- `*IDN?`;
- `:SENSe:FUNCtion?`;
- consultas de faixa, NPLC, dígitos, aperture, Zero Check, Zero Correct, REL, média, mediana e trigger;
- `:OUTPut1:STATe?`.

**Resultado confirmado:**

| Parâmetro | Valor lido |
|---|---:|
| Função | `VOLTage:DC` |
| Autorange | desligado |
| Faixa efetiva/manual | 200 V |
| NPLC | 1 |
| Janela de medição (`APERture?`) | 16,66667 ms |
| Dígitos | 6 |
| Zero Check | ligado |
| Zero Correct | desligado |
| REL | desligado; referência 0 V |
| Filtro digital | ligado |
| Tipo/modo/contagem | `SCALar` / `MOVing` / 10 |
| Janela avançada | 1% |
| Mediana | ligada; rank 1 |
| Pontos do trigger | infinito (`+9.9e37`) |
| Repetição | 0 (ARM count 1) |
| Atraso fonte→medição | 0 s |
| Fonte HV | standby |

**Evidência de não interferência:** trace `log/protocolo_20260824_104946.log` com 45 consultas, zero escritas e fechamento normal do recurso.

## Teste 2 — inicialização automática do programa

**Ação:** iniciar o coordenador funcional sem recurso pré-selecionado.

**Resultado:** o programa encontrou o único recurso VISA, conectou em modo observador, publicou estado `Sincronizado` e preencheu os rascunhos com os valores confirmados. NPLC 1 e janela de 16,66667 ms chegaram ao `ViewState` sem edição local.

**Evidência de não interferência:** trace `log/protocolo_20260824_105102.log` com 24 consultas, zero escritas e fechamento normal.

## Testes automatizados

Comando executado:

```text
python -m unittest discover -s tests -v
```

Resultado final: **53 testes aprovados**.

Cobertura acrescentada:

- conexão observadora sem escrita e sem IFC;
- snapshot mutável após alteração externa simulada;
- conflito preservando rascunho local e valor do instrumento;
- aplicação de um único delta e confirmação por consulta posterior;
- reconexão adotando o estado atual, sem restaurar cache;
- respostas opcionais inválidas ou em timeout sem perder os demais campos do snapshot;
- mudança de faixa efetiva em autorange sem falso conflito;
- interface integrada de leitura e controles na página Medição.

## Teste 3 — aplicação explícita e restauração real de NPLC

**Ação:** com a fonte de alta tensão em standby, solicitar pelo coordenador da aplicação a mudança de NPLC `1 → 2`, aguardar o readback confirmado e então restaurar `2 → 1`, também com readback.

**Resultado:** ambas as aplicações foram aceitas pelo 6517A, `:SYSTem:ERRor?` retornou `0,"No Error"` nas duas operações e o estado final confirmado foi NPLC 1.

**Evidência:** trace `log/protocolo_20260824_105705.log` com 114 consultas e exatamente 2 escritas deliberadas:

```text
:SENSe:VOLTage:DC:NPLCycles 2
:SENSe:VOLTage:DC:NPLCycles 1
```

Nenhum comando de alta tensão foi enviado.

## Teste 4 — controles seguros com restauração integral

**Precondições confirmadas:** função `VOLTage:DC`, Zero Check ligado e fonte HV em standby.

Cada parâmetro abaixo foi alterado, confirmado por snapshot, restaurado ao valor inicial e confirmado novamente:

| Controle | Ciclo validado |
|---|---|
| Dígitos | 6 → 5 → 6 |
| Faixa manual | 200 V → 20 V → 200 V |
| Autorange | desligado → ligado → desligado, com faixa 200 V restaurada |
| Atraso fonte→medição | 0 s → 0,005 s → 0 s |
| Repetição | 0 → 1 → 0 |
| Tipo da média | `SCALar` → `ADVanced` → `SCALar` |
| Modo da média | `MOVing` → `REPeat` → `MOVing` |
| Contagem da média | 10 → 11 → 10 |
| Janela de ruído | 1% → 2% → 1% |
| Filtro digital | ligado → desligado → ligado |
| Rank da mediana | 1 → 2 → 1 |
| Filtro de mediana | ligado → desligado → ligado |

**Resultado:** 12 ciclos aprovados e comparação final sem divergências (`FINAL_MISMATCHES {}`). Zero Check, Zero Correct, REL, função, NPLC e estado da fonte HV também foram comparados ao final e permaneceram iguais aos valores iniciais.

**Evidência:** trace `log/protocolo_20260824_110343.log` com 1.125 consultas, 25 escritas deliberadas, 24 respostas `0,"No Error"`, nenhum erro SCPI e fechamento normal. As 25 escritas correspondem aos 24 passos aplicar/restaurar; a restauração do autorange também reafirma explicitamente a faixa manual de 200 V.

## Teste 5 — filtros pelo caminho completo da interface

**Ação:** usar os mesmos intents acionados pelos controles da página Medição, passando por rascunho, botão Aplicar, driver, `VisaWorker` e readback.

**Ciclos confirmados:**

1. média e mediana desligadas;
2. média ligada em `ADVanced`/`REPeat`, 11 leituras, janela 2%, mediana ligada com rank 2;
3. restauração para `SCALar`/`MOVing`, 10 leituras, janela 1%, mediana ligada com rank 1.

**Resultado:** `APP_FILTER_PATH_OK`; trace `log/protocolo_20260824_111135.log` com 159 consultas, 14 escritas esperadas, três verificações `0,"No Error"` e nenhum erro SCPI. A leitura observadora final confirmou todos os filtros restaurados, `INITiate:CONTinuous` desligado, Zero Check ligado e HV desligada.

**Limite do teste de efeito:** ao iniciar conversões com Zero Check ligado, o 6517A retornou `+9.91E37` com status `Z`, portanto não havia amostras numéricas válidas para comparar desvio padrão com filtros ligados/desligados. O teste foi interrompido sem desligar Zero Check; a sequência de encerramento foi `INITiate:CONTinuous OFF` seguida de `ABORt`, e o estado foi restaurado.

**Matemática CALC1:** o instrumento confirmou `CALCulate:STATe = ON`, formato polinomial e coeficientes identidade `a0=0`, `a1=1`, `a2=0`, sem erro. Esse subsistema `CALCulate/KMATh` não está exposto atualmente na interface; os controles existentes em FILTROS cobrem média digital/avançada e mediana.

## Limitações restantes da bancada

- alteração manual de NPLC no painel e confirmação automática na interface;
- Zero Correct e REL no equipamento físico, respeitando precondições;
- homologação equivalente em um 6517B real.

## Segurança

- nenhum `*RST`, `*CLS`, `ABORt`, `INITiate`, `READ?` ou escrita de configuração foi usado nos testes observadores;
- alta tensão não foi habilitada;
- o painel frontal permaneceu disponível;
- a desconexão não alterou parâmetros preexistentes;
- o programa só desfará automaticamente alta tensão que ele próprio tiver habilitado.

## Estado entregue

A interface foi reaberta e deixada em execução como **Keithley 6517 Control Studio** (PID 14512), conectada automaticamente ao `GPIB0::27::INSTR`. O trace final `log/protocolo_20260824_110504.log` confirmou identificação do 6517A, NPLC 1, consulta do estado HV e 54 consultas com **zero escritas** durante a inicialização.
