# Guia de instalação e comunicação com o Keithley 6517A

## Objetivo

Este guia prepara outro computador para comunicar com o eletrômetro Keithley 6517A usando:

- Interface National Instruments GPIB-USB-B;
- NI-488.2 versão 17.6;
- NI-VISA;
- Python e PyVISA;
- Recurso VISA `GPIB0::27::INSTR`.

O programa realiza somente a consulta `*IDN?`. Ele não inicia medições, não altera a configuração e não lê o buffer do instrumento.

## 1. Sistema operacional

Use preferencialmente Windows 10 de 64 bits, com uma conta de administrador.

O GPIB-USB-B é um equipamento antigo e requer NI-488.2 versão 17.6 ou anterior. Não instale uma versão mais nova do NI-488.2 para esse adaptador.

Referência oficial: [compatibilidade entre GPIB-USB-B, GPIB-USB-HS e GPIB-USB-HS+](https://knowledge.ni.com/KnowledgeArticleDetails?id=kA00Z000000P8kcSAC&l=en-US).

## 2. Copiar os arquivos do projeto

Copie a pasta do projeto para o novo computador. A pasta dos instaladores é:

```text
NI4882_17.6_GPIB_USB_B
```

Ela contém:

```text
palSetup64.msi
palerri64.msi
ni488Runtime64.msi
ni488Utilities64.msi
ni488MaxSupport.msi
```

## 3. Instalar o NI-VISA

Baixe o NI-VISA pelo site oficial da National Instruments:

[Download oficial do NI-VISA](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html)

Instale uma versão compatível com Windows 10. Se estiver disponível, prefira uma versão da mesma geração do NI-488.2 17.6.

Durante a instalação, mantenha habilitados:

- VISA Runtime;
- suporte GPIB;
- NI Measurement & Automation Explorer (NI MAX), se aparecer como opção.

Reinicie o computador se o instalador solicitar.

## 4. Instalar o NI-488.2 17.6

Na pasta `NI4882_17.6_GPIB_USB_B`, execute os instaladores como administrador, nesta ordem:

1. `palSetup64.msi`
2. `palerri64.msi`
3. `ni488Runtime64.msi`
4. `ni488Utilities64.msi`
5. `ni488MaxSupport.msi`

Para cada arquivo: clique com o botão direito, escolha **Executar como administrador** e siga o instalador.

Se aparecer a opção **Repair** ou **Reparar**, escolha essa opção. Evite reiniciar entre os instaladores, exceto se o instalador exigir. Ao terminar todos, reinicie o computador.

## 5. Conectar o equipamento

1. Desligue o Keithley 6517A.
2. Conecte o cabo GPIB entre o Keithley e o NI GPIB-USB-B.
3. Conecte o adaptador GPIB-USB-B ao computador.
4. Ligue o Keithley.
5. Aguarde o Windows reconhecer o adaptador.

O adaptador deve aparecer no Windows como:

```text
NI GPIB-USB-B
```

## 6. Configurar o Keithley 6517A

No painel frontal, configure:

```text
GPIB ADDRESS = 27
GPIB LANGUAGE = SCPI
```

O endereço padrão do 6517A é 27. Mesmo assim, confirme o valor no instrumento.

Na inicialização, o display deve mostrar algo semelhante a:

```text
IEEE Addr=27 SCPI
```

Não selecione DDC para esta aplicação, pois o programa usa o comando SCPI `*IDN?`.

## 7. Verificar no NI MAX

Abra o **NI Measurement & Automation Explorer**.

Em **My System → Devices and Interfaces**, deve aparecer uma interface semelhante a:

```text
GPIB0
```

ou:

```text
GPIB0 (NI GPIB-USB-B)
```

Clique em **Refresh** e execute o teste ou **Scan for Instruments**.

O Keithley deverá ser encontrado no endereço 27. O recurso VISA esperado é:

```text
GPIB0::27::INSTR
```

Se aparecer somente `NI GPIB-USB-B` como dispositivo USB, sem `GPIB0`, o driver NI-488.2 ou o suporte GPIB do NI-VISA não foi configurado corretamente.

## 8. Instalar o Python e PyVISA

Instale Python 3.10 ou superior. Durante a instalação, marque:

```text
Add Python to PATH
```

Abra o PowerShell na pasta do projeto e execute:

```powershell
python --version
python -m pip install -r .\requirements.txt
```

O arquivo `requirements.txt` instala a dependência:

```text
pyvisa>=1.14
```

Não é necessário instalar LabVIEW nem o driver LabVIEW do Keithley para executar esta aplicação Python.

## 9. Testar o VISA antes da aplicação

No PowerShell, execute:

```powershell
python -c "import pyvisa; rm=pyvisa.ResourceManager(); print(rm.list_resources())"
```

O resultado esperado deve conter:

```text
('GPIB0::27::INSTR',)
```

Se o resultado for `()`, o NI-VISA ainda não está encontrando uma interface GPIB.

Se aparecer `VI_ERROR_INTF_NUM_NCONFIG`, a interface `GPIB0` não está configurada. Nesse caso, verifique primeiro o NI MAX, o driver NI-488.2 e a reinicialização do Windows.

## 10. Executar a aplicação

Na pasta do projeto, execute:

```powershell
python .\keithley_6517_comunicacao.py
```

Na janela da aplicação:

1. Clique em **Buscar GPIB**.
2. Selecione `GPIB0::27::INSTR`.
3. Clique em **Conectar e identificar**.

A resposta esperada é semelhante a:

```text
KEITHLEY INSTRUMENTS INC., MODEL 6517A, ...
```

## 11. Diagnóstico rápido

| Resultado | Interpretação |
|---|---|
| Adaptador não aparece no Windows | Problema de USB, cabo ou driver |
| Adaptador aparece como USB, mas não existe `GPIB0` | NI-488.2/GPIB não configurado |
| `GPIB0` existe, mas o endereço 27 não é encontrado | Verificar cabo GPIB, alimentação e endereço do Keithley |
| `GPIB0::27::INSTR` abre, mas não responde | Verificar se o instrumento está em SCPI |
| `*IDN?` retorna identificação Keithley 6517A | Comunicação estabelecida |

## Arquivos principais do projeto

- `keithley_6517_comunicacao.py` — aplicação de comunicação inicial;
- `requirements.txt` — dependência Python PyVISA;
- `NI4882_17.6_GPIB_USB_B` — instaladores locais do NI-488.2;
- `logs` — registros das tentativas de comunicação.

