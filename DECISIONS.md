# DECISIONS（v0.1 决策记录）

> 日期：2026-08-06 ｜ 状态：active

## 已定决策

1. 仓库结构：两个独立仓库（`relay-pool-gateway` + `sensitive-channel-lockdown`），可独立使用。
2. 许可证：MIT（推荐默认，用户可改 Apache-2.0）。
3. v0.1 协议主线：`/v1/chat/completions` 与 `/v1/completions`（Hermes/opencode/curl 即插即用）；`/v1/responses` 仅透传实验端点，README 标注。
4. Codex 适配：v0.2 先验证 `wire_api=chat`，不行再实现 responses 翻译层。
5. 定位一句话：**“本地优先、隐私硬隔离的 OpenAI 兼容网关 + Codex 技能，Hermes/opencode 即插即用”**。
6. 本执行轮只交付 mock 上游 demo；真实渠道灰度与 GitHub 发布等待用户输入（决策节点）。

## 待用户确认

- GitHub 账号/org 与仓库名占用复查；
- 是否允许真实渠道 7 天灰度（key 只进环境变量）；
- 发布渠道优先级（linux.do 首发 vs GitHub 先行）。
