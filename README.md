# AI Daily Email Workflow

> 当前正式选源规则：[事件评分与选源逻辑](docs/SOURCE_SELECTION_LOGIC.md)。2026-09-18 起默认使用正文事件评分；这与下方 `daily_v2` 的中台影子工作区是两项不同改造。

> 新版开发入口：[`docs/V2_SHADOW_RELEASE.md`](docs/V2_SHADOW_RELEASE.md)。V2 影子工作区已可运行；现有正式制作与发送入口保持原状态。完整需求基线为 PRD 1.4。

面向内部 AI 情报日报的可部署邮件工作流：选源、API 内容 JSON、研究图质量门禁、Foxmail 兼容 HTML、CID 内联图片和受控 SMTP 发送。

2026-10-12 新增[邮件 AI 自审与失败恢复](docs/EMAIL_SELF_REVIEW.md)：定时生成和正式发送前审查最终邮件，最多 3 轮尝试，耗尽后仅向编辑邮箱发送原因和建议。

2026-10-19 更新[采集、审核输入与 trace 计数](docs/TRACE_OPTIMIZATION_2026-10-19.md)：修复压缩 RSS 解析，保留证据文本并压缩审核元数据，明确区分待评估与已评估暂缓。原模型独立复审通过，当期输入 token 实测减少 28.26%。新闻数量策略见[新闻预算](docs/NEWS_VOLUME_BUDGET.md)。

## 保留的主链

```text
Source selection -> API editorial JSON -> Research image -> HTML -> CID package -> SMTP send
```

- 选源：AIHot 主/全量/论文 feed、AIHot Daily 编辑预筛、BestBlogs 和配置化官方研究源。
- 内容：研究前沿使用结构化解读；新闻使用轻量但带业务启发的桥接字段。
- 图片：只为研究前沿生成；图片必须通过来源、Prompt、Hash 与人工质检门禁。
- 邮件：研究图被打包为 CID 内联附件；启用反馈后按收件人生成匿名个性化邮件，阅读像素和来源链接埋点是仅有的远程请求。
- 去重：只以成功正式发送的日报作为下一期新闻去重基线。

## 仓库边界

这个仓库只保存代码、提示词、模板和示例配置。

不提交：真实 SMTP 配置、收件名单、密码、日报运行产物、研究图、HTML、EML 和日志。它们由本地运行目录或未来中台的对象存储保存。

## 首次配置

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config/email_settings.example.json config/email_settings.json
cp config/recipients.example.json config/recipients.json
cp config/editor_settings.example.json config/editor_settings.json
cp config/ai_runtime.example.json config/ai_runtime.json
cp .env.example .env
```

选源策略、读者画像、栏目和编辑规则已分别放在 `selection_policy.json`、`audience_profiles.json`、`content_sections.json`、`editorial_policy.json`。这些文件不含密钥，可随代码版本管理；字段说明见 [配置说明](docs/CONFIGURATION.md)。

Python 依赖见 `requirements.txt`；系统还需要 `curl` 用于抓取公开 RSS/HTML 源。文本和图片的 API 运行时配置见 [API Runtime](docs/API_RUNTIME.md)，当前示例默认使用 `gpt-6-sol` 与 `gpt-image-2`。

## 每日入口

```bash
# 查看某期工作流文件是否齐全
python3 scripts/run_ai_daily.py status --date YYYY-MM-DD

# 创建或查看该期的运行状态单；已有本地文件会首次自动回填到状态单
python3 scripts/run_ai_daily.py state --date YYYY-MM-DD

# 仅检查测试/正式名单组的状态、版本、人数和指纹，不显示邮箱
python3 scripts/run_ai_daily.py recipients

# 检查文本与图片模型的本地配置，不发网络请求
python3 scripts/run_ai_daily.py check-api

# 安全写入本地中转 Key；终端隐藏输入，Key 不进入命令历史
python3 scripts/configure_ai_runtime.py --base-url https://<approved-gateway>/v1

# 文本和生图使用不同中转时，分别写入各自的 Key 与地址
python3 scripts/configure_ai_runtime.py --scope text --base-url https://<text-gateway>/v1
python3 scripts/configure_ai_runtime.py --scope image --base-url https://<image-gateway>/v1

# 只诊断某一个通道，避免另一侧暂时不可用时混淆结果
python3 scripts/run_ai_daily.py check-api --kind image --live-models

# 自动选源：按内容价值、证据准入和新闻预算选择，不补最低条数
python3 scripts/run_ai_daily.py select --date YYYY-MM-DD

# 一次完成选源、一手源抓取、配置的文本模型内容和 gpt-image-2 候选图
python3 scripts/run_ai_daily.py prepare --date YYYY-MM-DD

# 将候选图放入比较版 HTML；不会批准图片或写正式发送记录
python3 scripts/run_ai_daily.py preview --date YYYY-MM-DD --theme digest_navy --news-layout paper

# 日报研究图默认使用 1920x1080；单次任务可临时覆盖画布
python3 scripts/run_ai_daily.py api-image --date YYYY-MM-DD --image-size 1920x1080 --refresh-api-image

# 内容定稿后，诊断自动选源与最终发布之间的差异，并生成编辑复盘模板
python3 scripts/run_ai_daily.py diagnose --date YYYY-MM-DD

