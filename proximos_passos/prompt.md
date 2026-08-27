# Prompt — novas funcionalidades e validação com o Keithley 6517B

Trabalhe no repositório:

`D:\RAD\Luan\keithley-6517-electrometer-control`

O Keithley 6517B estará conectado ao computador e o operador estará disponível para testar o painel frontal.

## Objetivo

Implemente na interface as funcionalidades solicitadas pela cliente e valide cada uma diretamente no eletrômetro.

Leia antes de começar:

- `proximos_passos\PLANEJAMENTO_PROXIMOS_PASSOS.md`;
- os dois PDFs presentes em `proximos_passos`;
- o DOCX com as necessidades da reunião;
- os manuais SCPI existentes no projeto;
- o código e os testes atuais.

## Funcionalidades solicitadas

A interface deverá permitir controlar e acompanhar:

1. função de medição;
2. faixa manual ou autorange;
3. faixa efetivamente usada pelo instrumento;
4. resolução e informações de exatidão da faixa;
5. velocidade de aquisição por NPLC, de 0,01 a 10;
6. Zero Check;
7. Zero Correct;
8. REL/referência;
9. filtros digitais e matemáticos;
10. filtro de média, tipo móvel ou repetição e quantidade de amostras;
11. filtro avançado e janela de ruído;
12. filtro de mediana e rank.

Esses controles deverão funcionar pela interface e também acompanhar alterações feitas manualmente no painel do 6517B.

## Mudanças planejadas na interface

Crie uma nova página no menu lateral chamada **Controle avançado**.

Ela deverá aparecer no `navmenu` logo depois de **Medição** e antes de **Aquisição**, usando um ícone coerente com os demais itens. Adicione o novo identificador aos contratos de navegação, o título da página, o botão do menu e a construção/renderização da tela seguindo a arquitetura atual.

Não duplique campos editáveis entre páginas. Mova função, autorange, faixa manual, NPLC e dígitos da página atual para **Controle avançado**. A página **Medição** deverá ficar focada na leitura atual, leitura única, resumo da configuração confirmada e um botão **Abrir Controle avançado**. Todos os campos editáveis e seus rascunhos ficarão em uma única página.

A nova página **Controle avançado** será organizada em quatro áreas.

### 1. Estado do instrumento

Mostrar no início da página:

- status da sincronização;
- horário da última leitura do instrumento;
- função e faixa atualmente confirmadas;
- botão **Ler instrumento agora**;
- indicação clara quando existir uma alteração manual ou conflito.

### 2. Medição

Adicionar controles para:

- função;
- autorange ligado/desligado;
- faixa manual;
- faixa efetiva lida do equipamento;
- NPLC;
- resolução/dígitos;
- resumo da resolução e exatidão correspondente à faixa.

Quando autorange estiver ativo, a faixa manual deverá ficar desabilitada. Mudanças normais da faixa efetiva causadas pelo autorange não deverão ser tratadas como conflito.

### 3. Correções

Criar um card para:

- Zero Check, com estado atual e controle liga/desliga;
- Zero Correct, com estado atual, ação de adquirir correção e controle liga/desliga;
- REL, com estado atual, valor de referência, ação de adquirir e ação de desativar.

Mostrar avisos e precondições antes das ações. Zero Correct e REL não deverão ser adquiridos automaticamente.

### 4. Filtros

Criar um card para:

- filtro digital ligado/desligado;
- tipo de filtro de média;
- média móvel ou por repetição;
- quantidade de leituras;
- janela de ruído do filtro avançado;
- mediana ligada/desligada;
- rank da mediana.

Desabilitar opções que não se aplicam à configuração selecionada.

### Barra de alterações

No final da página, mostrar:

- resumo dos valores modificados;
- botão **Descartar alterações**;
- botão **Adotar valores do instrumento**;
- botão **Aplicar alterações**.

Não aplicar uma configuração apenas porque o usuário alterou um campo. Os comandos deverão ser enviados somente ao pressionar **Aplicar alterações** ou uma ação explícita como **Adquirir Zero Correct**.

Ao sair e retornar à página, os valores confirmados, rascunhos e conflitos deverão permanecer preservados. A navegação não poderá provocar comandos SCPI.

## Sincronização com o painel frontal

O estado real do eletrômetro será a fonte da verdade.

- A conexão inicial deverá apenas identificar e ler o equipamento.
- Não usar `*RST`, `*CLS` nem receitas que alterem o estado durante a conexão observadora.
- Consultar periodicamente os parâmetros sem enviar escritas.
- Se o operador alterar um parâmetro no painel e não houver edição local, atualizar a interface.
- Se houver uma edição local ainda não aplicada, preservar o valor digitado e mostrar o conflito com o valor atual do instrumento.
- Permitir que o operador escolha entre adotar o instrumento ou aplicar sua alteração.
- Depois de qualquer comando enviado pela interface, consultar novamente o parâmetro e mostrar o valor realmente confirmado.
- Depois de uma escrita no console SCPI, atualizar novamente todos os controles relacionados.

Todas as comunicações devem continuar passando pelo único `VisaWorker`.

## Implementação e testes ao vivo

Trabalhe em pequenas etapas:

1. inspecione o Git e preserve alterações existentes;
2. conecte em modo somente leitura;
3. identifique modelo, número de série, firmware e recurso VISA;
4. leia os parâmetros atuais e compare com o painel;
5. implemente uma área da interface por vez;
6. execute os testes automatizados;
7. abra a interface e valide no equipamento;
8. peça ao operador para alterar um único parâmetro no painel;
9. confirme que a interface acompanhou a alteração sem escrever de volta;
10. teste o controle do mesmo parâmetro pela interface;
11. confirme a resposta no painel e por consulta SCPI;
12. registre o resultado antes de avançar.

Ordem sugerida:

1. NPLC e resolução;
2. autorange e faixa manual;
3. filtros;
4. REL;
5. Zero Check;
6. Zero Correct;
7. troca de função;
8. comportamento durante aquisição.

Amplie o simulador/fake VISA e crie testes para alterações externas, conflitos, confirmação após escrita, timeout, respostas inválidas e reconexão.

## Segurança

- Não bloqueie o painel frontal.
- Não habilite alta tensão durante esses testes sem solicitação e confirmação explícitas do operador.
- Não altere automaticamente um estado que já estava configurado no equipamento.
- Se ocorrer erro, timeout ou resposta inesperada, interrompa novas escritas e faça o diagnóstico somente por consultas.
- A aplicação só deverá desfazer automaticamente estados perigosos que ela própria habilitou.

## Registro do trabalho

Crie durante os testes:

`proximos_passos\RELATORIO_VALIDACAO_6517B.md`

Registre nele:

- identificação do equipamento;
- funcionalidade testada;
- estado inicial;
- ação feita no painel ou na interface;
- comandos e respostas SCPI;
- resultado na interface e no instrumento;
- falhas, correções e testes automatizados;
- confirmação de que não houve escrita inesperada.

Não faça commit nem push sem solicitação explícita.

Comece apresentando o estado do Git, o equipamento encontrado e os valores atuais lidos em modo somente leitura. Depois mostre um plano curto das mudanças visuais antes de editar a interface.
