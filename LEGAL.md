# Legal Notes（合规留痕）

> 更新时间：2026-08-06 ｜ 性质：信息整理，不构成法律意见；重大决策前请咨询持证律师。

## 结论摘要

- 本项目是通用 HTTP API 网关 + 隐私过滤，不涉及 VPN、侵入工具或非法经营；代码本身法律风险低。
- 工具设计合规 ≠ 用户行为合规：用户接入什么端点由用户负责（见 ACCEPTABLE_USE.md）。
- 风险控制点：宣传文案、用户滥用、未来托管商业化、面向他人的数据出境。

## 参考来源

- 《生成式人工智能服务管理暂行办法》：https://www.gov.cn/zhengce/202311/content_6917778.htm
- 《促进和规范数据跨境流动规定》（2024）：https://www.gov.cn/gongbao/2024/issue_11366/202405/content_6954192.html
- 《数据出境安全评估办法》（2022）：https://www.gov.cn/zhengce/2022-07/07/content_5728937.htm
- 《网络安全法》第27条：官方裁量基准（教育部 PDF 快照）及地方公安权责清单
- 《刑法》第285条第3款普法页：https://qh.12348.gov.cn/pub/qhpfw/sfxzyw/jrbb/202302/t20230221_100556.html
- GitHub DMCA 流程：https://docs.github.com/en/site-policy/content-removal-policies/guide-to-submitting-a-dmca-takedown-notice
- GitHub Acceptable Use Policies：https://docs.github.com/en/site-policy/acceptable-use-policies/github-acceptable-use-policies

## 合规动作（已落实）

- README 合规声明 + “非 VPN”说明；
- ACCEPTABLE_USE.md（禁止滥用 + 举报通道）；
- 不收集遥测；审计仅本地；
- 示例仅用占位域名/占位路径；
- 基准仅合成数据；
- 发布前敏感信息扫描门禁（scripts/scan_repo.py，CI 强制）。
