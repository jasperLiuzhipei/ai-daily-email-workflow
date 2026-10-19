# 事件选源首版发布记录

状态：已部署 `event-editorial-v2.1`。正式运行目录：`/opt/ai-daily/ai_daily_workflow`。

- 默认 `select/prepare` 已接入事件正文评分；从2026-09-19期起生产发送要求新评分与覆盖复核。
- 初次20个文件，后补原文跳转URL和相关测试2个文件；文件Hash均由安装程序核对。
- 本地完整回归：211项通过，包含19项新增事件评分测试。
- 正式服专项回归：64项通过（事件19、覆盖12、来源质量6、选源路由16、源适配3、候选回流8）。
- 正式服现有文本网关完成真实单事件评估，GitHub文章75分、证据limited；没有触发内容生成或邮件发送。
- 今日抽样：24来源、23事件、18完成、5待核验；6新闻、1研究。7个入选材料完成交接和最终URL校验。覆盖复核有未处理事项，正确阻止放行。
- 当前已发送的今日HTML Hash保持 `5cca81f3fde87f959538b300234cc4f58213500d7a24db4f111283a464b20313`。

备份：

- `/opt/ai-daily/deploy_backups/event_editorial_20260918_153132`
- `/opt/ai-daily/deploy_backups/event_editorial_20260918_153556`

每个备份目录保存部署清单、旧文件和 `deployment_result.json`。安装器在测试失败时自动恢复；未来人工回滚须按后补→初次的倒序并核对后续修改，不能直接覆盖较新的用户工作。

本地留存：`tmp/scoring_v2_deploy/`、`tmp/scoring_v2_deploy_followup/`、`tmp/event_scoring_validation_20260918/`。部署保留了服务器既有周线程邮件逻辑及其他非本次差异。

限制：尚未完成14天人工标注回测；重评可有分数波动；部分正文仍会失败；旧选择器及兼容配置尚未全部拆除。详见 [现行规则](SOURCE_SELECTION_LOGIC.md) 和 [代码审计](CODE_AUDIT_2026-09-18.md)。
