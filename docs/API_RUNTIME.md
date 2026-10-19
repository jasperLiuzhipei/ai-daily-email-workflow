# API 运行时部署

线上日报不再需要 Codex 会话。运行时分为两类 API Provider：

- 文本：选源后的研究解析与日报内容 JSON，默认模型 `gpt-6-sol`。
- 图像：研究图候选生成，默认模型 `gpt-image-2`。

抓源、去重、评分、JSON 校验、HTML 渲染、CID 内联、邮件打包和 SMTP 发送仍是本地确定性步骤，不调用模型。

## 配置与密钥

首次在运行环境创建本地配置：

```bash
cp config/ai_runtime.example.json config/ai_runtime.json
```

`config/ai_runtime.json` 只保存环境变量名、默认模型和超时，不保存真实 Key 或真实网关地址。它已被 `.gitignore` 排除。脚本会自动读取仓库内被忽略的 `.env`；虚拟机则应由密钥管理注入同名环境变量。

文本路由目前使用 Chat Completions 生成 JSON，不调用工具。`gpt-6-sol` 的请求配置为 `reasoning_effort: medium` 和 `max_completion_tokens: 12000`；推理模式下不传 `temperature`。

文本与图片共用中转服务时，只需要：

```bash
AI_DAILY_API_KEY=<relay-or-service-key>
AI_DAILY_API_BASE_URL=https://<approved-gateway>/v1
AI_DAILY_TEXT_MODEL=gpt-6-sol
AI_DAILY_IMAGE_MODEL=gpt-image-2
```

如果文本与图片走不同网关，再分别设置 `AI_DAILY_TEXT_BASE_URL` 和 `AI_DAILY_IMAGE_BASE_URL`，它们会覆盖共享地址。迁移期仍兼容 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，新部署不建议继续使用旧变量名。不要把 Key、SMTP 密码、收件人明文或内部文章原文提交到 Git。

如果中转网关不属于公司受控网络，先获得数据出域与合规确认。日报可能包含内部编辑判断和邮件名单相关元数据；即使只传公开链接，也不要默认把未审查的原文材料发送到第三方网关。

## Image2 备用路由与服务器锁定

排障或本地候选测试时，主图片网关请求失败后，工作流可以改用 `config/ai_runtime.image_fallback.json`。它只会在以下三个变量都已配置时启用，不影响文本模型：

```bash
AI_DAILY_IMAGE_FALLBACK_API_KEY=<fallback-key>
AI_DAILY_IMAGE_FALLBACK_BASE_URL=https://<fallback-gateway>/v1
AI_DAILY_IMAGE_FALLBACK_MODEL=gpt-image-2
```

当前备用适配支持两种图片返回：`data[0].b64_json` 与 `data[0].url`。URL 模式不会把临时 URL 写进日报；程序仅从配置白名单中的 HTTPS 域名下载图片字节，再执行 PNG/JPEG/WebP、尺寸和邮件资产校验。默认 Grsai 配置只允许 `*.aitohumanize.com`，下载上限为 32 MiB，且不会把主网关的 Bearer Key 转发给该图片 URL 或重定向目标。

定时服务器不应在生产候选图中静默切换路由。为保证每天的 Image2 来自同一条、已验收的服务链路，定时 service 使用 `--strict-image-route`：主路由不可用时任务失败并保留日志，不会生成来自备用网关的图片。将已经通过三次稳定性测试和人工 A/B 的 Image2 网关填入 `AI_DAILY_IMAGE_*`；`AI_DAILY_IMAGE_FALLBACK_*` 仅保留给人工排障。

可以单独检查备用配置，不调用模型：

```bash
python3 scripts/check_ai_runtime.py \
  --runtime-config config/ai_runtime.image_fallback.json \
  --kind image
```

## API 步骤

在工作流根目录先做不联网检查：

```bash
python3 scripts/run_ai_daily.py check-api
```

部署验收时才使用 `check-api --live-models` 调用 `GET /models`。日报主链运行：

```bash
# 一次完成自动选源、一手源材料、两次 gpt-6-sol 结构化生成和 Image2 候选图。
python3 scripts/run_ai_daily.py prepare --date YYYY-MM-DD --strict-image-route

# 生成候选图比较版 HTML，不进入生产图片门禁。
python3 scripts/run_ai_daily.py preview --date YYYY-MM-DD --theme digest_navy --news-layout paper
```

`api-compose` 和 `api-image` 仍保留为排障用的底层分步命令，不再作为日常主入口。

文本阶段产物：

```text
daily_runs/YYYY-MM-DD/source_material.json
daily_runs/YYYY-MM-DD/research_brief.parsed.json
daily_runs/YYYY-MM-DD/research_brief.md
daily_runs/YYYY-MM-DD/research_image_prompt.txt
daily_runs/YYYY-MM-DD/ai_daily_email_content.json
```

图片阶段产物：

```text
daily_runs/YYYY-MM-DD/research_image_image2_candidate.png
daily_runs/YYYY-MM-DD/research_image_image2_generation.json
```

每一步默认复用完整已有产物，不会静默重复扣费。需要重新调用模型时才显式添加：

```bash
--refresh-source-materials
--refresh-api-content
--refresh-api-image
```

## Image2 门禁

Image2 当前是候选 Provider。主网关使用 `response_format: "b64_json"`；备用网关可使用 `response_format: "url"`，但临时 URL 只用于受限下载，最终仍直接落盘为私有项目图片。

候选图需要：

1. 同一份 `research_image_prompt.txt` 与当前质量基线人工对比。
2. 检查机制、文字、证据、边界、配色和邮件缩放可读性。
3. 在图片 manifest 中写入比较结论后，才可以提升为 production Provider。

正式 `prod --send` 仍会拦截未经批准的候选图。这样即使文本和图片都已 API 化，也不会把未经验证的图片自动群发。

## 虚拟机验收

在测试虚拟机先分别验证：

1. 文本网关 `/v1/models` 是否能看到配置的文本模型（当前默认 `gpt-6-sol`）。
2. 图像网关可用时再检查 `/v1/models`；部分兼容网关未实现该接口，不能以此作为唯一验收依据。
3. `prepare` 能生成研究与新闻 JSON，并保持来源 URL 由脚本回填。
4. Image2 能返回 `data[0].b64_json` 或受白名单约束的 `data[0].url`，并生成有效 PNG 和 manifest。
5. 测试名单能收到 CID 内联图的邮件。

手工排障时可以不带 `--strict-image-route` 以测试备用网关；定时任务不得这样运行。即使图片 API 生成成功，正式发送仍不会绕过人工审核。
