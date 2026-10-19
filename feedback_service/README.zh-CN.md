# AI Daily Feedback P0

[English](README.md) | **简体中文**

一个无第三方 Python 依赖的内部反馈服务，提供：

- 点击邮件星级后自动记录，无确认按钮。
- 评分成功后尝试自动关闭新页面；浏览器拦截时保留成功页兜底。
- 同一期、同一匿名收件人只保留一条最终评分；再次点击会更新。
- 请求 `event_id` 幂等，避免重试造成重复事件。
- 独立、可选的 300 字文本建议页。
- SQLite 汇总接口和估算打开像素。
- 来源链接无感跳转埋点，并过滤明显预取和安全扫描请求。
- 自动同步正式发送台账，只导入期次、发送时间和人数，不保存邮箱地址。
- 带密码保护的内部数据后台，按发送时间查看打开率、有效点击率、评分率和文本意见。

这个目录适合本地开发或“可挂载持久卷”的服务器容器。Vercel 是无状态运行环境，
不要把这个 SQLite 服务原样部署到 Vercel；Vercel + Supabase 版本位于
[`feedback_web/`](../feedback_web/README.zh-CN.md)。

## 本地运行

```bash
export AI_DAILY_FEEDBACK_SECRET='<至少16字符的随机签名密钥>'
export AI_DAILY_FEEDBACK_ADMIN_KEY='<独立的管理密钥>'
python3 -m feedback_service.server
```

打开 `http://127.0.0.1:8765/admin`，浏览器弹出认证框时用户名可任意填写，
密码填写 `AI_DAILY_FEEDBACK_ADMIN_KEY`。默认会同步项目根目录下的
`daily_runs/send_ledger.json`；其他位置可通过 `AI_DAILY_SEND_LEDGER` 或
`--send-ledger` 指定。

另开终端生成测试链接：

```bash
python3 -m feedback_service.create_token \
  --issue 2026-07-17 \
  --recipient reader@example.com
```

汇总：

```bash
curl http://127.0.0.1:8765/api/summary/2026-07-17 \
  -H "X-Admin-Key: $AI_DAILY_FEEDBACK_ADMIN_KEY"

# 查看当期匿名文本建议
curl http://127.0.0.1:8765/api/comments/2026-07-17 \
  -H "X-Admin-Key: $AI_DAILY_FEEDBACK_ADMIN_KEY"

# 查看全部期次，按正式发送时间倒序
curl http://127.0.0.1:8765/api/issues \
  -H "X-Admin-Key: $AI_DAILY_FEEDBACK_ADMIN_KEY"
```

## 容器

```bash
docker build -f feedback_service/Dockerfile -t ai-daily-feedback .
docker run --rm -p 8765:8765 \
  -e AI_DAILY_FEEDBACK_SECRET='<secret-store-value>' \
  -e AI_DAILY_FEEDBACK_ADMIN_KEY='<admin-key>' \
  -e AI_DAILY_SEND_LEDGER='/runtime/send_ledger.json' \
  -v ai_daily_feedback:/data \
  -v /path/to/send_ledger.json:/runtime/send_ledger.json:ro \
  ai-daily-feedback
```

正式部署时由中台注入密钥并挂载持久卷。评分链接格式为
`/f/<signed-token>#score=<1-5>`；分数位于 URL fragment 中，普通链接扫描请求不会把分数发送给服务端。

## 有持久卷的平台配置

当前服务同时提供评分页、文本反馈页、统计 API 和后台页面，不需要再单独部署一套前端。
把 `platform.env.example` 中的变量录入部署平台，其中只有一个公开地址：

```bash
AI_DAILY_FEEDBACK_BASE_URL=https://<平台分配的 HTTPS 域名>
```

邮件生成端与服务端都使用这个域名。路由由 `feedback_service/routes.py` 统一管理：

- 健康检查：`/healthz`
- 服务描述：`/.well-known/ai-daily-feedback.json`
- 星级承接页：`/f/<signed-token>#score=<1-5>`
- 文字建议页：`/comment/<signed-token>`
- 打开像素：`/open/<signed-token>.gif`
- 来源点击跳转：`/click/<signed-token>/<link-id>?u=<destination>&t=<title>&s=<signature>`
- 数据后台：`/admin`
- 正式发送人数同步：`POST /api/delivery`
- 期次列表：`GET /api/issues`
- 单期汇总：`GET /api/summary/<issue-id>`
- 单期意见：`GET /api/comments/<issue-id>`

部署平台应将服务挂在域名根路径，并挂载持久化的 `/data` 目录。平台若自动注入
`PORT`，服务会直接使用；也可通过 `AI_DAILY_FEEDBACK_PORT` 覆盖。上线后执行：

```bash
python3 -m feedback_service.check_deployment
```

它会检查健康接口和服务描述，并打印邮件、后台和 API 的全部最终地址。

## 接入邮件发送链

反馈服务与邮件打包器必须使用同一个 `AI_DAILY_FEEDBACK_SECRET`。仓库中的
`config/email_settings.example.json` 引用了 `AI_DAILY_FEEDBACK_BASE_URL`；复制为本地配置并部署完成后，在
邮件生成环境注入公开域名和签名密钥，反馈会自动启用，不需要再改 JSON。

随后运行测试包检查：

```bash
export AI_DAILY_FEEDBACK_SECRET='<secret-store-value>'
python3 scripts/run_ai_daily.py package-test --date YYYY-MM-DD --require-feedback
python3 scripts/run_ai_daily.py test --date YYYY-MM-DD --skip-build --require-feedback --ask-password --send
```

启用后，包内 `messages/` 保存每名收件人的真实发送 EML；顶层 EML 和 `body.html`
是无追踪预览。自动 SMTP 发送器会读取 manifest 并逐封发送，不能手工群发顶层 EML 代替。
正式发送成功后，workflow 会使用 `AI_DAILY_FEEDBACK_ADMIN_KEY` 调用 `/api/delivery`
同步发送人数；即使线上同步短暂失败，邮件发送和本地发送台账也不会回滚。

交互成本：打开统计和来源点击统计均不需要额外动作；来源链接会立即 302 跳转原文。星级只需一次点击，成功页会尝试自动关闭；文字意见是独立可选入口，提交后也会尝试关闭。邮件 HTML 本身不能执行 JavaScript，因此星级点击仍会短暂打开浏览器页面。

后台的打开率是估算值：部分邮件客户端会拦截图片或使用图片代理。有效点击会排除明显预取和安全扫描请求，但仍不能等同于用户已阅读原文。所有后台时间统一显示为北京时间；评分与文本意见按“同一期、同一匿名收件人”去重。

收件人标识是按期次隔离的假名 ID，不等于绝对匿名；签名密钥须保密。服务器与代理日志仍可能包含 IP、User-Agent 和时间，应按部署需求配置权限和保留期限。

## 许可证

本模块属于 AI Daily Email Workflow，采用 [MIT 许可证](../LICENSE)。
