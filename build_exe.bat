@echo off
setlocal
title Build Keithley 6517 Control Studio
cd /d "%~dp0"

echo Construindo Keithley 6517 Control Studio...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "Keithley6517ControlStudio" ^
  --icon "assets\branding\keithley_6517_spectrum_icon.ico" ^
  --paths "." ^
  --add-data "assets;assets" ^
  --collect-all customtkinter ^
  --collect-data matplotlib ^
  "packaging\entrypoint.py"

if errorlevel 1 (
  echo.
  echo Falha ao construir o executavel.
  pause
  exit /b 1
)

echo.
echo Executavel criado em:
echo %cd%\dist\Keithley6517ControlStudio.exe
endlocal
