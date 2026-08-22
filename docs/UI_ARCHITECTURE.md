# Arquitetura da interface

## Limite da camada visual

`src/keithley_6517_ui.py` é o único módulo que importa CustomTkinter. Ele contém widgets, layout, temas, navegação, diálogos, tabela e gráfico. Não importa PyVISA, driver, `threading`, `queue` ou armazenamento.

Os módulos funcionais são:

- `keithley_6517_application.py`: intents, tarefas assíncronas e `ViewState`;
- `keithley_6517_contracts.py`: contratos imutáveis;
- `keithley_6517_profiles.py`: capacidades por modelo;
- `keithley_6517_scpi.py`: catálogo e pré-análise;
- `keithley_6517_acquisition.py`: LIVE/BUFFER e cancelamento;
- `keithley_6517_storage.py`: CSV, preferências e logs;
- `keithley_6517_driver.py`: autoridade VISA/SCPI e máquina de estados;
- `main.py`: composition root.

```text
CustomTkinter/main thread
  └─ AppIntent
      └─ application coordinator
          ├─ acquisition/storage
          └─ KeithleyController
              └─ VisaWorker FIFO único

workers
  └─ ViewState imutável
      └─ fila limitada/coalescida
          └─ root.after()
              └─ renderização visual
```

## Navmenu lateral esquerdo

O menu é a navegação primária e permanente:

- largura fixa de 56 px, no padrão de uma Activity Bar compacta;
- indicador vertical de 3 px para a página ativa;
- botões com caixa uniforme de 48 × 44 px e glifo de 22 × 22 px;
- ícones sempre centralizados no mesmo eixo, sem modo expandido;
- tooltip textual após 450 ms para preservar descoberta e acessibilidade;
- Configurações ancorada no rodapé e demais páginas no agrupamento principal;
- páginas Painel, Conexão, Medição, Aquisição, Alta tensão, Console SCPI, Registros e Configurações.

O menu ocupa o lado esquerdo da janela; todo conteúdo operacional fica à direita. Os glifos são SVGs selecionados do conjunto oficial Microsoft VS Code Codicons e redistribuídos sob CC BY 4.0. Os SVGs-fonte, a atribuição e as versões PNG de alta densidade usadas pelo CustomTkinter ficam em `assets/icons/codicons/`. Nenhuma marca ou logotipo da Microsoft é usado como identidade do aplicativo.

## Desempenho

- atualização visual limitada a aproximadamente 20 Hz;
- fila de snapshots limitada e coalescida;
- últimas 2.000 leituras mantidas para tabela/gráfico;
- CSV recebe todas as amostras;
- um único `CTk` e um único `mainloop`;
- páginas visuais são construídas uma vez, mas somente a página ativa permanece
  mapeada; a troca usa `grid_remove()`/`grid()` dentro da transação de redesenho,
  impedindo sobreposição de widgets nativos entre duas páginas;
- a navegação não usa animação, captura, máscara, espera ou `update_idletasks`:
  todas as alterações são enfileiradas no mesmo evento e entregues juntas ao
  loop gráfico, mantendo os botões imediatamente responsivos;
- a página realmente visível é rastreada separadamente do estado assíncrono, de
  modo que a confirmação do coordenador não repete a mesma transição;
- nenhuma operação VISA bloqueia a thread gráfica.