# 仅将“已完成编辑复盘”的期次汇总为评分回放集
python3 scripts/run_ai_daily.py replay

# 在内容 JSON 与研究图审核完成后构建 HTML
python3 scripts/run_ai_daily.py build --date YYYY-MM-DD --theme digest_navy --news-layout paper

# 发测试邮件
python3 scripts/run_ai_daily.py test --date YYYY-MM-DD --skip-build --ask-password --send

# 正式发送：必须明确确认，成功后才写入去重与期数记录
python3 scripts/run_ai_daily.py prod --date YYYY-MM-DD --skip-build --ask-password --send --confirm-prod
```

每次 HTML 构建完成后，脚本会打印对应日期的正式发送命令。

每个期次还有一份 `daily_runs/YYYY-MM-DD/workflow_run.json`。它记录稳定的 `run_id`、阶段、文件 Hash、冻结名单快照元数据和发送结果，但不记录邮箱、密码或正文。正式邮件已成功发送的日期默认拒绝再次群发；只有确认需要补发时，才额外加 `--allow-resend-prod`。

## 收件人名单

`config/recipients.json` 支持名单组 ID、版本、启用状态和成员信息。每次邮件打包会在包内生成不可变的 `recipient_snapshot.json`；发送和发送记录都使用这份快照，因此不会因打包后的名单变动而错发。

本地旧格式仍能运行，但会显示为 `legacy v0`。中台部署前应迁移到示例文件中的 `schema_version: 1` 格式，并在每次修改成员时递增对应名单组的 `version`。

现有旧名单可以原样迁移并自动备份：

```bash
python3 scripts/migrate_recipient_registry.py --write
```

字段和未来数据库映射见 [收件人名单说明](docs/RECIPIENT_REGISTRY.md)。

## 选源质量诊断

每期定稿后运行 `diagnose`。它会生成：

- `selection_diagnostic.json`：自动选择、最终发布、未选池补入、手工未入池来源、自动选中但未发布、重复守卫被覆盖等差异。
- `selection_review.json`：编辑复盘模板。只需记录真正的漏选、高价值补入、误判重复和评分原因。

编辑将 `review_status` 改为 `completed` 后，运行 `replay` 才会把该期进入 `selection_replay.jsonl`。这份回放集用于调整评分和 Prompt，不直接自动改规则。

详见 [选源质量说明](docs/SELECTION_QUALITY.md)。

## 图像 Provider

- `codex_imagegen`：当前效果基线，仅适用于 Codex 本地工作流。
- `gpt_image_2` / `image2`：中台运行 Provider，目前仍处于质量候选阶段；必须经过 A/B 与单图质量审核，默认不能正式群发。
- Image2 的稳定性测试使用同一份研究图 Prompt 连续生成三次，输出候选图、拼图和 `stability_report.json`；它不改 HTML、不写正式图片资产、更不会发送邮件。

详见 [Image Provider Rollout](docs/image_provider_rollout.md) 与 [API Runtime](docs/API_RUNTIME.md)。

## 中台部署

P0 的目标是把这套流程部署成“可预览、可审批、可发送”的任务服务，不是立刻重做成知识库产品。状态机、密钥管理、最小留存和验收标准见 [中台部署说明](docs/MIDDLE_PLATFORM_HANDOFF.md)。

## 反馈服务 P0

反馈服务有两个兼容实现：

- `feedback_service/`：Python + SQLite，本地开发或公司中台持久卷部署。
- `feedback_web/`：Next.js + Supabase Postgres，面向 Vercel 的前后端线上版。

两者都提供邮件打开估算、来源链接无感跳转埋点、星级自动提交、同一期匿名去重、改分 Upsert、独立可选文本评价和管理后台。点击会过滤明显预取和安全扫描请求，后台以“有效点击”展示。邮件包启用反馈后，会生成 `messages/` 下的一人一封 EML；SMTP 发送器自动复用一个连接逐封发送。顶层 EML 和 `body.html` 只用于预览，不带追踪，避免检查邮件时污染数据。

启用前必须先把服务部署到员工邮箱客户端可访问的内部 HTTPS 地址，然后配置：

```bash
export AI_DAILY_FEEDBACK_BASE_URL='https://<平台分配的 HTTPS 域名>'
export AI_DAILY_FEEDBACK_SECRET='<secret-store-value>'
export AI_DAILY_FEEDBACK_ADMIN_KEY='<admin-key>'
python3 scripts/run_ai_daily.py package-test --date YYYY-MM-DD --require-feedback
```

设置公开域名后，邮件打包会自动启用反馈；正式发送完成后还会同步发送人数供后台计算参与率。
本地运行见 [Python 反馈服务说明](feedback_service/README.md)；Vercel + Supabase
逐步部署见 [线上反馈服务说明](feedback_web/README.md)。

## 项目结构

```text
config/     source and provider configuration; local mail config stays ignored
docs/       deployment, image-provider, and workflow notes
feedback_service/ local/container feedback implementation
feedback_web/ Vercel + Supabase feedback implementation
prompts/    source-selection and editorial JSON prompts
scripts/    deterministic selection, validation, rendering, CID packaging, and send scripts
templates/  manual source-manifest example
```
