@echo off
setlocal EnableExtensions
cd /d "%~dp0"

:: ============================================================
:: genshin-tweak build script
:: Uses the csc.exe compiler that ships with every Windows 10/11
:: (.NET Framework 4.x). No SDK, no Visual Studio, no install.
:: ============================================================

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
    echo [ERROR] csc.exe not found. .NET Framework 4.x is required.
    echo         It ships with Windows 10/11 by default.
    pause
    exit /b 1
)

echo [1/2] Compiling src\genshin-tweak.cs ...
"%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ ^
    /out:genshin-tweak.exe ^
    /win32manifest:src\app.manifest ^
    /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll ^
    src\genshin-tweak.cs
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See compiler errors above.
    pause
    exit /b 1
)

echo [2/2] OK - genshin-tweak.exe created.
echo.
echo Run: double-click genshin-tweak.exe (UAC prompt appears, this is expected).
echo.
pause
exit /b 0
