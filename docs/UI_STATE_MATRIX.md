# Matriz de estados da interface

| Estado | Permitido | Bloqueado/observações |
|---|---|---|
| Desconectado | modelo esperado, recurso, busca, conexão, tema | medição e instrumento |
| Conectando | aguardar | demais operações |
| Conectado seguro | configuração, console validado, fonte em standby | aquisição antes de configurar |
| Configurado | leitura, aquisição, configuração HV | seleção de modelo |
| Adquirindo | parar e desligar HV | reconfiguração e console |
| HV ativa | desligar HV, aquisição já configurada | reconfiguração da fonte e ativação repetida |
| Erro | desligamento/parada segura e diagnóstico | nova ativação HV |
| Comunicação perdida / HV desconhecida | instrução física e tentativa controlada de standby | nunca mostrar seguro/verde |
| Encerrando | aguardar parada segura | destruir a janela antes do resultado |

## Alta tensão

`DESLIGAR HV AGORA` não pede confirmação e permanece visível no cabeçalho quando a fonte está ativa. Ativação exige três confirmações não persistidas e diálogo com resposta padrão negativa.

## Resultados atrasados

O coordenador mantém identificadores de sessão/operação e snapshots revisionados. A interface renderiza apenas o snapshot mais novo de cada ciclo de `after()`.

