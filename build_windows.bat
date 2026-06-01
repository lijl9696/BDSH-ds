@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo [1/5] Preparing Python virtual environment...
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv .venv-win
) else (
  python -m venv .venv-win
)
if errorlevel 1 goto :error

call .venv-win\Scripts\activate.bat
if errorlevel 1 goto :error

echo [2/5] Installing build dependencies...
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :error

echo [3/5] Building Windows application...
python -m PyInstaller packaging\windows\tg_report_tool.spec --noconfirm --clean
if errorlevel 1 goto :error

echo [4/5] Copying fixed config/template directories...
if exist "dist\团购报表工具\配置表" rmdir /s /q "dist\团购报表工具\配置表"
xcopy "配置表" "dist\团购报表工具\配置表" /E /I /Y >nul
if errorlevel 1 goto :error

if not exist "dist\团购报表工具\outputs" mkdir "dist\团购报表工具\outputs"
if not exist "dist\团购报表工具\data" mkdir "dist\团购报表工具\data"

echo [5/5] Done.
echo.
echo Built app folder:
echo   dist\团购报表工具
echo.
echo Give this whole folder to Windows users. They can double-click:
echo   dist\团购报表工具\团购报表工具.exe
echo.
goto :eof

:error
echo.
echo Build failed. Check the error above.
exit /b 1
