@echo off
setlocal EnableExtensions
pushd "%~dp0" || (echo [ERROR] Cannot enter script directory. & pause & exit /b 1)

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

tasklist /FI "IMAGENAME eq genshin-tweak.exe" 2>nul | find /I "genshin-tweak.exe" >nul
if not errorlevel 1 (
    echo [ERROR] genshin-tweak.exe is still running, probably in the tray.
    echo         Exit it first: tray icon -^> Exit and restore hardware.
    pause
    popd
    exit /b 1
)

echo [1/2] Compiling src\*.cs ...
"%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ ^
    /out:genshin-tweak.exe ^
    /win32manifest:src\app.manifest ^
    /win32icon:src\app.ico ^
    /resource:src\app.ico,app.ico ^
    /resource:src\tray_default.ico,tray_default.ico ^
    /resource:src\tray_video.ico,tray_video.ico ^
    /resource:src\tray_work.ico,tray_work.ico ^
    /resource:src\tray_game.ico,tray_game.ico ^
    /resource:src\tray_custom.ico,tray_custom.ico ^
    /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll ^
    src\*.cs
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
popd
exit /b 0
