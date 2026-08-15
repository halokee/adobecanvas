# 安装与运行

本项目由 FastAPI 后端和 React + Vite 前端组成。推荐在 Windows、macOS 或 Linux 上使用 Python 3.10+ 与 Node.js 20.19+（或 22.12+）运行；推荐 Node.js 22 LTS。

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

### 代理连接方式

启动后在「设置」中选择“代理连接方式”：

| 连接方式 | 路由 |
| --- | --- |
| 关闭代理 | 本机 -> 目标 |
| 仅本地 HTTP 代理 | 本机 -> 本地 HTTP 代理 -> 目标 |
| 本地 HTTP -> SOCKS5 链式连接（推荐） | 本机 -> 本地 HTTP 代理 -> SOCKS5 上游 -> 目标 |
| 直接连接 SOCKS5（高级） | 本机 -> SOCKS5 上游 -> 目标 |

“直接连接 SOCKS5（高级）”只适用于当前网络可以直接访问 SOCKS5 上游的情况。若 711Proxy 必须先通过本地 VPN / HTTP 代理访问，应选择链式模式，而不是直接 SOCKS5。

### 配置本地 HTTP -> SOCKS5 链式代理（推荐）

1. 在“代理连接方式”中选择“本地 HTTP -> SOCKS5 链式连接（推荐）”。
2. 填写本地 VPN 提供的 HTTP 代理，例如 `http://127.0.0.1:7890`。
3. 填写 711Proxy SOCKS5 地址，例如 `socks5://user:pass@host:port`。
4. 保存配置并点击测试。

保存后链路为：

```text
本机 -> 本地 HTTP 代理 -> 711 SOCKS5 -> 目标
```

链式模式会经本地 HTTP 代理 CONNECT 到 711 SOCKS5，不需要开启“直接连接 SOCKS5（高级）”。SOCKS5 地址中的账号密码不会由配置读取接口回显；使用 `socks5h://` 可让 DNS 也通过 SOCKS5 上游解析。用户名或密码中的 `@`、`:`、`/`、`?`、`#`、`%` 必须 URL 编码。

### 链式代理流量统计

设置页显示的“链式代理流量（本次运行）”包括上行、下行、总流量和连接数。它从后端启动时开始统计，后端重启后归零，并且只记录本地 HTTP -> SOCKS5 中继转发到的字节；仅本地 HTTP 与直接 SOCKS5 模式不计入其中。

该统计用于观察本机链路，不能作为 711Proxy 的计费依据。711Proxy 的用量和余额请以其控制台为准。

## 3. Windows 一键启动

安装 Python 3.10+ 和 Node.js 20.19+（推荐 Node.js 22 LTS）后，双击 `start.bat`。如从 GitHub 下载 ZIP，请先完整解压到本地文件夹，并确认文件名是 `start.bat` 而不是 `start.bat.txt`；不要在压缩包预览中直接运行。

脚本会：

1. 在项目目录创建 `.venv` 并安装后端 Python 依赖；
2. 安装前端依赖并构建前端；
3. 启动服务，健康检查通过后自动打开浏览器。

启动完成后会自动打开 [http://127.0.0.1:8900](http://127.0.0.1:8900)。首次下载依赖和构建前端可能需要几分钟，请保持命令窗口打开并等待日志继续输出；运行时按 `Ctrl+C` 停止服务。

若双击后没有看到浏览器或窗口立即关闭，请在项目目录中打开“命令提示符”并运行：

```bat
start.bat
```

脚本会停在错误信息处。优先检查 Python 是否已加入 PATH、Node.js 是否达到所需版本，以及 `8900` 是否被其他程序占用。

## 4. macOS 一键启动

安装 Python 3.10+ 和 Node.js 20.19+（推荐 Node.js 22 LTS）后，在 Finder 中双击 `start.command`。脚本会始终以自身所在的项目目录启动；首次运行会自动创建 `.venv`、安装 Python 与前端依赖、构建前端，并在服务健康检查通过后打开 [http://127.0.0.1:8900](http://127.0.0.1:8900)。

推荐从 GitHub Releases 下载名称包含 `macos` 的发布压缩包；它会保留 `start.command` 的执行权限。通过 `git clone` 获取代码也会保留该权限。若使用 GitHub 的“Source code (zip)”下载后 Finder 提示脚本没有执行权限，在项目目录打开“终端”运行一次：

```bash
chmod +x start.command
```

首次被 macOS Gatekeeper 拦截时，在 `start.command` 上按住 Control 单击，选择“打开”后确认。服务运行期间请保持 Terminal 窗口打开，按 `Ctrl+C` 停止服务；依赖、版本、端口或构建错误都会保留在该窗口中，不会一闪而过。

也可以在终端中手动运行启动脚本：

```bash
./start.command
```

## 5. 手动安装与生产运行

不使用一键脚本时，按以下步骤手动安装并启动。Windows 使用 PowerShell；macOS / Linux 使用 Terminal。

### 后端

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r backend/requirements.txt
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
# Windows PowerShell
.\.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8900

# macOS / Linux
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8900
```

浏览器打开 [http://127.0.0.1:8900](http://127.0.0.1:8900)。按 `Ctrl+C` 停止服务。

## 6. 前端开发模式

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

- `npm` 或 `python` 未找到：确认已安装 Node.js 20.19+（推荐 22 LTS）、Python 3.10+，并重新打开终端。
- `8900` 端口被占用：停止现有服务，或修改 `uvicorn` 命令中的 `--port` 参数；前端开发代理也要同步调整。
- Firefly 无法生成：在应用“设置”中导入有效 Cookie / IMS Token，并检查网络或代理配置。
- 不要提交 `config/config.json`、`config/tokens.json` 或 `config/refresh_profiles.json`；它们可能包含账号凭证。
