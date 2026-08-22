# Fontes normativas SCPI

Última revisão da pesquisa: 8 de agosto de 2026.

Somente documentos do fabricante são fontes normativas para comandos e limites. Textos consolidados do projeto são auxiliares e devem apontar para uma destas fontes.

| Modelo | Documento | Revisão | Uso |
|---|---|---:|---|
| 6517A | [Model 6517A Electrometer User's Manual](https://download.tek.com/manual/6517A_900_01C.pdf) | Rev. C | Comandos, trigger, FORMAT, TRACE, SYSTEM e segurança |
| 6517B | [Model 6517B Electrometer Reference Manual](https://download.tek.com/manual/6517B-901-01F_Jan2024_Ref.pdf) | Rev. F, jan. 2024 | Referência atual de comandos e operação |
| 6517B | [Model 6517B Electrometer User's Manual](https://download.tek.com/manual/6517B-900-01C_Jan2024_User.pdf) | Rev. C, jan. 2024 | Operação, segurança e tabela de eventos |
| 6517B | [Central de downloads](https://www.tek.com/en/support/datasheets-manuals-software-downloads?model=6517B) | catálogo atual | Revisões vigentes, datasheet e firmware |

## Política de rastreabilidade

Cada comando em `keithley_6517_scpi.py` deve registrar:

- modelos suportados;
- tipo write/query;
- parâmetros, unidade e domínio;
- esquema de resposta;
- estados permitidos e risco HV;
- documento, revisão, seção e página impressa;
- estado de validação: `DOCUMENTED`, `FAKE_VALIDATED` ou `HARDWARE_VALIDATED`.

Uma revisão de firmware não deve ser tratada como compatível apenas pelo nome do modelo. O `*IDN?` bruto, número de série, firmware e `:SYST:VERS?` devem constar do relatório de qualificação.

