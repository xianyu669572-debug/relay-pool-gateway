# relay-pool-gateway

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![Version](https://img.shields.io/badge/version-0.1.0-green.svg)](https://github.com/xianyu669572-debug/relay-pool-gateway/releases)
[![Tests](https://img.shields.io/badge/tests-73%20passed-brightgreen.svg)](#development)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen.svg)](#quick-start)

**Local-first, privacy-hardened OpenAI-compatible LLM gateway — provider pool + privacy routing.**

Pool multiple relay / official / local providers behind one endpoint. If one provider fails or rate-limits, it automatically fails over to the next. If a request looks sensitive, it is *never* sent to an untrusted relay — it is routed to an official or local provider, redacted, or blocked.

- **Protocol:** `POST /v1/chat/completions` (streaming SSE supported)
- **Runtime:** Python 3.11+ standard library only — **zero pip dependencies**
- **Tests:** 73 passed (`python3 -m unittest discover -s tests -v`)
- **License:** [MIT](LICENSE)

[中文介绍在下方 ↓](#中文介绍)

---

## Why this exists

| Pain point | How this gateway solves it |
|---|---|
| One relay returns 429 / goes down | Priority + weighted round-robin pool, bounded failover to the next provider |
| API keys leak from config files | Config only stores env-var *names*; keys live in environment variables |
| Agent context contains passwords / paths / IDs and gets sent to an untrusted relay | Sensitive-data detection + `safe_route`: the whole session is pinned to a safe/local channel |
| You still want cheap relays for non-sensitive traffic | `redact` mode: reversible placeholders for safe data, irreversible masking for secrets |

This is **not a VPN**. It only does API aggregation and outbound privacy routing.

## Quick Start

```bash
git clone https://github.com/xianyu669572-debug/relay-pool-gateway.git
cd relay-pool-gateway

# 1. Configure (never put real keys in the toml)
cp config.example.toml config.local.toml
# Edit [[providers]]: set base_url and api_key_env for each provider

# 2. Inject keys via environment
export RELAY_A_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."

# 3. Run (listens on 127.0.0.1:8400 by default)
python3 -m relay_gateway --config config.local.toml
```

```bash
# 4. Call it — any OpenAI-compatible client works
curl http://127.0.0.1:8400/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

OpenAI Python SDK: set `base_url="http://127.0.0.1:8400/v1"`. Hermes / Codex / curl work the same way. Note: Codex `/v1/responses` is currently pass-through only; full adaptation is planned for v0.2.

## Architecture

```
Client (Hermes / curl / OpenAI SDK)
        │  http://127.0.0.1:8400/v1/chat/completions
        ▼
┌─────────────────────────────────────────┐
│              server.py                  │
│  Auth → Privacy decision → Routing      │
│       → Upstream → Audit                │
└─────────────────────────────────────────┘
        │
        ├─ privacy.py   sensitive detection / 4 modes / session stickiness
        ├─ router.py    priority · weighted pool · failure classification
        ├─ health.py    probing · 3-state circuit breaker · 429 cooldown
        ├─ upstream.py  JSON / SSE, Retry-After parsing
        └─ state.py     circuit state on disk (invalidated when config changes)
```

**Privacy modes:** `safe_route` (default, pin session to safe/local) · `redact` (placeholder + restore) · `local` (local models only) · `block` (reject with 400). Detection uses regex + context keywords + vault-path fingerprints + decode-then-rescan (base64/hex/URL). It is fail-closed by design — not 100%, and it only protects the outbound hop to upstream providers.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full request flow.

---

## 中文介绍

本地优先、隐私硬隔离的 **OpenAI 兼容 LLM 网关**。

把多家 **中转站 / 官方 API / 本地模型** 合成一个入口：按优先级调用，挂了自动换号（号池）；请求里一旦像敏感数据，就改走官方或本地，**默认不发给中转**。

- 协议：`POST /v1/chat/completions`（流式 SSE 可用）
- 运行：Python **3.11+ 标准库**，无 pip 依赖
- 实测：本机 `python3 -m unittest discover -s tests -v` → **73 passed**（2026-09）
- 许可：[MIT](LICENSE)

> Codex 的 `/v1/responses` 目前只是透传，完整适配标在 v0.2。Hermes / curl / 任意 chat-completions 客户端可以直接打本机端口。

---

### 它解决什么问题

| 痛点 | 本网关怎么做 |
|---|---|
| 一家中转 429 / 挂了 | 优先级 + 同级权重轮询，失败按规则换下一家 |
| 密钥写进配置文件被截断或泄漏 | 配置里只写环境变量名，key 在环境变量 |
| Agent 上下文里有密码/路径/身份证，却打到不可信中转 | 检测后 `safe_route`：整段会话钉到官方/本地 |
| 想「脱敏后仍用便宜中转」 | `redact` 模式：可逆占位符，响应再还原；密码类不可逆掩码 |

**不是 VPN，也不帮你翻墙。** 只做 API 聚合和出口隐私。

---

### 架构

```
客户端 (Hermes / curl / OpenAI SDK)
        │  http://127.0.0.1:8400/v1/chat/completions
        ▼
┌─────────────────────────────────────────┐
│              server.py                  │
│  鉴权 → 隐私决策 → 选渠道 → 上游 → 审计 │
└─────────────────────────────────────────┘
        │
        ├─ privacy.py   敏感检测 / 四模式 / 会话粘性
        ├─ router.py    优先级 · 权重号池 · 故障分类 · 有界 fallback
        ├─ health.py    探活 · 三态熔断 · 429 冷却
        ├─ upstream.py  JSON / SSE，解析 Retry-After
        └─ state.py     熔断状态落盘（配置变了旧状态作废）
```

请求路径（详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)）：

1. 可选本机 API key  
2. **隐私**：`safe_route` / `redact` / `local` / `block`；命中后会话可钉死安全通道（默认 4 小时）  
3. **路由**：`priority` 小的先用；同优先级按 `weight` 轮询  
4. **模型名映射**：客户端仍叫 `deepseek-v4-flash`，各渠道可映射成自己的名字  
5. **故障**：400 不切换（你的请求有问题）；401 隔离该渠道；429/5xx/超时换下一家  
6. `redact` 模式下把占位符还原后再回给客户端  
7. 审计只记类别/状态/耗时，**不写请求体、不写 Authorization**

熔断：`CLOSED` → 连续失败达阈值 → `OPEN`（冷却）→ `HALF_OPEN`（放探针）→ 成功回 CLOSED / 失败回 OPEN。

---

### 使用方法

#### 1. 环境

- Python 3.11 或更高（WSL / Linux / macOS / Windows 均可）
- 不需要 `pip install`（标准库即可）

```bash
git clone https://github.com/xianyu669572-debug/relay-pool-gateway.git
cd relay-pool-gateway
python3 -m unittest discover -s tests -v
```

#### 2. 配置

复制示例，**不要把真实 key 写进 toml**：

```bash
cp config.example.toml config.local.toml
# Windows PowerShell: Copy-Item config.example.toml config.local.toml
```

把 `[[providers]]` 改成你的中转 `base_url` 和 `api_key_env`。官方渠道加 `safe = true`（只给敏感请求用，不进普通号池）。本地 Ollama 等加 `local = true`。

`[privacy] vault_paths` 改成你的知识库路径，用于「路径指纹」检测（避免把 vault 路径打到中转）。

#### 3. 注入密钥

```bash
export RELAY_A_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
```

PowerShell：

```powershell
$env:RELAY_A_API_KEY = "sk-..."
$env:DEEPSEEK_API_KEY = "sk-..."
```

#### 4. 启动

```bash
python3 -m relay_gateway --config config.local.toml
```

默认监听 `127.0.0.1:8400`。

#### 5. 调用

```bash
curl http://127.0.0.1:8400/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

OpenAI Python SDK：把 `base_url` 设为 `http://127.0.0.1:8400/v1`，`api_key` 若网关 `api_keys` 为空则本机可随便填占位。

Hermes：自定义 provider 的 `base_url` 指向上述地址，`api_mode` 用 chat completions。

---

### 隐私四模式

| 模式 | 行为 |
|---|---|
| **safe_route（默认）** | 检测到敏感 → 本会话改走 `safe`/`local` 渠道，原文不进中转 |
| **redact** | 可逆占位符后仍可发中转，响应还原；密码/密钥/IP 等不可逆掩码 |
| **local** | 敏感请求只打本地模型 |
| **block** | 直接 400 拒绝 |

检测：正则 + 上下文关键词 + vault 路径 + base64/hex/URL 解码再扫。  
**不是 100%。** 默认 fail-closed：宁可拒答或改道，也不裸发中转。只保护「出站到上游」这一跳，不保护你本机 Agent 日志。

---

### 号池怎么配

```toml
[[providers]]
id = "relay-a"
priority = 1          # 越小越先用
weight = 1            # 同一 priority 内的流量比例
api_key_env = "RELAY_A_API_KEY"
base_url = "https://your-relay.example/v1"

[[providers]]
id = "relay-b"
priority = 1
weight = 2            # 同级里拿大约 2/3 流量
api_key_env = "RELAY_B_API_KEY"
```

健康检查见各 provider 的 `[providers.health]`；熔断见 `[providers.circuit]`。

---

### 文档索引

| 文档 | 内容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模块与请求流程 |
| [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) | 客户端兼容矩阵 |
| [docs/BASELINE.md](docs/BASELINE.md) | 合成基准与检测边界 |
| [SECURITY.md](SECURITY.md) | 漏洞报告与安全模型 |
| [ACCEPTABLE_USE.md](ACCEPTABLE_USE.md) / [LEGAL.md](LEGAL.md) | 使用边界 |

---

### 安全与合规（请先读）

- 密钥只放环境变量或系统密钥库，禁止提交 `config.local.toml` / `.env`
- 绑定 `0.0.0.0` 时必须配置 `[server] api_keys`
- 官方 API 也可能保留日志；极敏感内容请用 `local`
- 本项目不提供绕过访问控制或未授权扫描他人系统的能力

---

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/scan_repo.py
```

欢迎 Issue / PR。请勿在 issue 里粘贴真实 key 或用户数据。

Issues and PRs are welcome. Please never paste real keys or user data in an issue.
