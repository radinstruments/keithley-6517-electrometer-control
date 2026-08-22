# Segurança da fonte de alta tensão

Este software não substitui barreiras, fixture, interlock externo, procedimentos de laboratório ou treinamento.

## Limites documentados

- Faixa 100 V: saída até ±100 V e corrente nominal até ±10 mA.
- Faixa 1000 V: saída até ±1000 V e corrente nominal até ±1 mA.
- `SOUR:CURR:LIM:STAT?` é um estado de compliance, não uma medição de corrente.

## Ambiguidade do interlock

Nos dois modelos, `SYST:INT? = 1` pode indicar fixture corretamente fechada ou cabo ausente do instrumento. O software usa o estado textual `Fechado OU cabo ausente — indeterminado`.

Ativação exige simultaneamente:

- fonte previamente configurada em standby;
- limite de tensão ativo e coerente;
- resposta do instrumento diferente de bloqueado;
- confirmação física do cabo, fixture e tampa;
- revisão do circuito, área controlada e autorização do operador.

## Estado global

- `DESLIGAR HV AGORA` permanece disponível durante aquisição e console.
- Desligar não pede confirmação.
- Perda de comunicação com estado da fonte desconhecido nunca aparece em verde.
- Checklist e autorização não são persistidos.
- Não existe atalho de teclado para ativar HV.

