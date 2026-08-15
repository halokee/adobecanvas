# 本地画布 Local Canvas

一款本地运行的**无限画布 + AI 创作**工具，参照 [infinite-canvas](https://github.com/basketikun/infinite-canvas) 的画布交互设计，内置 Adobe Firefly 调用通道，并**兼容 [adobe2api](https://github.com/leik1000/adobe2api) 的 cookie / token / 配置文件格式**。

![stack](https://img.shields.io/badge/Backend-FastAPI-blue) ![stack](https://img.shields.io/badge/Frontend-React%2BVite-blue)

## 功能特性

- **无限画布**：节点拖拽、滚轮缩放、平移导航、贝塞尔连线、小地图跳转
- **节点类型**：文生图 / 图生图、视频生成、图片/视频素材（本地上传）、文本便签
- **画布工作流**：节点连线自动串联（上游生成结果自动作为下游参考图 / 首帧）
- **撤销 / 重做**（Ctrl+Z / Ctrl+Y）、复制节点（Ctrl+D）、导入导出画布 JSON
- **双生成通道**：
  - 🔥 内置 Firefly：导入 cookie 后直接调用 Adobe Firefly（无需部署 adobe2api）
  - 🔌 外部 OpenAI 兼容 API：配置 `Base URL + Key` 即可转发
- **Cookie / Token 管理**：兼容 adobe2api 的 `config/config.json` 与 `config/tokens.json`
- **OpenAI 兼容 API**：内置 `/v1/models`、`/v1/images/generations`、`/v1/videos/generations`、`/v1/chat/completions` 端点

## 与 adobe2api 的兼容性

| 项目 | adobe2api | 本地画布 |
| --- | --- | --- |
| `config/config.json` | ✅ | ✅ 完全兼容（字段超集） |
| `config/tokens.json` | ✅ | ✅ 完全兼容（字段一致） |
| Cookie 导入格式 | 字符串 / `{cookie:...}` / 对象数组 | ✅ 全部支持 |
| Cookie → IMS Token 刷新 | `ims/check/v6/token` + `client_id=clio-playground-web` | ✅ 同端点同参数 |
| Firefly 3P API | `firefly-3p.ff.adobe.io` + x-nonce 签名 | ✅ 同端点同签名 |

可直接把已有 adobe2api 的 `config/` 目录复制到本项目根目录即可复用全部 token。

## 快速开始

完整的安装、配置与开发运行说明见 [INSTALL.md](INSTALL.md)。

### 方式一：一键启动（Windows）

先安装 Python 3.10+ 与 Node.js 20.19+（推荐 Node.js 22 LTS）。从 GitHub 下载 ZIP 时，请先完整解压到本地文件夹，确认文件名是 `start.bat` 而不是 `start.bat.txt`，再双击它；不要在压缩包预览中直接运行。

脚本会在项目目录创建 `.venv`、安装缺失的 Python/前端依赖、构建前端，并在服务健康检查通过后自动打开 `http://127.0.0.1:8900`。首次下载依赖和构建可能需要几分钟，请保持命令窗口打开并等待日志继续输出；运行时按 `Ctrl+C` 停止服务。

若双击后没有看到浏览器或窗口立即关闭，请在项目目录的命令提示符中运行：

```bat
start.bat
```

新版脚本会保留错误窗口并显示具体原因。常见原因是 Python 未加入 PATH、Node.js 版本过低，或 `8900` 端口已被其他程序占用。

### 方式二：一键启动（macOS）

安装 Python 3.10+ 与 Node.js 20.19+（推荐 Node.js 22 LTS）后，在 Finder 中双击 `start.command`。它会自动创建 `.venv`、安装 Python/前端依赖、构建前端，并在服务就绪后打开 `http://127.0.0.1:8900`。

推荐从 GitHub Releases 下载名称包含 `macos` 的发布压缩包；它会保留 `start.command` 的执行权限。通过 `git clone` 获取代码也会保留该权限。若使用 GitHub 的“Source code (zip)”下载后 Finder 提示脚本没有执行权限，在项目目录打开“终端”运行一次：

```bash
chmod +x start.command
```

首次被 macOS Gatekeeper 拦截时，在 `start.command` 上按住 Control 单击并选择“打开”。服务运行期间 Terminal 会保持打开；依赖、版本、端口或构建异常都会显示在窗口中，不会一闪而过。需要从终端启动时运行：

```bash
./start.command
```

### 方式三：手动启动

不使用一键脚本时，可手动安装依赖、构建前端并启动后端。

Windows PowerShell：

终端 1，启动后端：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend/requirements.txt
.\.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 8900
```

终端 2，前端开发模式：

```powershell
cd web
npm ci
npm run dev # http://localhost:3000
```

生产方式先构建前端，再运行终端 1 中的后端命令：

```powershell
cd web
npm ci
npm run build
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt

cd web
npm ci
npm run build
cd ..

.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8900
```

## 使用指南

### 1. 导入 Cookie（Firefly 通道）

进入「设置」→ 粘贴浏览器插件导出的 cookie，支持以下任意格式：

```
# 字符串
adobe_token=xxx; deployment_id=yyy; ...

# 简单对象
{"cookie": "adobe_token=xxx; ..."}

# 浏览器插件标准导出（对象数组）
[{"name": "adobe_token", "value": "xxx"}, ...]
```

导入成功后自动通过 Adobe ID 端点换取 IMS Token 并加入 token 池，token 池状态可在「设置」中查看。

> 如何获取 cookie：在浏览器登录 [firefly.adobe.com](https://firefly.adobe.com)，用 Cookie 导出插件（如 "EditThisCookie" / "Cookie-Editor"）导出当前域名 cookie。

### 2. 添加 Token（手动）

也可直接粘贴已获得的 Adobe IMS `access_token` 添加进 token 池（格式与 adobe2api 的 `tokens.json` 相同）。

### 3. 在画布上生成

1. 点击「＋ 添加节点」选择 **文生图 / 图生图** 或 **视频生成**
2. 输入 prompt，选择模型与参数，点击生成
3. 结果图片/视频显示在节点中，输出端口可连线到下游节点作为参考图 / 首帧

### 4. 画布操作

| 操作 | 方式 |
| --- | --- |
| 平移 | 鼠标拖拽空白处 |
| 缩放 | 滚轮 |
| 选择节点 | 点击 |
| 连线 | 从节点右侧输出端口拖到目标节点顶部输入端口 |
| 删除 | 选中后按 `Delete`（边 / 节点均可） |
| 复制 | 选中后 `Ctrl+D` |
| 撤销 / 重做 | `Ctrl+Z` / `Ctrl+Y` |
| 导入 / 导出 | 工具栏按钮，或 `Ctrl+O` / `Ctrl+S` |

## 配置说明（config/config.json）

```jsonc
{
  "api_key": "",                  // Firefly x-api-key（默认 clio-playground-web）
  "use_proxy": false,              // 兼容字段：仅本地 HTTP 或链式连接时为 true
  "proxy": "",                     // 本地 HTTP 代理，如 http://127.0.0.1:7890
  "use_socks5_proxy": false,       // 兼容字段：直接连接 SOCKS5（高级）时为 true
  "socks5_proxy": "",              // SOCKS5 上游，如 socks5://user:pass@host:port
  "use_socks5_proxy_chain": false, // 兼容字段：本地 HTTP -> SOCKS5 链式连接时为 true
  "gpt_image_quality": "medium",  // 默认图片质量 low|medium|high
  "generate_timeout": 300,        // 生成超时（秒）
  "token_rotation_strategy": "round_robin",  // 轮换策略 round_robin|random
  "external_base_url": "",        // 外部 OpenAI 兼容 API Base URL
  "external_api_key": "",         // 外部 API Key
  "default_channel": "firefly"    // 默认通道 firefly|external
}
```

### 代理连接方式

在「设置」中选择一种代理连接方式。界面会把选择映射为兼容旧配置的三个开关；`config/config.json` 不会持久化 `proxy_mode` 字段。

| 连接方式 | 实际链路 | 适用场景 | 兼容字段组合 |
| --- | --- | --- | --- |
| 关闭代理 | 本机 -> 目标 | 不需要代理 | 三个开关均为 `false` |
| 仅本地 HTTP 代理 | 本机 -> 本地 HTTP 代理 -> 目标 | 只需要本地 VPN / HTTP 代理 | `use_proxy=true` |
| 本地 HTTP -> SOCKS5 链式连接（推荐） | 本机 -> 本地 HTTP 代理 -> SOCKS5 上游 -> 目标 | SOCKS5 上游必须先通过本地 VPN / HTTP 代理访问，例如 711Proxy | `use_proxy=true`、`use_socks5_proxy_chain=true` |
| 直接连接 SOCKS5（高级） | 本机 -> SOCKS5 上游 -> 目标 | 当前网络能直接访问 SOCKS5 上游 | `use_socks5_proxy=true` |

链式模式需要同时填写本地 HTTP 代理和 SOCKS5 上游地址。例如：

```text
本机 -> http://127.0.0.1:7890 -> socks5://user:pass@711-host:port -> 目标服务
```

后端会创建一个仅监听 `127.0.0.1` 的临时 SOCKS5 中继：先经本地 HTTP 代理使用 CONNECT 连接上游，再透明转发 SOCKS5 认证和目标请求。选择链式模式时不需要、也不会启用“直接连接 SOCKS5（高级）”；后者只保留给无需本地 HTTP 第一跳的网络环境。

SOCKS5 上游地址格式为：

```text
socks5://user:pass@host:port
```

若希望 DNS 也由 SOCKS5 上游解析，可使用 `socks5h://`。用户名或密码中含有 `@`、`:`、`/`、`?`、`#` 或 `%` 时，需要进行 URL 百分号编码。SOCKS5 地址含有账号密码，配置读取接口不会回显该地址。

### 链式代理流量统计

选择链式模式后，「设置」会显示“链式代理流量（本次运行）”，包括上行、下行、总流量、累计连接数和当前活跃连接数。计数从后端启动时开始，并在后端重启后归零；仅统计本地 HTTP -> SOCKS5 中继实际转发到的字节，直接 SOCKS5 和仅本地 HTTP 模式不会产生这组统计。

该数字用于本地运行观测，不是 711Proxy 的计费或余额数据。服务商按其自身规则统计，可能与本地中继观测值不同，因此请以 711Proxy 控制台为准。

## OpenAI 兼容 API

本地画布自带 OpenAI 兼容网关（`/v1/*`），任何支持 OpenAI 接口的客户端都可直接对接：

```bash
# 模型列表
curl http://127.0.0.1:8900/v1/models

# 文生图（同步等待结果）
curl -X POST http://127.0.0.1:8900/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"firefly-nano-banana-pro-2k-16x9","prompt":"a red apple"}'

# 视频生成
curl -X POST http://127.0.0.1:8900/v1/videos/generations \
  -H "Content-Type: application/json" \
  -d '{"model":"firefly-sora2-8s-16x9-720p","prompt":"a dog running"}'
```

## 目录结构

```
本地画布/
├── backend/           # FastAPI 后端
│   ├── main.py        # 入口 + API 路由 + OpenAI 兼容网关
│   ├── adobe_client.py# Firefly 3P 客户端（签名/上传/生成/轮询）
│   ├── refresh_manager.py  # Cookie 导入 + Token 刷新
│   ├── token_manager.py    # Token 池（兼容 adobe2api）
│   └── config_manager.py   # 配置（兼容 adobe2api）
├── web/               # React + Vite 前端
│   └── src/
│       ├── canvas/    # 无限画布核心
│       ├── nodes/     # 各类节点组件
│       ├── pages/     # 画布页 / 设置页
│       └── store/     # 状态管理（撤销重做）
├── config/            # config.json + tokens.json（与 adobe2api 同格式）
├── outputs/           # 生成结果
├── start.bat          # Windows 一键启动
└── start.command      # macOS 一键启动
```

## 免责声明

本项目仅供个人学习研究使用。内置的 Firefly 通道调用的是 Adobe 网页版内部接口，请遵守 Adobe 服务条款，勿用于商业用途或大规模滥用。
