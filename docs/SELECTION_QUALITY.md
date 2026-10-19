# 选源质量诊断与回放集

## 目的

把“这条为什么没有选到”“这条为什么重复了”“这条为什么值得破格加入”变成可检查的样本，而不是每次临时修改关键词或权重。

## 每期流程

1. 运行 `select`，保留完整候选池、已选、拒选和重复剔除结果。
2. 编辑完成日报内容 JSON 后运行：

   ```bash
   python3 scripts/run_ai_daily.py diagnose --date YYYY-MM-DD
   ```

3. 阅读 `selection_diagnostic.json`，并补充同目录的 `selection_review.json`：
   - `manual_promotions`：最终发出、但自动选择没有选中的内容及原因。
   - `auto_selected_drops`：自动选中、最终没有发出的内容及原因。
   - `missed_high_value`：候选池里完全没有出现、但应被收录的内容。
   - `false_duplicate_reports`：被错误判重的内容。
   - `scoring_feedback`：评分异常及希望修正的方向。
4. 确认后将 `review_status` 改为 `completed`。
5. 定期运行 `replay` 汇总已复盘期次，再依据样本调整 Prompt、来源或规则。

## 诊断指标

- 自动命中率：最终发布内容中，自动已选的占比。
- 候选池漏选：最终发布内容在候选池内但没有自动选中的数量。
- 候选池盲区：最终发布内容在候选池中不存在的数量。
- 自动误选：自动选中但最终被编辑放弃的数量。
- 重复守卫误伤：最终发布内容曾被判为重复的数量。

指标用来定位问题：低自动命中率优先看评分；候选池盲区优先加源或改抓取；自动误选优先改阈值/分类；重复守卫误伤优先改相似度策略。

## 时间窗口与内容路由

- 普通新闻仍使用 48 小时滚动窗口，避免周一把整个周末的普通资讯重新堆进日报。
- 研究前沿按研究贡献而非研究语气识别：新方法、新 Benchmark、新数据集、新评测框架或新技术报告可进入 72 小时研究回看；仅报道某模型在现有基准上强弱的内容仍属于行业动态。
- 仅有二手媒体来源的重分类研究会恢复进入候选池，但研究分暂时封顶在 `selection.secondary_research_score_cap`；找到官方报告或一手研究链接后才解除，防止补漏规则默认挤掉更可靠的一手研究。
- 当上一期正式发送时间早于普通新闻窗口时，开启“重大事件断档审计”。该补漏池只接纳达到高分门槛的通用大模型大版本或重大安全/信任事件，仍经过历史去重。
- 回放数据中的 `content_research_route_reason` 与 `gap_audit_reason` 用于确认候选为何破例进入研究池或断档补漏池。

## 回放集原则

- 只纳入 `review_status: completed` 的期次，避免未经确认的草稿污染评估。
- 不自动用回放集改分；先看至少 10-20 个明确样本是否呈现同一类系统性偏差。
- 每条规则调整都要留下“对应样本、改动目的、预期副作用”，然后用后续期次回放验证。

## 未发布高价值内容补漏（2026-09-10）

`daily_runs/editorial_backlog.json` 保留近7天的高价值未发布候选。每天选源前从已有选源、最终正文、正式发送账本恢复状态；原始新闻分至少80、具备研究资格且研究分至少65，或已被选中的稿件均可进入记录。每日最多召回12个历史候选重新评分，不额外突破正式选稿的来源门槛、版面上限或事实核验。

状态包含 `pending_review`、`needs_source_review`、`generation_failed`、`draft_ready`、`published`、`dismissed`、`expired_review`。原始日期不改成今天；超过7天只转为待审过期，保留原因，不当作已报道。预览或未发送的正文不关闭补漏条目；只有正式发送账本中的实际卡片身份能够标记 `published`。

正文模型校验失败时，允许一次带具体失败原因的修复调用，仍失败则保留诊断和补漏记录。不会把RSS原始标题或占位摘要直接塞进日报。模型编辑同时收到有界的原文证据，允许中文中的英文论文名、模型名与基准名。

新发送记录保存最终卡片的来源身份快照。旧发送记录兼容读取其最终正文，但只读取 `news` / `paper` / `research_items`，不递归读取 `generation.dropped_cards`，以免把丢稿误认成已发布。`coverage_source_urls` 用来保存经过编辑核实的原始链接别名；不能把无关事件的URL放进去抵消漏报。

