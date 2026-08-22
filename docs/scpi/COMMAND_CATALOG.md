# Catálogo de comandos usado pela aplicação

O catálogo executável está em `src/keithley_6517_scpi.py`. Esta página descreve os grupos aceitos pelo console seguro; receitas internas usam builders tipados do driver.

| Grupo | Comandos principais | Modelos | Risco |
|---|---|---|---|
| Identidade | `*IDN?`, `*OPT?`, `:SYST:VERS?` | A/B | leitura |
| Status | `*STB?`, `:SYST:ERR?`, `*CLS` | A/B | `*CLS` altera status |
| Medição | `:SENS:FUNC`, `:RANG`, `:RANG:AUTO`, `:NPLC`, `:DIG` | A/B | configuração |
| Leitura | `:SENS:DATA:FRESH?`, `FETCh?`, `READ?` | A/B | `READ?` atua no trigger |
| Trigger | `ABORt`, `INITiate`, `ARM`, `TRIGger` | A/B | altera execução |
| Buffer | `TRAC:POIN`, `:ACT?`, `:FEED`, `:FEED:CONT`, `:DATA?`, `:TST:FORM` | A/B | configuração/transferência |
| Formato | `FORM:DATA`, `FORM:ELEM`, `TRAC:ELEM` | A/B | contrato de parsing |
| Zero | `SYST:ZCH`, `SYST:ZCOR`, `SENS:CHAR:REF` | A/B | pré-condições obrigatórias |
| Fonte | `SOUR:VOLT`, `:RANG`, `:LIM`, `OUTP1` | A/B | alta tensão |
| Interlock | `SYST:INT?` | A/B | resposta ambígua |

## Regras do console

1. Uma unidade SCPI por transação.
2. Ponto e vírgula é bloqueado.
3. O marcador `?` deve estar no final do cabeçalho.
4. Comandos desconhecidos são rejeitados até terem fonte, parâmetros e teste.
5. Alterações perigosas exigem confirmação ligada ao hash do comando, modelo e sessão.
6. Ativação HV exige também confirmação física; `allow_hv=True` isolado não basta.
7. Toda transação configuradora drena a fila de erros.

## Status de leitura ASCII

| Token | Interpretação |
|---|---|
| `N` | normal |
| `O` | overflow |
| `U` | underflow |
| `Z` | leitura sob zero check; não é amostra válida |
| `R` | valor relativo/referenciado; não é erro por si só |
| `L` | fora dos limites programados |

O parser compara tokens completos. Não procura letras soltas dentro de palavras.

