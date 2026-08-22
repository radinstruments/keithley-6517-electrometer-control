# Protocolo de verificação SCPI

Não se declara “zero erros SCPI” apenas por revisão textual. Cada receita avança por níveis verificáveis.

## Nível 1 — documental

- comando presente no manual oficial;
- revisão, seção e página registradas;
- parâmetros, unidades, limites e resposta definidos;
- classificação de risco revisada.

Resultado: `DOCUMENTED`.

## Nível 2 — fake estrito

- fake específico para 6517A ou 6517B;
- comandos desconhecidos retornam erro, nunca resposta genérica `0`;
- sequência e estados verificados;
- fila de erros, respostas malformadas e timeouts cobertos;
- `FORMat`/`TRACe` confirmados por query-back;
- mensagens compostas e modelo divergente rejeitados.

Resultado: `FAKE_VALIDATED`.

## Nível 3 — bancada sem HV

Registrar antes do ensaio:

- `*IDN?` bruto;
- modelo, série e firmware;
- `:SYST:VERS?`;
- opções instaladas;
- interface, endereço e versões VISA/NI-488.2.

Executar identidade, status, configuração de medição, leitura única, LIVE curto e buffers progressivos. Depois de cada receita, exigir fila de erros vazia. Não energizar a fonte.

## Nível 4 — bancada controlada com fonte

Somente após análise de risco e autorização específicas. Usar carga apropriada, fixture, barreiras e instrumento de verificação independente. Testar primeiro níveis baixos na faixa 100 V. A qualificação deve registrar todos os readbacks e estados de compliance/interlock.

Resultado por combinação exata de modelo e firmware: `HARDWARE_VALIDATED`.

## Critério de aprovação

- nenhuma resposta órfã;
- nenhuma ocorrência de `-410`;
- nenhuma entrada não zero na fila após receita aprovada;
- readbacks dentro da tolerância definida;
- saída em standby e nível zero ao terminar;
- sessão incompatível fechada antes da configuração;
- relatório assinado com a matriz de hardware/firmware realmente testada.

