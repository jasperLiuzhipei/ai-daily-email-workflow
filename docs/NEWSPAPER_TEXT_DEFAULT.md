> 2026-09-15 后续确认：有研究内容时采用 B 版墨线机制插画，强调色随内容变化，见 [当前图解默认规则](NEWSPAPER_ILLUSTRATED_DEFAULT.md)。以下保留9月14日纯文字规则及发送记录。

# 日报默认模板：纯文字报纸版

2026-09-14 用户确认采用；服务器已部署。默认生成入口仍为工作日 prepare → preview，prepare 在本模式跳过生图调用。研究内容继续保留，HTML_READY 无需研究图片；旧日期保持旧流程。

- 策略：config/newspaper_presentation.json；effective_date = 2026-09-14。
- 样式：templates/newspaper_text.html；渲染器：scripts/newspaper_text.py。
- 配色：config/newspaper_accent_palette.json；七色循环，按刊期固定。朱砂红、梅子紫、钴蓝、灰调亮橙、灰调鸢尾紫、灰调钻石蓝、灰调蒂芙尼蓝。草色不收录。
- 刊头：原有宋体字体，AI 字号为 1.22em、下调 3px。大标题与 AI 用强调色，小字用同色系较深色。
- 正文展示刊期编号；邮件主题为 AI 情报雷达｜{year}年第{week}周，按 ISO 周数计算。
- 纯文字版无研究图片、SVG、Canvas 或脚本依赖。采用内联样式和表格适配邮件；评分反馈继续在发送包中个性化。
- 今日已审核文案覆盖保存在 daily_runs/2026-09-14/newspaper_editorial.json，绑定内容签名。未来刊期从当日正文动态渲染，不复用今天的研究或新闻。
- 发送校验：检查日期、模式、HTML 哈希及无图无脚本；有图旧版的图片审核不变。
- 验证：桌面/390px 手机浏览器检查，六项回归检查通过。真实邮件客户端显示以单人测试为准。

用户先确认向 reader@example.com 单独测试，随后明确批准正式发送。2026-09-14 正式名单 29 人均由 SMTP 接受，反馈投递分母随后同步成功。后续新增 V2 开发版处于独立影子阶段，参见 V2_SHADOW_RELEASE.md。
