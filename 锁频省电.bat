@echo off
chcp 65001 >nul
title 启动 BetterGI 锁频省电工具

:: 1. 自动以管理员身份重新运行
>nul 2>&1 "%SYSTEMROOT%\system32\cacls.exe" "%SYSTEMROOT%\system32\config\system"
if '%errorlevel%' NEQ '0' (
    goto UACPrompt
) else ( goto gotAdmin )

:UACPrompt
    echo Set UAC = CreateObject^("Shell.Application"^) > "%temp%\getadmin.vbs"
    echo UAC.ShellExecute "%~s0", "", "", "runas", 1 >> "%temp%\getadmin.vbs"
    "%temp%\getadmin.vbs"
    del "%temp%\getadmin.vbs"
    exit /B

:gotAdmin
    pushd "%CD%"
    CD /D "%~dp0"

:: 2. 优先无黑框启动 pythonw，无则用 python
where pythonw >nul 2>nul
if %errorlevel% equ 0 (
    start "" pythonw "%~dp0EcoGenshinOptimizer.py"
) else (
    start "" python "%~dp0EcoGenshinOptimizer.py"
)

exit