# BASELINE（v0.1 基线）

> 日期：2026-08-06（Phase 1 完成后更新）

## 测试基线

- 命令：`python -m unittest discover -s tests -v`
- 结果：**71/71 passed**，覆盖：
  - 路由：优先级、400/401/429/500/超时语义、熔断半开恢复、Retry-After 冷却
  - 隐私：上下文检测、编码绕过（base64/hex/URL）、脱敏还原、会话粘性
  - 兼容：chat/completions 非流式/流式/tools、/v1/models union
  - 加固：鉴权、审计轮转、热加载并发、熔断状态持久化、模型别名
- 运行环境：Windows + Python 3.12（标准库，零第三方依赖）。

## 检测基准（合成数据）

- 命令：`python scripts/benchmark.py --cases tests/benchmark/cases.jsonl --holdout tests/benchmark/holdout.jsonl`
- cases：259 条，recall=1.000，precision=1.000，F1=1.000
- holdout：34 条，recall=1.000，precision=1.000，F1=1.000
- 说明：纯合成数据（`scripts/generate_benchmark.py` 可复现），不代表真实世界召回率；阈值初版 recall≥0.85 / precision≥0.90。

## 已修复缺陷（相对 v0 起点）

1. ✅ 密码口语化上下文检测（含防误报白名单）；
2. ✅ 编码绕过防御（base64/hex/URL 解码再扫 + 整体不可逆掩码）；
3. ✅ 熔断状态持久化 + 配置指纹失效；
4. ✅ 非回环绑定强制鉴权；
5. ✅ 公开基准（cases + holdout）；
6. ✅ 审计轮转、热加载双缓冲、模型别名、Retry-After 冷却；
7. ✅ 示例配置真实路径已替换为占位符。

## 实测评测（mock 干跑，2026-08-06）

- 评测台：work/privacy-eval（出站捕获代理 + 19 条合成样本 + 三模式分级）
- safe_route：recall=1.0 / clean=1.0 / leak=0 / sticky=✅ → 等级 S
- redact：leak=0（修复后）→ 等级 A
- block：recall=1.0 / clean=1.0 / leak=0 → 等级 A
- 修复：英文密码系动词漏掩码（the password is xxx）；上游 URL 双 `/v1` 拼接。

## Agent 级实测（opencode 端到端，mock 上游）

- opencode 1.18.14 经网关调用：干净请求走中转（捕获无敏感），含邮箱+密码的请求自动改走安全通道；
- 发现并修复 agent 提示词误报：“secrets and keys”“refresh token support”等安全规范语句曾被误判敏感；
- 修复策略：英文上下文值改为“像密钥才敏感”启发式（含数字/符号或 sk-/ghp_/eyJ 前缀），赋值式强检测不变。

## 已知边界（v0.1）

- `/v1/responses` 仅透传，未做协议翻译（Codex 适配在 v0.2，先试 `wire_api=chat`）；
- Hermes/opencode 真机冒烟待验证；
- 检测非 100%，依赖 fail-closed 与 `safe_route` 兜底。
