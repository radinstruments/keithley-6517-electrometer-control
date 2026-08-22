# Guia de recuperação — falha de conexão (VI_ERROR_INV_SETUP)

## Sintoma

A aplicação falha ao conectar com o Keithley 6517A/6517B e o log mostra:

```
VI_ERROR_INV_SETUP (-1073807302): Unable to start operation because setup is
invalid (usually due to attributes being set to an inconsistent state).
```

Ao mesmo tempo, `pyvisa.ResourceManager().list_resources()` retorna `()` (vazio),
ou seja, o NI-VISA não enumera nenhum recurso GPIB.

## Causa raiz

O driver NI-488.2 entra num estado ruim — tipicamente quando:

- uma sessão Python anterior terminou abruptamente (crash, kill do processo);
- o cabo GPIB-USB-B foi desconectado/reconectado sem reconhecimento;
- o serviço Windows `ni488enumsvc` (NI-488.2 Enumeration Service) parou.

Quando isso acontece, a única solução simples era reiniciar o PC — o driver
volta ao estado limpo quando o Windows sobe.

## Solução rápida (sem reiniciar o PC)

Execute em PowerShell **como Administrador**:

```powershell
Restart-Service ni488enumsvc -Force
Restart-Service NiSvcLoc -Force
```

Em seguida:

1. Desconecte e reconecte o cabo GPIB-USB-B.
2. Aguarde 5 segundos.
3. Tente conectar na aplicação novamente.

Teste rápido no terminal Python:

```powershell
python -c "import pyvisa; rm = pyvisa.ResourceManager(); print(rm.list_resources())"
```

Se retornar algo como `('GPIB0::27::INSTR',)`, o driver voltou ao normal.

## Prevenção — configurar reinício automático dos serviços NI

Para evitar que o problema se repita e force um reboot, configure o Windows
para reiniciar automaticamente os serviços NI quando eles falharem.

### Uso único (recomendado)

Abra PowerShell **como Administrador** na raiz do projeto e execute:

```powershell
.\configurar_servicos_ni.ps1
```

O script configura:

- `ni488enumsvc` — NI-488.2 Enumeration Service
- `NiSvcLoc` — NI Service Locator

para reiniciar automaticamente após falha (1 min, 2 min e 3 min de atraso
progressivo) e para iniciar com o Windows.

### Configuração manual (alternativa)

```powershell
sc.exe failure ni488enumsvc reset= 86400 actions= restart/60000/restart/120000/restart/180000
sc.exe failure NiSvcLoc reset= 86400 actions= restart/60000/restart/120000/restart/180000
Set-Service -Name ni488enumsvc -StartupType Automatic
Set-Service -Name NiSvcLoc -StartupType Automatic
```

## Detecção automática na aplicação

As aplicações em `src/` (`keithley_6517_comunicacao.py` e
`keithley_6517_controle.py`) detectam quando o NI-VISA não enumera recursos
e registram no log uma mensagem orientando a recuperação:

```
NI-VISA não enumera recursos. Provável causa: serviço ni488enumsvc parado
ou driver GPIB em estado ruim. Tente: Restart-Service ni488enumsvc -Force
e reconecte o cabo GPIB-USB-B.
```

Confira `var/logs/comunicacao_AAAAMMDD.log` ou `var/logs/controle_AAAAMMDD.log`.

## Se nada funcionar

Se mesmo após reiniciar os serviços e reconectar o cabo o erro persistir:

1. Desinstale NI-488.2 e NI-VISA via **Configurações → Aplicativos** ou pelo
   NIUninstaller em `C:\Program Files (x86)\National Instruments\Shared\NIUninstaller\uninst.exe`.
2. Reinicie o PC.
3. Reinstale NI-488.2 e NI-VISA pelos instaladores NI originais.
4. Rode `.\configurar_servicos_ni.ps1` para configurar a recuperação automática.
