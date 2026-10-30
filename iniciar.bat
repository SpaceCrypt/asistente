@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist "venv\Scripts\python.exe" (
  echo Creando entorno virtual...
  set "PYEXE="
  for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PYEXE=%%D\python.exe"
  )
  if not defined PYEXE (
    where py >nul 2>&1 && set "PYEXE=py -3"
  )
  if not defined PYEXE (
    echo No encontre Python 3. Instala Python desde python.org y vuelve a intentar.
    pause
    exit /b 1
  )
  if /I "!PYEXE!"=="py -3" (
    py -3 -m venv venv
  ) else (
    "!PYEXE!" -m venv venv
  )
)
if not exist "venv\Scripts\python.exe" (
  echo No pude crear el entorno virtual.
  pause
  exit /b 1
)

echo Instalando dependencias...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Fallo la instalacion de dependencias.
  pause
  exit /b 1
)

echo.
echo Asistente personal en http://127.0.0.1:8765
echo Cierra esta ventana para detenerlo.
echo.
"venv\Scripts\python.exe" -m uvicorn back.main:app --host 127.0.0.1 --port 8765
if errorlevel 1 pause
endlocal
