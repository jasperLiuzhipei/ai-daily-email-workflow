# 邮件日报中台部署说明

## P0 目标

把现有日报流程作为一个受控任务部署到中台，直接产出并发送带 CID 内联图片的邮件。P0 不要求建设历史知识库、用户门户或完全自动群发。

```text
定时器或人工触发
  -> 选源与去重
  -> 编辑/模型生成内容 JSON
  -> 研究图生成与人工质检
  -> HTML 渲染与 CID 邮件打包
  -> 人工批准
  -> 公司邮件服务发送
```

## 运行状态

建议给每期创建唯一 `run_id`，并保存以下状态：

`DISCOVERED -> SELECTED -> CONTENT_READY -> IMAGE_APPROVED -> HTML_READY -> APPROVED_TO_SEND -> SENT | FAILED`

P0 的关键规则：`APPROVED_TO_SEND` 之前不能调用正式收件名单；同一 `run_id` 的正式发送必须幂等。

## 需要中台提供的能力

- 定时任务与手动重跑入口。
- 工作目录或对象存储，用于保存单期 JSON、研究图、HTML、图片 manifest 与发送记录。
- 密钥管理：SMTP 密码、Image2/API Key 不进入代码库或任务参数。
- 公司邮件服务或受控 SMTP 出口；发送端直接复用 CID 打包逻辑，不需要人工下载 `.eml`。
- 简单审批界面：预览 HTML、确认收件名单、点击正式发送。

## 需要保存的最小产物

- 选源结果、评分、重复剔除理由与来源快照。
- 最终内容 JSON、HTML、研究图及其 lineage manifest。
- 发送时间、收件名单版本、发送状态和失败原因。

原始抓取页面、临时 EML、候选图片可以放在任务临时目录，按 7-30 天保留策略清理；无需把当前整个 `daily_runs/` 文件夹当作用户可见系统搬迁。

## Provider 迁移

- `codex_imagegen` 是当前质量基线，但不适合直接作为中台依赖。
- `gpt_image_2` / Image2 已有 API Provider 适配位置；先在中台做同 Prompt A/B，对每张图保留 prompt hash、图片 hash、质量决策和比较说明。
- 只有 Provider 被显式批准且单张图片通过质量门禁，才能进入正式邮件。

## 配置约定

首次部署时复制：

```bash
cp config/email_settings.example.json config/email_settings.json
cp config/recipients.example.json config/recipients.json
cp config/editor_settings.example.json config/editor_settings.json
```

以上三个本地文件被 `.gitignore` 排除。中台环境应以密钥管理和配置中心覆盖这些配置，而不是提交真实收件人、编辑身份或 SMTP 信息。

## P0 验收

1. 定时或手动触发能生成一份可预览 HTML。
2. 研究图、JSON、HTML 和发送记录能按 `run_id` 追溯。
3. 测试名单可收到带 CID 图片的完整邮件。
4. 正式发送需要人工确认，重复点击不会重复群发。
5. 次日选源只以成功正式发送记录作为去重基线。
