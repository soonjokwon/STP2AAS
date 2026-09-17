@echo off
setlocal enabledelayedexpansion
set "HERE=%~dp0"
title STP2AAS setup

echo ============================================================
echo  STP2AAS setup  -  creates a self-contained env in .\env
echo ============================================================

REM --- locate conda -------------------------------------------------------
set "CONDA="
for %%P in (conda.exe) do set "CONDA=%%~$PATH:P"
if not defined CONDA (
  for %%D in ("%USERPROFILE%\miniforge3" "%LOCALAPPDATA%\miniforge3" "C:\ProgramData\miniforge3" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3") do (
    if exist "%%~D\Scripts\conda.exe" set "CONDA=%%~D\Scripts\conda.exe"
  )
)
if not defined CONDA (
  echo conda not found - installing Miniforge3 via winget...
  winget install --id CondaForge.Miniforge3 --accept-package-agreements --accept-source-agreements --silent
  for %%D in ("%USERPROFILE%\miniforge3" "%LOCALAPPDATA%\miniforge3" "C:\ProgramData\miniforge3") do (
    if exist "%%~D\Scripts\conda.exe" set "CONDA=%%~D\Scripts\conda.exe"
  )
)
if not defined CONDA (
  echo ERROR: conda could not be found or installed. Install Miniforge manually and re-run.
  pause & exit /b 1
)
echo Using conda: !CONDA!

REM --- create / update the in-folder env ----------------------------------
if exist "%HERE%env\python.exe" (
  echo Updating existing .\env ...
  "!CONDA!" env update --prefix "%HERE%env" -f "%HERE%environment.yml" --prune
) else (
  echo Creating .\env  ^(downloads pythonocc/occt/vtk - a few minutes^) ...
  "!CONDA!" env create --prefix "%HERE%env" -f "%HERE%environment.yml"
)
if not exist "%HERE%env\python.exe" (
  echo ERROR: env creation failed.
  pause & exit /b 1
)

echo.
echo Done. Everything now lives in this folder ^(code + .\env^).
echo   run_gui.bat                          launch the GUI
echo   run.bat  input.stp -o out\m.aasx     convert (CLI)
echo   view.bat out\m.aasx --open           build + open the 3D viewer
pause
