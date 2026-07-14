# AI Daily Email Workflow

面向内部 AI 情报日报的可部署邮件工作流：选源、内容 JSON、研究图质量门禁、Foxmail 兼容 HTML、CID 内联图片和受控 SMTP 发送。

## 保留的主链

```text
Source selection -> Editorial JSON -> Research image -> HTML -> CID package -> SMTP send
```

- 选源：AIHot 主/全量/论文 feed、AIHot Daily 编辑预筛、BestBlogs 和配置化官方研究源。
- 内容：研究前沿使用结构化解读；新闻使用轻量但带业务启发的桥接字段。
- 图片：只为研究前沿生成；图片必须通过来源、Prompt、Hash 与人工质检门禁。
- 邮件：本地图片被打包为 CID 内联附件，收件人不依赖远程图床。
- 去重：只以成功正式发送的日报作为下一期新闻去重基线。

## 仓库边界

这个仓库只保存代码、提示词、模板和示例配置。

不提交：真实 SMTP 配置、收件名单、密码、日报运行产物、研究图、HTML、EML 和日志。它们由本地运行目录或未来中台的对象存储保存。

## 首次配置

```bash
cp config/email_settings.example.json config/email_settings.json
cp config/recipients.example.json config/recipients.json
cp config/editor_settings.example.json config/editor_settings.json
```

核心脚本只使用 Python 标准库。若启用 API 图像 Provider：

```bash
python3 -m pip install -r requirements.txt
```

系统还需要 `curl` 用于抓取公开 RSS/HTML 源。

## 每日入口

```bash
# 查看某期工作流文件是否齐全
python3 scripts/run_ai_daily.py status --date YYYY-MM-DD

# 仅检查测试/正式名单组的状态、版本、人数和指纹，不显示邮箱
python3 scripts/run_ai_daily.py recipients

# 自动选源：新闻至少 5 条、不设上限；输出会显示预估阅读时长
python3 scripts/run_ai_daily.py select --date YYYY-MM-DD

# 内容定稿后，诊断自动选源与最终发布之间的差异，并生成编辑复盘模板
python3 scripts/run_ai_daily.py diagnose --date YYYY-MM-DD

# 仅将“已完成编辑复盘”的期次汇总为评分回放集
python3 scripts/run_ai_daily.py replay

# 在内容 JSON 与研究图审核完成后，构建 HTML
python3 scripts/run_ai_daily.py build --date YYYY-MM-DD --theme digest_navy --news-layout paper

# 发测试邮件
python3 scripts/run_ai_daily.py test --date YYYY-MM-DD --skip-build --ask-password --send

# 正式发送：必须明确确认，成功后才写入去重与期数记录
python3 scripts/run_ai_daily.py prod --date YYYY-MM-DD --skip-build --ask-password --send --confirm-prod
```

每次 HTML 构建完成后，脚本会打印对应日期的正式发送命令。

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
- `gpt_image_2` / `image2`：API 候选 Provider，适合未来中台接入；必须经过 A/B 与单图质量审核，默认不能正式群发。

详见 [Image Provider Rollout](docs/image_provider_rollout.md)。

## 中台部署

P0 的目标是把这套流程部署成“可预览、可审批、可发送”的任务服务，不是立刻重做成知识库产品。状态机、密钥管理、最小留存和验收标准见 [中台部署说明](docs/MIDDLE_PLATFORM_HANDOFF.md)。

## 项目结构

```text
config/     source and provider configuration; local mail config stays ignored
docs/       deployment, image-provider, and workflow notes
prompts/    source-selection and editorial JSON prompts
scripts/    deterministic selection, validation, rendering, CID packaging, and send scripts
templates/  manual source-manifest example
```
