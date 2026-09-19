# Security Policy

## 报告安全漏洞

- 渠道：GitHub Private Vulnerability Reporting（发布后启用）或项目维护者邮箱（见仓库主页）。
- 请勿公开披露未修复漏洞。

## 响应承诺

- 确认：48 小时内回复；
- 严重（可导致敏感数据出站泄漏）漏洞：7 天内发布修复或缓解说明；
- 其余漏洞：30 天内评估并给出处理计划。

## 安全模型

- 唯一硬保证是“不发送”：`safe_route`/`local`/`block` 模式下，检测命中内容绝不发往中转。
- `redact` 模式是尽力而为的体验选项，不承诺 100% 召回。
- 审计日志不落请求体与 Authorization。

## 已知边界

见 [docs/BASELINE.md](docs/BASELINE.md)：检测非 100%；只保护出口流量。
