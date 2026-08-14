@echo off
chcp 65001 >nul
title 本地画布 Local Canvas
cd /d "%~dp0"

echo ============================================
echo   本地画布 Local Canvas - 一键启动
echo ============================================
echo.

REM ---- 1. 检查 Python ----
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM ---- 2. 安装后端依赖 ----
echo [1/3] 检查后端依赖...
python -c "import fastapi, uvicorn, requests, pydantic, multipart" >nul 2>nul
if %errorlevel% neq 0 (
    echo       首次运行，正在安装依赖...
    python -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)

REM ---- 3. 构建前端 ----
echo [2/3] 检查前端...
if not exist "web\dist\index.html" (
    echo       未检测到前端构建产物，正在构建前端...
    cd web
    where npm >nul 2>nul
    if %errorlevel% neq 0 (
        echo [错误] 未找到 npm，请先安装 Node.js 18+
        cd ..
        pause
        exit /b 1
    )
    if not exist "node_modules" (
        call npm ci --registry=https://registry.npmmirror.com
    )
    call npm run build
    cd ..
)

REM ---- 4. 启动后端 ----
echo [3/3] 启动服务...
echo.
echo 浏览器访问: http://localhost:8900
echo 按 Ctrl+C 停止服务
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8900

pause
