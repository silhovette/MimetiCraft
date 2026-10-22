@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title MimetiCraft v0.3.1 - Windows Launcher

echo ==============================================
echo   MimetiCraft v0.3.1 - ChatGPT login
echo ==============================================
echo.
echo [INFO] Using a project-local Python environment.
echo [INFO] No administrator permission is required.
echo.

rem Prevent inherited Python settings from pointing at other projects.
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
set "PIP_USER="

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating isolated environment in .venv ...
    where py >nul 2>&1
    if errorlevel 1 goto missing_python

    rem Prefer 3.13 / 3.12, but work with 3.14 if it is the only installed version.
    set "PY_LAUNCHER="
    call :try_version 3.13
    if not defined PY_LAUNCHER call :try_version 3.12
    if not defined PY_LAUNCHER call :try_version 3.11
    if not defined PY_LAUNCHER call :try_version 3.14
    if not defined PY_LAUNCHER call :try_version 3.10
    if not defined PY_LAUNCHER goto missing_python

    echo [INFO] Interpreter: !PY_LAUNCHER!
    !PY_LAUNCHER! -m venv ".venv"
    if errorlevel 1 goto venv_failed
) else (
    echo [1/3] Using existing .venv ...
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" goto venv_failed

echo [2/3] Installing dependencies into .venv ...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto install_failed

echo [3/3] Starting graphical app ...
"%VENV_PY%" app.py
if errorlevel 1 goto app_failed
exit /b 0

:missing_python
echo.
echo [ERROR] Compatible Python 3.10+ was not found by the Windows py launcher.
echo Install Python 3.13 from python.org and rerun START_WINDOWS.bat.
pause
exit /b 1

:venv_failed
echo.
echo [ERROR] Could not create the local .venv environment.
echo Check write permission to this extracted project folder.
echo If a broken .venv was partially created, delete only this project's .venv folder, then retry.
pause
exit /b 1

:install_failed
echo.
echo [ERROR] Dependency installation failed INSIDE the project's .venv.
echo Do not modify global site-packages or run as Administrator.
echo If using Python 3.14, try installing Python 3.13 and delete this project's .venv before retrying.
echo Copy the error text above if you need help.
pause
exit /b 1

:app_failed
echo.
echo [ERROR] The GUI failed to start. Copy the error above for diagnosis.
pause
exit /b 1

:try_version
py -%1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_LAUNCHER=py -%1"
exit /b 0
