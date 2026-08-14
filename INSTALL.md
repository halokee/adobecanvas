# 安装与运行

本项目由 FastAPI 后端和 React + Vite 前端组成。推荐在 Windows、macOS 或 Linux 上使用 Python 3.10+ 与 Node.js 18+ 运行。

## 1. 获取代码

```bash
git clone https://github.com/halokee/adobecanvas.git
cd adobecanvas
```

## 2. 准备本地配置

仓库不会包含你的 Cookie、IMS Token、外部 API Key、代理设置或请求日志。首次使用可复制安全的示例配置：

PowerShell：

```powershell
Copy-Item config/config.example.json config/config.json
Copy-Item config/tokens.example.json config/tokens.json
Copy-Item config/refresh_profiles.example.json config/refresh_profiles.json
```

也可以跳过此步骤，在应用启动后通过“设置”页面导入 Cookie 或 Token、配置代理和外部 OpenAI 兼容 API。

## 3. Windows 一键启动

安装 Python 3.10+ 和 Node.js 18+ 后，双击 `start.bat`。脚本会：

1. 安装后端 Python 依赖；
2. 安装前端依赖并构建前端；
3. 启动服务。

启动完成后访问 [http://127.0.0.1:8900](http://127.0.0.1:8900)。

## 4. 手动安装与生产运行

### 后端

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
```

### 前端构建

```bash
cd web
npm ci
npm run build
cd ..
```

### 启动服务

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8900
```

浏览器打开 [http://127.0.0.1:8900](http://127.0.0.1:8900)。按 `Ctrl+C` 停止服务。

## 5. 前端开发模式

先在一个终端启动后端：

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8900
```

再在另一个终端启动 Vite：

```bash
cd web
npm ci
npm run dev
```

开发服务器运行在 [http://127.0.0.1:3000](http://127.0.0.1:3000)，并会将 `/api` 和 `/outputs` 请求转发到后端。

默认服务仅监听本机回环地址，避免局域网或网页跨域读取本地凭证。需要对外提供服务时，应在已配置认证与 HTTPS 的反向代理后运行，不要直接改为监听 `0.0.0.0`。

## 常见问题

- `npm` 或 `python` 未找到：确认已安装 Node.js 18+、Python 3.10+，并重新打开终端。
- `8900` 端口被占用：停止现有服务，或修改 `uvicorn` 命令中的 `--port` 参数；前端开发代理也要同步调整。
- Firefly 无法生成：在应用“设置”中导入有效 Cookie / IMS Token，并检查网络或代理配置。
- 不要提交 `config/config.json`、`config/tokens.json` 或 `config/refresh_profiles.json`；它们可能包含账号凭证。
