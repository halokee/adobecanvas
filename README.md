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

双击 `start.bat`，首次运行会自动安装依赖并构建前端，然后自动打开 `http://localhost:8900`。

### 方式二：手动

```bash
# 1. 后端
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8900

# 2. 前端（开发模式，另开终端）
cd web
npm install
npm run dev        # http://localhost:3000
# 或构建后由后端直接托管
npm run build      # 后端会托管 web/dist
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
  "use_proxy": false,             // 是否走代理
  "proxy": "",                    // 代理地址，如 http://127.0.0.1:7890
  "gpt_image_quality": "medium",  // 默认图片质量 low|medium|high
  "generate_timeout": 300,        // 生成超时（秒）
  "token_rotation_strategy": "round_robin",  // 轮换策略 round_robin|random
  "external_base_url": "",        // 外部 OpenAI 兼容 API Base URL
  "external_api_key": "",         // 外部 API Key
  "default_channel": "firefly"    // 默认通道 firefly|external
}
```

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
└── start.bat          # 一键启动
```

## 免责声明

本项目仅供个人学习研究使用。内置的 Firefly 通道调用的是 Adobe 网页版内部接口，请遵守 Adobe 服务条款，勿用于商业用途或大规模滥用。
