# Changelog

## v0.1.0（2026-08-06）

- 初始开源版（mock 演示可用）：
  - 优先级 + 权重路由、三态熔断（持久化 + 配置指纹）、故障分类、有界 fallback
  - Retry-After 冷却、SSE 流式透传、模型别名映射、审计轮转、热加载双缓冲
  - 隐私四模式、上下文检测、编码绕过防御、会话粘性
  - 64 项测试、259+34 合成基准、兼容矩阵（chat/completions）
  - MIT 许可 + 合规文档（ACCEPTABLE_USE / LEGAL / SECURITY）

### v0.1.0 实测修复（评测台干跑发现）

- 英文密码上下文“the password is xxx”漏掩码 → 修复系动词取值；
- 上游 URL 双 `/v1`（base_url 含 /v1 + 路径 /v1/...）→ 修复拼接；
- agent 提示词误报（“secrets and keys”“refresh token support”）→ 英文上下文值改为“像密钥才敏感”启发式；
- 测试增至 71 项。

## 已知边界

- `/v1/responses` 仅透传（Codex 适配 v0.2）
- Hermes/opencode 真机冒烟待验证
