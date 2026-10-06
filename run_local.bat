@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0"
if errorlevel 1 exit /b 1

rem Keep all installs inside venv; leave existing .env and application data untouched.
if exist "venv\Scripts\python.exe" goto check_venv
call :find_python
if not errorlevel 1 goto create_venv
call :install_tool Python.Python.3.11 --scope user
if errorlevel 1 goto failed
call :find_python
if errorlevel 1 (
    echo [error] Python 3.11+ was not found after installation.
    goto failed
)

:create_venv
echo [setup] Creating venv...
%PYTHON% -m venv venv
if errorlevel 1 goto failed

:check_venv
"venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [error] Existing venv is broken or uses Python older than 3.11.
    echo         Rename venv to keep it, then run this launcher again.
    goto failed
)
echo [setup] Checking Python dependencies...
"venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed

rem Reuse repository-local Node if present, otherwise use the installed toolchain.
if exist ".tools\node\node.exe" set "PATH=%CD%\.tools\node;%PATH%"
call :check_node
if not errorlevel 1 goto check_ffmpeg
call :install_tool OpenJS.NodeJS.LTS
if errorlevel 1 goto failed
call :check_node
if errorlevel 1 (
    echo [error] Node.js/npm compatible with Vite was not found after installation.
    echo         Install Node.js LTS, reopen the terminal, and retry.
    goto failed
)

:check_ffmpeg
call :check_ffmpeg_tools
if not errorlevel 1 goto launch
call :install_tool Gyan.FFmpeg
if errorlevel 1 goto failed
call :check_ffmpeg_tools
if errorlevel 1 (
    echo [error] ffmpeg and ffprobe were not found after installation.
    echo         Add the FFmpeg bin directory to PATH, reopen the terminal, and retry.
    goto failed
)

:launch
echo [start] Starting local API and worker. Stop with Ctrl+C.
"venv\Scripts\python.exe" -u run_local.py %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" if not "%RESULT%"=="130" goto failed
popd
exit /b %RESULT%

:find_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
    exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYTHON=python"
exit /b 0

:check_node
node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit((major === 20 && minor >= 19) || major > 22 || (major === 22 && minor >= 12) ? 0 : 1)" >nul 2>&1
if errorlevel 1 exit /b 1
call npm.cmd --version >nul 2>&1
exit /b %ERRORLEVEL%

:check_ffmpeg_tools
ffmpeg -version >nul 2>&1
if errorlevel 1 exit /b 1
ffprobe -version >nul 2>&1
exit /b %ERRORLEVEL%

:install_tool
where winget.exe >nul 2>&1
if errorlevel 1 (
    echo [error] Missing tool: %1. Automatic installation requires winget.
    echo         Install Microsoft App Installer from Microsoft Store, then retry.
    exit /b 1
)
echo [setup] Installing %1 via winget. Windows may request administrator permission.
winget install --id %1 --exact --source winget --silent --accept-package-agreements --accept-source-agreements --disable-interactivity %2 %3
if errorlevel 1 exit /b 1
rem Installers update the registry PATH, not this running cmd.exe process.
for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -Command "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')"`) do set "PATH=%%P;%PATH%"
exit /b 0

:failed
if not defined RESULT set "RESULT=1"
echo.
echo [error] Local startup failed. See the error above.
rem Keep double-clicked failures visible; CI can set CI=1 to avoid the prompt.
if not defined CI pause
popd
exit /b %RESULT%
