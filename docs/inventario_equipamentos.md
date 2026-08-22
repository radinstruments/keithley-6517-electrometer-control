# Inventário de equipamentos

## 1. Interface GPIB-USB

- **Fabricante:** National Instruments
- **Modelo:** GPIB-USB-B
- **Part number:** 188417D-01
- **Número de série:** 10BF816
- **Função:** converter a comunicação USB do computador para a interface GPIB/IEEE-488 do instrumento.
- **Driver utilizado:** NI-488.2 17.6 para Windows.

## 2. Eletrômetro

- **Fabricante:** Keithley Instruments
- **Modelo:** 6517A
- **Tipo:** eletrômetro / medidor de alta resistência.
- **Interface de comunicação:** GPIB/IEEE-488.
- **Driver de instrumento:** Keithley 6517 para LabVIEW.

## 3. Cabos e conexão

- Cabo USB entre o computador e o National Instruments GPIB-USB-B.
- Cabo GPIB entre o GPIB-USB-B e o Keithley 6517A.

### Esquema de ligação

```text
Computador (USB)
        |
        v
National Instruments GPIB-USB-B
        |
        v
Keithley 6517A (GPIB)
```

## 4. Software relacionado

- **NI-488.2 Runtime 17.6** — comunicação com controladores GPIB.
- **NI-488.2 MAX Support 17.6** — configuração e teste pelo Measurement & Automation Explorer (NI MAX).
- **NI-488.2 Utilities 17.6** — utilitários de comunicação GPIB.
- **NI-VISA** — camada de comunicação com instrumentos.
- **Driver Keithley 6517 para LabVIEW** — disponível na pasta `Keithley_6517_LabVIEW` do projeto.