只查看或重建队列：

```bash
python scripts/editorial_backlog.py --date YYYY-MM-DD
```

编辑明确放弃或延期时须留原因：

```bash
python scripts/editorial_backlog.py --date YYYY-MM-DD --resolve-id ID --decision dismiss --reason '具体编辑原因'
python scripts/editorial_backlog.py --date YYYY-MM-DD --resolve-id ID --decision defer --defer-until YYYY-MM-DD --reason '等待原文或独立验证'
```

回归样本：9/10 Terminal-Universe 成稿失败、9/9 Images 2.5 汇总标题失败、Mantis 二手来源79分封顶、AIHot新域名，以及未发送草稿不应参与历史去重。

补漏发现入口（2026-09-10）：Google Developers 使用实测的日期兄弟卡片解析，新闻回看72小时；OpenAI Newsroom同样启用72小时回看。arXiv增加独立且有上限的定向发现：cs.AI/cs.CL中标题含agent、agents、memory、evaluation、reasoning的最新120篇，按原始submitted/published时间过滤到研究窗口，仍接受原有评分、相关性与去重门槛。DAIR周刊列表有发布滞后，本次直接用arXiv补足对应研究发现，不把DAIR页面日期冒充论文首发时间。

边界：补漏队列保证已发现的高价值候选有后续状态，不承诺全网信息零遗漏；定向arXiv也有主题和数量上限。来源抓取失败仍写入source_selection的source health/errors。生成失败只进行一次有针对性的修复，再失败保留待审，不靠降低中文质量门槛、来源可靠性上限或改写时间强行发稿。


## 2026-09-18：放开数量、先核验、发送前覆盖复核

本节取代上文关于12条封顶、同主题名额及阅读时长抬分的历史说明。selection.max_news_count=null；news_count_mode=quality_threshold；enforce_group_limits=false。所有新闻分达到76的独立事件进入正文候选，5条只是保留的最低数量目标，10分钟只作估算。显式CLI --max-news-count仍可用于人工实验，不是定时任务默认。去重、时效、来源边界和硬过滤保持。

source_verification.py在最终选源前读取高价值候选的官方正文，默认并发4、最多24条候选，每条最多尝试3条已发现链接，每次12秒。超出网络预算明确not_attempted_budget，进入覆盖复核；网络预算不充当出版篇数上限。官方域名不是核验通过：需HTTP成功、正文可读且与事件匹配，保留URL、内容hash、文本与尝试记录。Qwen动态博客适配公开GET /api/v2/article/，参数以网页实际使用的language/path/type为准；服务器下游正文生成复用hash匹配的原文缓存。聚合摘要保留为discovery_excerpt。网络失败不再给模型内容额外打69分惩罚，仍不获得已核验一手证据的加分。

新增GitHub AI官方RSS，72小时限界补漏，按既有发布去重。综合早报不能作为独立事件凭关键词高分入选。

从2026-09-19起，compose/build生成high_value_coverage_review.json及.md；prod --send（包括--skip-build）在打包和SMTP前刷新并检查。重要模型、官方工程实践、AIHot前15名和BestBlogs 90分以上推荐（含匹配或原文解析失败的线索）：未在最终正文出现或官方日期矛盾时需编辑明确说明。核查完整all_candidates_scored及排重记录，不再依赖截断为18项的rejected_items。每条决定绑定候选指纹；重新评分/来源更新会让旧决定失效。hash只绑定输入，不证明独立审核身份。

处理方法：运行scripts/coverage_review.py --selection <selection.json> --content <content.json> --review <high_value_coverage_review.json>；按报告补选正文，或加--item-id <id> --disposition exclude|defer|retain_with_timing_note --reason <具体编辑理由> --editor <审核人>记录决定。发送前--check复核。仅有顶层approved或空理由无效，生成阶段掉稿会重新出现。判定延期时保留候选记录供后续补漏。

GitHub正文定位优先真实文章容器，避免将作者简介当正文。旧研究补漏条目只进入研究位，不冒充当日新闻。拒绝仅填写“分数低”“篇幅限制”等占位理由；编辑仍须对内容判断负责。
