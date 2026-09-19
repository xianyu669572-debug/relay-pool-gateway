# 架构

## 模块

| 文件 | 职责 |
|---|---|
| `relay_gateway/config.py` | TOML 配置加载、鉴权校验、模型别名、熔断状态指纹 |
| `relay_gateway/health.py` | 周期探活 + 三态熔断 + 429 冷却 + 状态持久化 |
| `relay_gateway/state.py` | 熔断状态原子持久化（配置指纹失效） |
| `relay_gateway/privacy.py` | 敏感检测（正则/上下文/编码绕过）、脱敏、会话粘性 |
| `relay_gateway/router.py` | 优先级/权重路由、故障分类、有界 fallback、模型映射 |
| `relay_gateway/upstream.py` | urllib 上游客户端：JSON 与 SSE，Retry-After 解析 |
| `relay_gateway/server.py` | OpenAI 兼容 HTTP 入口、隐私决策、审计、热加载 |
| `relay_gateway/__main__.py` | CLI 入口（`python -m relay_gateway --config ...`） |

## 请求流程

```text
客户端 → /v1/chat/completions
  → 鉴权（api_keys）
  → 隐私决策（safe_route/redact/local/block + 会话粘性）
  → 选渠道（优先级 + 健康 + 熔断 + 冷却）
  → 模型别名映射
  → 上游请求（JSON/SSE，超时/Retry-After）
  → 故障分类（400 不切换 / 401 隔离 / 429/5xx/超时切换）
  → 响应还原（redact 模式）+ 审计（仅元数据）
```

## 熔断状态机

`CLOSED` →（连续失败 ≥ 阈值）→ `OPEN`（冷却期）→（到期）→ `HALF_OPEN`（放行探针）→ 成功回 `CLOSED` / 失败回 `OPEN`。

状态持久化到 `gateway-state.json`，附配置指纹；配置变化（渠道/超时/熔断参数）后旧状态自动失效。
