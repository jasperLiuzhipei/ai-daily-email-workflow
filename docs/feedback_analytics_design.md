# AI 日报无感反馈与统计设计

## 用户侧体验

- 邮件展示时加载一个 1x1 远程像素，记录一次估算打开事件。
- 每篇来源链接通过签名跳转地址无感记录，再立即 302 到原文；正常用户不增加任何操作。
- 今日看点下方显示五颗星；点击任意一颗后自动完成评分。
- 星级右侧提供独立的“写一句建议”入口，文本反馈完全可选。
- 评分落地页只显示“已收到，可以关闭页面”，不会强制进入文本表单。
- 评分链接在新页面打开；保存成功后尝试自动关闭，浏览器拦截时保留成功页。

## 发送方式

现有发送器把同一封 EML 一次发送给所有收件人。独立统计需要在 SMTP
连接内逐人发送，并在发送前将 `{{RECIPIENT_TOKEN}}` 替换为每位收件人的
匿名签名 Token。Token 建议使用服务端密钥执行 HMAC，不在统计库保存明文邮箱。

## 接口

- `GET /open/{token}.gif`
  - 写入或更新打开事件，返回透明 GIF。
  - `(issue, token)` 首次出现计入独立打开，后续只累计打开次数。
- `GET /f/{token}#score={1-5}`
  - 返回极简落地页；分数只放在 URL fragment，不随 GET 请求发送给服务端。
  - 页面加载后自动 `POST /api/rating`，不要求用户再次确认。
- `POST /api/rating`
  - 校验签名、期数和 1-5 分范围。
  - 对 `(issue, token)` 执行 upsert，重复评分以最后一次为准。
- `GET /comment/{token}`
  - 打开独立的文本反馈页，不要求用户先评分。
- `POST /api/comment`
  - 接收最多 300 字的可选文本反馈。
  - 校验签名后保存；同一用户可以更新当期反馈。
- `GET /api/summary/{issue}`
  - 使用管理 Key 返回当期平均分、星级分布、文本数量和估算打开量。
- `GET /api/comments/{issue}`
  - 使用管理 Key 返回当期匿名文本反馈列表。
- `GET /click/{token}/{link_id}?u={destination}&t={title}&s={signature}`
  - 先验证整个跳转载荷签名，避免开放跳转；记录候选点击后立即 302 到原文。
  - 根据预取 Header、已知安全扫描 Header 和自动化 User-Agent 判定“有效点击”；候选请求会保留以便审计，但不计入核心 CTR。

## 最小数据表

### campaigns

- `issue_id`
- `send_date`
- `subject`
- `recipient_count`
- `sent_at`

### deliveries

- `issue_id`
- `recipient_token_hash`
- `sent_at`
- `delivery_status`

### open_events

- `issue_id`
- `recipient_token_hash`
- `first_opened_at`
- `last_opened_at`
- `open_count`

### ratings

- `issue_id`
- `recipient_token_hash`
- `score`
- `rated_at`

### comments

- `issue_id`
- `recipient_token_hash`
- `comment_text`
- `commented_at`

### click_events

- `issue_id`
- `recipient_token_hash`
- `link_id`
- `clicked_at`
- `request_count`
- `qualified_click_count`

## 聚合指标

- 估算独立打开率：`unique_open_tokens / successful_deliveries`
- 平均评分：`AVG(score)`
- 评分参与率：`unique_rating_tokens / successful_deliveries`
- 星级分布：1-5 分各自占比
- 热门原文：按 `link_id` 统计独立点击人数
- 有效点击率：`unique_qualified_click_tokens / successful_deliveries`

打开像素受图片拦截、缓存和预览窗影响，只应称为“估算打开”。有效点击会剔除明显
预取和扫描请求，比打开更可靠，但无法证明用户已经阅读原文。管理页面默认只展示聚合数据，不展示个人阅读排行。
