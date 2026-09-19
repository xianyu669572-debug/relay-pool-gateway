# 客户端兼容性矩阵（v0.1）

> 日期：2026-08-06 ｜ 基准：mock 上游 + 自动化测试（tests/test_client_compat.py）

## 已自动化验证（curl 等价）

| 场景 | 结果 |
|---|---|
| `/v1/chat/completions` 非流式 | ✅ 200，响应体/响应头正确 |
| `/v1/chat/completions` 流式（SSE） | ✅ 内容、`: ping` keepalive、`[DONE]` 透传 |
| `tools` / `tool_calls`（非流式） | ✅ 原样透传 |
| 流式 `tool_calls` delta | ✅ 透传 |
| 400 客户端错误 | ✅ 不 failover，原样返回 |
| 401 未授权 | ✅ failover + 渠道隔离（quarantine） |
| 429 限流 | ✅ failover |
| 5xx 服务端错误 | ✅ failover |
| 超时 | ✅ failover |
| `/v1/models` | ✅ 返回多渠道 union |

## 真机冒烟（待验证）

| 客户端 | 协议 | 状态 |
|---|---|---|
| curl | chat/completions | ✅（已由自动化测试等价覆盖） |
| Hermes | chat/completions | ⏳ 待真机验证（环境未提供） |
| opencode | chat/completions | ⏳ 待真机验证（环境未提供） |
| Codex | responses | ⏳ v0.2（先试 wire_api=chat，再考虑翻译层） |

## 已知边界

- `/v1/responses` 目前为透传实验端点：仅对原生支持 responses 的上游可用；Codex 适配在 v0.2。
- `/v1/completions` 为透传端点，未做格式转换。
