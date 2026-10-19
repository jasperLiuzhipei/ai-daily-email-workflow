# 邮件日报中台部署说明

## P0 目标

把现有日报流程作为一个受控任务部署到中台，直接产出并发送带 CID 内联图片的邮件。P0 不要求建设历史知识库、用户门户或完全自动群发。

```text
定时器或人工触发
  -> 选源与去重
  -> gpt-5.6-sol 生成研究解读与内容 JSON
  -> gpt-image-2 生成研究图候选与人工质检
  -> HTML 渲染与按收件人个性化的 CID 邮件打包
  -> 人工批准
  -> 公司邮件服务发送
```

## 运行状态

当前本地工作流已经为每期生成 `daily_runs/YYYY-MM-DD/workflow_run.json`，其中 `run_id` 固定为 `ai-daily-YYYY-MM-DD`。中台上线时可将同一份字段映射到任务表和对象存储，而不是重新设计一套流程。

阶段为：

`DISCOVERED -> SELECTED -> CONTENT_READY -> IMAGE_APPROVED -> HTML_READY -> PACKAGED -> TESTED -> APPROVED_TO_SEND -> SENT`

失败不会覆盖最后一个成功阶段，而是作为 `outcome: FAILED` 和 `last_error` 记录，便于人工重跑。P0 的关键规则：`APPROVED_TO_SEND` 之前不能调用正式收件名单；同一 `run_id` 的正式发送必须幂等。当前脚本已默认拦截同日期重复正式发送，只有显式添加 `--allow-resend-prod` 才能补发。

状态单的最小字段：

- `run_id`、`run_date`、`phase`、`outcome` 和事件时间线。
- `artifacts`：选源结果、内容 JSON、图片 manifest、HTML、EML 与名单快照的相对路径、大小、Hash。
- `delivery`：测试/正式发送状态、名单组 ID/版本、人数和快照指纹，不保存邮箱明文。
- `last_error`：经过邮箱脱敏的失败摘要。

## 需要中台提供的能力

- 定时任务与手动重跑入口。
- 工作目录或对象存储，用于保存单期 JSON、研究图、HTML、图片 manifest 与发送记录。
- 密钥管理：SMTP 密码、共享 API Key 与文本/图片网关地址不进入代码库或任务参数。
- 收件人目录：名单组、成员、名单版本和冻结快照由数据库或组织架构服务管理，不由前端粘贴邮箱。
- 公司邮件服务或受控 SMTP 出口；发送端直接复用 CID 打包逻辑，不需要人工下载 `.eml`。
- 简单审批界面：预览 HTML、确认收件名单、点击正式发送。
- 可被员工邮件客户端访问的反馈服务入口，以及持久化数据库。公司中台可使用
  Python + SQLite 持久卷；Vercel 方案使用 Supabase Postgres，不能依赖函数本地文件。
- 反馈服务使用一个公开根域名同时承接评分页、文字页、打开像素、统计 API 与后台；正式发送端通过受保护的 `/api/delivery` 同步发送人数。

## 需要保存的最小产物

- 选源结果、评分、重复剔除理由与来源快照。
- 最终内容 JSON、HTML、研究图及其 lineage manifest。
- 发送时间、收件名单版本、发送状态和失败原因。
- 每期匿名打开事件、最终评分和可选文本意见；反馈库不保存邮箱明文。

原始抓取页面、临时 EML、候选图片可以放在任务临时目录，按 7-30 天保留策略清理；无需把当前整个 `daily_runs/` 文件夹当作用户可见系统搬迁。

## Provider 迁移

- `codex_imagegen` 是当前质量基线，但不适合直接作为中台依赖。
- `gpt_image_2` / Image2 已是中台运行路径；先做同 Prompt A/B，对每张图保留 prompt hash、图片 hash、质量决策和比较说明。
- 只有 Provider 被显式批准且单张图片通过质量门禁，才能进入正式邮件。
- 文本解析和日报 JSON 已统一走 `gpt-5.6-sol` OpenAI-compatible API，不依赖 Codex 会话。

## 配置约定

首次部署时复制：

```bash
cp config/email_settings.example.json config/email_settings.json
cp config/recipients.example.json config/recipients.json
cp config/editor_settings.example.json config/editor_settings.json
cp config/ai_runtime.example.json config/ai_runtime.json
```

以上四个本地文件被 `.gitignore` 排除。中台环境应以密钥管理和配置中心覆盖这些配置，而不是提交真实收件人、编辑身份、API Key 或 SMTP 信息。

## P0 验收

1. 定时或手动触发能生成一份可预览 HTML。
2. 研究图、JSON、HTML 和发送记录能按 `run_id` 追溯。
3. 测试名单可收到带 CID 图片的完整邮件。
4. 正式发送需要人工确认，重复点击不会重复群发。
5. 次日选源只以成功正式发送记录作为去重基线。
6. 每次正式发送都能追溯名单组 ID、版本、人数和冻结快照。
7. 打开邮件可产生估算阅读数据；点星一次即保存，重复点击只更新最终值；文字意见始终可选。
8. 部署后 `/healthz` 与 `/.well-known/ai-daily-feedback.json` 可访问，地址自检脚本能列出所有邮件和后台入口。
