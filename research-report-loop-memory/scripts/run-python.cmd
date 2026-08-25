@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "PYTHON_BIN=%WORKBUDDY_PYTHON%"
if defined PYTHON_BIN if exist "%PYTHON_BIN%" goto validate
set "PYTHON_BIN="

for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
  if exist "%%~fD\bin\python.exe" set "PYTHON_BIN=%%~fD\bin\python.exe"
  if exist "%%~fD\python.exe" set "PYTHON_BIN=%%~fD\python.exe"
)
if defined PYTHON_BIN goto validate

for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PYTHON_BIN set "PYTHON_BIN=%%~fI"
if not defined PYTHON_BIN (
  >&2 echo research-report-loop requires Python 3.10 or newer. Set WORKBUDDY_PYTHON to python.exe.
  exit /b 1
)

:validate
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%PYTHON_BIN%" -c "import sys;raise SystemExit(0 if max((3,10),sys.version_info[:2])==sys.version_info[:2] else 1)"
if errorlevel 1 (
  >&2 echo research-report-loop requires Python 3.10 or newer. Set WORKBUDDY_PYTHON to a compatible python.exe.
  exit /b 1
)

"%PYTHON_BIN%" %*
exit /b %ERRORLEVEL%
