@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "NODE_BIN=%WORKBUDDY_NODE%"
if defined NODE_BIN if exist "%NODE_BIN%" goto validate
set "NODE_BIN="

for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\node\versions\*") do (
  if exist "%%~fD\bin\node.exe" set "NODE_BIN=%%~fD\bin\node.exe"
  if exist "%%~fD\node.exe" set "NODE_BIN=%%~fD\node.exe"
)
if defined NODE_BIN goto validate

for /f "delims=" %%I in ('where node.exe 2^>nul') do if not defined NODE_BIN set "NODE_BIN=%%~fI"
if not defined NODE_BIN (
  >&2 echo research-report-memory requires Node.js 22.16 or newer. Set WORKBUDDY_NODE to node.exe.
  exit /b 1
)

:validate
"%NODE_BIN%" -e "const [a,b]=process.versions.node.split('.').map(Number);process.exit(a^>22^|^|(a===22^&^&b^>=16)?0:1)"
if errorlevel 1 (
  >&2 echo research-report-memory requires Node.js 22.16 or newer. Set WORKBUDDY_NODE to a compatible node.exe.
  exit /b 1
)

"%NODE_BIN%" %*
exit /b %ERRORLEVEL%
