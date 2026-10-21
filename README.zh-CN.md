# AI Daily Email Workflow

[English](README.md) | **简体中文**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> 当前正式选源规则：[事件评分与选源逻辑](docs/SOURCE_SELECTION_LOGIC.md)。2026-09-18 起默认使用正文事件评分；这与下方 `daily_v2` 的中台影子工作区是两项不同改造。

> 新版开发入口：[`docs/V2_SHADOW_RELEASE.md`](docs/V2_SHADOW_RELEASE.md)。V2 影子工作区已可运行；现有正式制作与发送入口保持原状态。完整需求基线为 PRD 1.4。

面向内部 AI 情报日报的可部署邮件工作流：选源、API 内容 JSON、研究图质量门禁、Foxmail 兼容 HTML、CID 内联图片和受控 SMTP 发送。

仓库首页及反馈模块说明提供中英文版本，默认显示英文。现有编辑提示词、日报模板和反馈界面面向中文读者；文档语言切换不改变日报生成语言。详细设计资料的语言见[文档索引](docs/README.zh-CN.md)。

2026-10-12 新增[邮件 AI 自审与失败恢复](docs/EMAIL_SELF_REVIEW.md)：定时生成和正式发送前审查最终邮件，最多 3 轮尝试，耗尽后仅向编辑邮箱发送原因和建议。

2026-10-19 更新[采集、审核输入与 trace 计数](docs/TRACE_OPTIMIZATION_2026-10-19.md)：修复压缩 RSS 解析，保留证据文本并压缩审核元数据，明确区分待评估与已评估暂缓。原模型独立复审通过，当期输入 token 实测减少 28.26%。新闻数量策略见[新闻预算](docs/NEWS_VOLUME_BUDGET.md)。

## 保留的主链

```text
Source selection -> API editorial JSON -> Research image -> HTML -> CID package -> SMTP send
```

- 选源：AIHot 主/全量/论文 feed、AIHot Daily 编辑预筛、BestBlogs 和配置化官方研究源。
- 内容：研究前沿使用结构化解读；新闻使用轻量但带业务启发的桥接字段。
- 图片：只为研究前沿生成；图片必须通过来源、Prompt、Hash 和配置的质量门禁。
- 邮件：研究图被打包为 CID 内联附件；启用反馈后按收件人生成匿名个性化邮件，阅读像素和来源链接埋点是仅有的远程请求。
- 去重：只以成功正式发送的日报作为下一期新闻去重基线。

## 仓库边界

这个仓库只保存代码、提示词、模板和示例配置。

不提交：真实 SMTP 配置、收件名单、密码、日报运行产物、研究图、HTML、EML 和日志。它们由本地运行目录或未来中台的对象存储保存。

## 首次配置

需要 Python 3.11 或更新版本及 `curl`；可选反馈前端还需要 Node.js 和 npm。模型名称和请求参数须按所用服务配置，示例不保证所有服务均提供对应模型。

```bash
git clone https://github.com/jasperLiuzhipei/ai-daily-email-workflow.git ai_daily_workflow
cd ai_daily_workflow

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

将 `YYYY-MM-DD` 替换为刊期。生成命令会调用所配置的外部服务，可能产生费用。开源版本的 `config/email_quality_policy.json` 默认将 `automatic_send_enabled` 设为 `false`，授权字段为空；部署者验证测试名单后，才自行启用自动发送并记录授权。安装代码不会自动启用定时器。

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

单来源 arXiv 摘要评估默认每批最多 4 篇，共享同一套编辑规则；保留完整摘要，只去除明确识别的元数据和导航尾部。每篇仍独立评分、校验来源与逐字引用，保留异常高分复核和单篇失败重试。批次 trace 位于 `event_editorial/paper_batches/`；多篇引用同一请求时，usage 应按 request ID 去重。将编辑策略的 `paper_batch_size` 设为 `1` 可恢复逐篇请求。

对 119 篇论文的离线回放，用 `o200k_base` 估算输入消息 token 减少 **66.1%**（119 个初评请求变为 30 批）。已记录的证据和判断通过回放；这不是新模型判断质量验证或账单实测，未计输出、重试和供应商缓存计费。抓取错误日志包含 URL、HTTP 状态、重试次数及耗时，并对 URL 中的凭据字段脱敏。

另用原模型对 4 篇论文小样本实测：相比此前各自的首轮请求，总 token **25,961 → 10,764（减少 58.5%）**，输入 **20,352 → 6,358（减少 68.8%）**。全部来源与引用校验通过，包括此前 source ID 写错的样本。评分和暂缓理由有变化；小样本不证明全量判断不变，也不保证每期都有相同降幅。

## 图像 Provider

- `codex_imagegen`：用于具有对应工具能力的本地工作流。
- `gpt_image_2` / `image2`：API 生图通道；正式使用须满足当期来源绑定、图片和邮件质量门禁。
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
本地运行见 [Python 反馈服务说明](feedback_service/README.zh-CN.md)；Vercel + Supabase
逐步部署见 [线上反馈服务说明](feedback_web/README.zh-CN.md)。反馈采用按期次隔离的假名 ID，签名密钥须保密；打开和点击指标不等于用户已阅读原文。

## 部署模板

`deploy/systemd/` 使用通用的 `ai-daily` 系统账号和以下目录布局。启用 timer 前，应按实际环境调整账号、目录、环境文件、工作日历和时间表。

```text
/opt/ai-daily/
├── .venv/
├── ai_daily_workflow/     # 本仓库
├── daily_runs/
├── output/
└── email_packages/
```

macOS 预览工具通过 `AI_DAILY_PREVIEW_SERVER` 接收 SSH 目标，通过 `AI_DAILY_REMOTE_ROOT` 指定远程工作目录（默认 `/opt/ai-daily`）：

```bash
AI_DAILY_PREVIEW_SERVER='ai-daily@your-server.example.com' \
  zsh deploy/mac/ai-daily-preview YYYY-MM-DD
```

## 开发验证

在仓库根目录执行：

```bash
python3 -m unittest discover -s tests
cd feedback_web
npm ci
npm run typecheck
```

Python 测试使用临时数据和模拟服务，传输测试会启动本机 HTTP 服务。贡献方式见[贡献指南](CONTRIBUTING.zh-CN.md)，安全问题见[安全说明](SECURITY.md#简体中文)。

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

## 许可证

项目代码及原创文档采用 [MIT 许可证](LICENSE)。第三方依赖及引用来源内容保留各自的许可和权利。
