# AI Daily Feedback Web

Vercel + Supabase 线上反馈服务，兼容当前邮件工作流已经生成的链接协议：

- `/f/<signed-token>#score=<1-5>`：点击星级后自动提交，可再次修改。
- `/comment/<signed-token>`：可选文字建议，最多 300 字。
- `/open/<signed-token>.gif`：匿名打开估算像素。
- `/click/<signed-token>/<link-id>`：无感记录来源链接请求后 302 跳转原文；链接参数整体签名，不能被改造成开放跳转。
- `/admin`：按期次查看估算打开率、有效点击率、评分率、均分、单篇点击和文字意见。
- `/api/delivery`：正式发送后同步发送人数，作为参与率分母。

原始邮箱地址不会发往 Vercel 或 Supabase。邮件打包器只把邮箱变成“按期次隔离的
HMAC 匿名 ID”，数据库无法反推出邮箱。

## 架构

```text
Email HTML
  -> Vercel pages and API routes
  -> Supabase Postgres
  -> protected /admin dashboard
```

本地 `feedback_service/` 仍保留为 SQLite 开发/中台容器版；`feedback_web/`
是面向 Vercel 的线上版。二者使用同一套签名协议，但不会同时承接同一封邮件。

## 1. 创建 Supabase 数据库

1. 在 Supabase 创建项目，区域按公司合规要求选择。
2. 打开项目的 SQL Editor。
3. 按文件名顺序复制并运行 `supabase/migrations/` 下的全部 SQL；已有项目只需新增执行
   `003_click_tracking.sql`。
4. 在项目设置/API Keys 中记录：
   - Project URL，写入 `SUPABASE_URL`。
   - Server-side Secret Key（`sb_secret_...`），写入
     `SUPABASE_SECRET_KEY`。
5. 不需要在浏览器端使用 Publishable Key。本项目所有数据库访问都经过 Vercel
   服务端 API。

数据库包含六张表：

| 表 | 用途 |
| --- | --- |
| `deliveries` | 每期正式发送人数和时间 |
| `open_events` | 每期匿名收件人的首次/末次打开与次数 |
| `click_events` | 每期匿名收件人对每篇来源的候选/有效点击次数 |
| `ratings` | 每期匿名收件人的最终星级 |
| `comments` | 每期匿名收件人的最终文字建议 |
| `feedback_events` | 请求幂等记录，防止网络重试重复写入 |

## 2. 创建 Vercel 项目

推荐先把仓库推到 GitHub，再在 Vercel 中：

1. `Add New -> Project`，导入仓库。
2. Root Directory 选择 `feedback_web`。
3. Framework Preset 保持 `Next.js`。
4. 在 Environment Variables 中添加：

```text
SUPABASE_URL
SUPABASE_SECRET_KEY
AI_DAILY_FEEDBACK_SECRET
AI_DAILY_FEEDBACK_ADMIN_KEY
```

`AI_DAILY_FEEDBACK_SECRET` 必须与日报邮件打包环境完全相同；否则邮件里的签名链接
无法验证。`AI_DAILY_FEEDBACK_ADMIN_KEY` 是另一把独立密码，不要与 SMTP 密码、
Supabase Secret 或签名 Secret 复用。

环境变量建议同时配置到 Preview 和 Production，测试/正式环境如果共用数据库，
必须清楚标记测试期次；更稳妥的方式是后续建立独立的 Preview Supabase 项目。

## 3. 部署和自检

在 Vercel 点击 Deploy，获得 `https://<project>.vercel.app` 后，本地运行：

```bash
cd /Users/jasperliuzp/Desktop/workat_THS/idea_opc/ai_daily_workflow
export AI_DAILY_FEEDBACK_BASE_URL='https://<project>.vercel.app'
export AI_DAILY_FEEDBACK_SECRET='<与 Vercel 相同的签名 Secret>'
export AI_DAILY_FEEDBACK_ADMIN_KEY='<与 Vercel 相同的后台 Secret>'

python3 -m feedback_service.check_deployment
python3 -m feedback_service.create_token \
  --issue 2026-07-23 \
  --recipient liuzhipei@myhexin.com
```

打开脚本打印的评分、建议和后台地址，完成一次测试：

1. 点击 4 星，后台应显示一条评分。
2. 再点击 5 星，评分人数不变，最终分数更新为 5。
3. 写一条建议，后台应显示一条文字意见。
4. 请求一次打开像素，估算打开人数增加。
5. 点击一条来源链接，浏览器应立即跳转原文；后台的“有效点击”与对应单篇数据增加。

## 4. 接回邮件工作流

本机 `.env` 中配置：

```text
AI_DAILY_FEEDBACK_BASE_URL=https://<project>.vercel.app
AI_DAILY_FEEDBACK_SECRET=<与 Vercel 相同>
AI_DAILY_FEEDBACK_ADMIN_KEY=<与 Vercel 相同>
```

不想手动编辑根目录 `.env` 时，在 Vercel 部署并获得公开 URL 后运行：

```bash
python3 feedback_web/scripts/configure_feedback_env.py \
  --sync-workflow \
  --feedback-base-url https://<project>.vercel.app
```

先只给自己发送：

```bash
cd /Users/jasperliuzp/Desktop/workat_THS/idea_opc/ai_daily_workflow
python3 scripts/run_ai_daily.py package-test \
  --date YYYY-MM-DD \
  --require-feedback

python3 scripts/run_ai_daily.py test \
  --date YYYY-MM-DD \
  --skip-build \
  --require-feedback \
  --ask-password \
  --send
```

验证邮件打开、星级、改分、文字建议和 `/admin` 后，再正式发送：

```bash
python3 scripts/run_ai_daily.py prod \
  --date YYYY-MM-DD \
  --skip-build \
  --require-feedback \
  --ask-password \
  --send \
  --confirm-prod
```

正式发送成功后，现有 workflow 会自动调用 `/api/delivery`，同步当期正式发送人数。
如果没有这个分母，后台仍能看到事件数量，但无法计算打开率、有效点击率和评分率。

## 5. 本地构建

```bash
cd feedback_web
python3 scripts/configure_feedback_env.py \
  --project-url https://<your-project>.supabase.co
npm install
npm run typecheck
npm run build
npm run dev
```

不要提交 `.env.local`、Vercel `.vercel/` 或任何 Secret。

## 隐私与边界

- 打开率是估算值。Foxmail、图片代理、缓存或禁图设置都可能造成偏差。
- 有效点击率会过滤已知预取和安全扫描请求，但无感跳转无法绝对识别所有自动化访问；它应被视为比打开率更可靠的运营指标，而非“已阅读原文”证明。
- Vercel/Supabase 属于外部 SaaS。正式收集公司员工行为数据前，应完成公司安全、
  数据合规和外部服务审批。
- 当前数据库不保存邮箱明文，但服务提供商日志仍可能包含 IP、User-Agent 和访问时间。
- 如果外部 SaaS 不获批准，可把相同页面/API 和 SQL 迁到公司中台 + 内部 Postgres；
  邮件链接协议无需改变。
