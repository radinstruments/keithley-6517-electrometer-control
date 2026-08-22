# Design system

## Estrutura

- activity bar lateral: 56 px fixa;
- botão de navegação: 48 × 44 px; glifo: 22 × 22 px;
- indicador ativo: 3 × 28 px;
- cabeçalho: 56 px;
- barra de status: 28 px;
- controles: mínimo 36 px;
- ações HV: mínimo 48 px;
- espaçamento: 4, 8, 12, 16, 24 e 32 px;
- margens horizontais de cartões: 24 px; vão entre cartões paralelos: 12 px;
- raio: 4–6 px;
- fonte: Segoe UI; dados e SCPI: Cascadia Mono/Consolas.

## Cores

| Token | Claro | Escuro |
|---|---|---|
| Janela | `#E8EDF2` | `#181818` |
| Workspace | `#F2F5F8` | `#1E1E1E` |
| Sidebar | `#E5EAF0` | `#252526` |
| Superfície | `#FFFFFF` | `#252526` |
| Hover | `#D7DFE8` | `#2A2D2E` |
| Seleção | `#CFE6F7` | `#37373D` |
| Borda | `#B7C1CC` | `#3C3C3C` |
| Texto | `#17212B` | `#CCCCCC` |
| Texto secundário | `#4D5C6A` | `#A6A6A6` |
| Acento | `#0067B8` | `#007ACC` |
| Perigo | `#B42318` | `#F14C4C` |
| Aviso | `#754B00` | `#CCA700` |
| Sucesso | `#087A55` | `#4EC9B0` |

## Regras

- nenhum estado importante é comunicado somente por cor;
- no tema claro, texto primário mantém contraste mínimo de 14,8:1 e texto
  secundário de 6,2:1 contra as superfícies previstas;
- cartões brancos nunca são colocados sobre workspace branco: fundo, superfície
  e borda devem permanecer visualmente distintos;
- status HV sempre contém texto;
- Treeview e gráfico recebem tema explicitamente;
- ícones monocromáticos possuem variantes normal/ativa para claro e escuro;
- navegação somente por ícone sempre possui tooltip textual após 450 ms;
- mínimo funcional em telas compactas: 900×560, com conteúdo rolável;
- alvo 1440×900;
- tamanho inicial respeita a área útil do Windows e nunca cobre a barra de tarefas;
- suportar escala de 100%, 125%, 150% e 200%;
- diálogos perigosos têm opção negativa como padrão.
