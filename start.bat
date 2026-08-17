@echo off
if /I "%~1"==":open_browser" goto :open_browser

setlocal EnableExtensions DisableDelayedExpansion
title Local Canvas

cd /d "%~dp0"
if errorlevel 1 goto :project_not_found
set "ROOT=%CD%"

if not defined LOCAL_CANVAS_PORT set "LOCAL_CANVAS_PORT=8900"
set "APP_URL=http://127.0.0.1:%LOCAL_CANVAS_PORT%"
set "VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe"

echo.
echo ============================================
echo   Local Canvas - Windows launcher
echo ============================================
echo.

if not exist "backend\main.py" goto :project_not_found
if not exist "backend\requirements.txt" goto :project_not_found
if not exist "web\package.json" goto :project_not_found

REM Use only built-in local port checks to keep the launcher simple and transparent.
netstat -ano | findstr /R /C:":%LOCAL_CANVAS_PORT% .*LISTENING" >nul
if not errorlevel 1 goto :port_in_use

if exist "%VENV_PYTHON%" goto :verify_venv

echo [1/4] Creating local Python environment...
set "BOOTSTRAP_PYTHON="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PYTHON=py -3"
if defined BOOTSTRAP_PYTHON goto :create_venv

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :python_not_found
set "BOOTSTRAP_PYTHON=python"

:create_venv
%BOOTSTRAP_PYTHON% -m venv "%ROOT%\.venv"
if errorlevel 1 goto :fail

:verify_venv
"%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto :python_version_error

echo [2/4] Checking Python dependencies...
"%VENV_PYTHON%" -c "import fastapi, uvicorn, requests, pydantic, multipart, socks, aiohttp_socks" >nul 2>nul
if errorlevel 1 goto :install_python_dependencies
if not exist "%ROOT%\.venv\.requirements.txt" goto :install_python_dependencies
fc /b "backend\requirements.txt" "%ROOT%\.venv\.requirements.txt" >nul
if errorlevel 1 goto :install_python_dependencies
"%VENV_PYTHON%" -m pip check >nul 2>nul
if not errorlevel 1 goto :check_node

:install_python_dependencies
echo       Installing Python dependencies...
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :fail
"%VENV_PYTHON%" -m pip install -r "backend\requirements.txt"
if errorlevel 1 goto :fail
copy /y "backend\requirements.txt" "%ROOT%\.venv\.requirements.txt" >nul
if errorlevel 1 goto :fail

:check_node
echo [3/4] Checking Node.js and frontend dependencies...
where node >nul 2>nul
if errorlevel 1 goto :node_not_found
where npm.cmd >nul 2>nul
if not errorlevel 1 goto :use_npm_cmd
where npm >nul 2>nul
if errorlevel 1 goto :npm_not_found
set "NPM=npm"
goto :check_node_version

:use_npm_cmd
set "NPM=npm.cmd"

:check_node_version
node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit((major === 20 && minor >= 19) || (major >= 22 && (major > 22 || minor >= 12)) ? 0 : 1)"
if errorlevel 1 goto :node_version_error

if not exist "web\package-lock.json" goto :install_frontend
if not exist "web\node_modules\.local-canvas-package-lock.json" goto :install_frontend
fc /b "web\package-lock.json" "web\node_modules\.local-canvas-package-lock.json" >nul
if errorlevel 1 goto :install_frontend
goto :build_frontend

:install_frontend
echo       Installing frontend dependencies...
pushd "web"
if errorlevel 1 goto :fail
if not exist "package-lock.json" goto :install_frontend_without_lock
call %NPM% ci
if errorlevel 1 goto :frontend_install_failed
copy /y "package-lock.json" "node_modules\.local-canvas-package-lock.json" >nul
if errorlevel 1 goto :frontend_install_failed
popd
goto :build_frontend

:install_frontend_without_lock
call %NPM% install
if errorlevel 1 goto :frontend_install_failed
if exist "package-lock.json" copy /y "package-lock.json" "node_modules\.local-canvas-package-lock.json" >nul
popd

:build_frontend
echo [4/4] Building frontend...
pushd "web"
if errorlevel 1 goto :fail
call %NPM% run build
if errorlevel 1 goto :frontend_build_failed
popd

echo.
echo Starting Local Canvas at %APP_URL%
echo Keep this window open while the service is running.
echo Press Ctrl+C to stop it.
echo.

if not defined LOCAL_CANVAS_NO_BROWSER start "" /b cmd.exe /d /c call "%~f0" :open_browser "%APP_URL%" "%LOCAL_CANVAS_PORT%"
"%VENV_PYTHON%" -m uvicorn backend.main:app --host 127.0.0.1 --port %LOCAL_CANVAS_PORT%
if errorlevel 1 goto :fail
goto :done

:port_in_use
echo [ERROR] Port %LOCAL_CANVAS_PORT% is already in use by another program.
echo If this is an existing Local Canvas instance, open %APP_URL% in a browser.
echo Otherwise close that program, or choose another port from Command Prompt:
echo   set LOCAL_CANVAS_PORT=8901
echo   start.bat
goto :fail

:python_not_found
echo [ERROR] Python 3.10 or newer was not found.
echo Install Python from https://www.python.org/downloads/ and enable PATH.
goto :fail

:python_version_error
echo [ERROR] The existing .venv does not use Python 3.10 or newer.
echo Delete the .venv folder, install a supported Python version, then run start.bat again.
goto :fail

:node_not_found
echo [ERROR] Node.js was not found.
echo Install Node.js 20.19+ or 22.12+ from https://nodejs.org/.
goto :fail

:npm_not_found
echo [ERROR] npm was not found with Node.js.
echo Reinstall Node.js 20.19+ or 22.12+ and then run start.bat again.
goto :fail

:node_version_error
echo [ERROR] This frontend requires Node.js 20.19+ or 22.12+.
echo Upgrade Node.js from https://nodejs.org/ and then run start.bat again.
goto :fail

:frontend_install_failed
popd
goto :fail

:frontend_build_failed
popd
goto :fail

:project_not_found
echo [ERROR] Run start.bat from the extracted project folder.
goto :fail

:fail
echo.
echo Startup failed. Read the error above, fix it, then run start.bat again.
pause
endlocal
exit /b 1

:done
endlocal
exit /b 0

:open_browser
setlocal EnableExtensions DisableDelayedExpansion
set "APP_URL=%~2"
set "PORT=%~3"

REM Wait only for a local listening port, then use the normal Windows browser
REM association.
for /l %%I in (1,1,30) do (
    netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
    if not errorlevel 1 (
        start "" "%APP_URL%"
        endlocal
        exit /b 0
    )
    ping.exe -n 2 127.0.0.1 >nul
)

endlocal
exit /b 0
