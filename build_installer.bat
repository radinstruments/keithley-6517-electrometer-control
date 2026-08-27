@echo off
setlocal
title Build Keithley 6517 Control Studio Installer
cd /d "%~dp0"

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC (
  echo Inno Setup 6 nao foi encontrado.
  exit /b 1
)
if not exist "dist\Keithley6517ControlStudio.exe" (
  echo O executavel nao foi encontrado. Execute build_exe.bat primeiro.
  exit /b 1
)

echo Construindo instalador...
"%ISCC%" /Qp "packaging\Keithley6517ControlStudio.iss"
if errorlevel 1 (
  echo Falha ao construir o instalador.
  exit /b 1
)

echo Instalador criado em:
echo %cd%\dist\Keithley6517ControlStudio-Setup.exe
endlocal
