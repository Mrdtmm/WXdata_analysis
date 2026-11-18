@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :venv

call python -c "import fastapi, uvicorn, yaml" >nul 2>&1
if errorlevel 1 goto :noenv

call python -m app.main
goto :end

:venv
".venv\Scripts\python.exe" -m app.main
goto :end

:noenv
echo.
echo [启动失败] 当前环境无法运行本工具
echo.
echo 当前命令行里的 Python 没有安装本工具需要的依赖，所以无法启动。
echo.
echo 请任选一种方式：
echo   方式一：打开 Anaconda Prompt，执行 conda activate wxdata
echo           然后 cd /d 到本目录，执行 python -m app.main
echo   方式二：在本目录建立 .venv 虚拟环境并安装 requirements.txt
echo.
echo 原因：直接双击时调用的 python 可能是其他软件自带的解释器，
echo       不是本工具的运行环境。
goto :end

:end
echo.
pause
