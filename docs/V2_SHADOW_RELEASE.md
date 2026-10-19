# AI 日报 V2 开发版 · 2.0.0-shadow.1

2026-09-14。此版把已确认的日报能力接到独立的版本与发布工作区，可生成预览、结构化改稿、绑定审核、冻结影子发布包和演练投递恢复。现有正式邮件系统未切换；本版没有真实 SMTP 发送命令，也不加载 `.env`。生产新模板与 V2 开发版是两项不同交付。

## 已实现

- 复用已定稿的纯文字报纸版，七色按刊期固定，原宋体、AI 1.22em／下移 3px、ISO 年周主题。编辑列表第一条成为重点稿；改稿重排可见，不会再由隐藏的类别偏好重选头条。
- 旧 `paper/research_items` 单向映射成 `deep_features`，旧刊号可从明确的“第 N 期”副标题恢复并记录来源。不靠日期重新编号，不补写缺失事实或证据。
- 预览冻结 revision：内容、证据、HTML、名单、模板、代码和运行依赖均有指纹。相同输入不重复建版；新版本使旧的未发送批准失效。SQLite 使用 WAL、FULL 同步和事务约束。
- 编辑操作覆盖新闻/深度字段、来源、撤稿、重排、简评；操作文件记录作者、原因及前后 hash。只改工作稿，重新预览与审核后才产生新 release。
- 审稿支持绑定 revision hash 的人工记录与独立上下文 AI 调用接口。真实调用须由已授权 text provider 显式注入，不读 SMTP 配置；requested/returned 身份分别记录。导入历史内容不伪报新 AI 审核已通过。
- 单执行单元 checkpoint 绑定证据、稿件、规则、模型及 provider，格式只修一次，超时/429/5xx 受总次数与时间预算约束；已成功稿件不重生成。中断调用保留 RUNNING，等待核对而非自动重复。现有网关未提供可可靠解析的类型化错误时标为不可用，不从错误文案猜测重试类别。
- 审核、批准、release manifest 和逐人 MIME 在事务中处理。发布端演练只接收 release ID；拒绝任意 HTML/名单覆盖。并发仅一个发送 claim，ACCEPTED 不重投，UNKNOWN 不自动重试，人工核对记录作者与证据。纠错重发必须创建新 revision、引用原 release 并说明原因，旧的未完成 release 退出发送。
- 每收件人第一封被接受的邮件成为周根；已发送身份只从 ACCEPTED 导出，草稿和失败项不进入去重。影子身份只在影子库有效，不可导入正式已发送事实。
- 反馈 outbox 与投递结果分开，持久重试要求接收端按 release/recipient upsert；备份恢复保留 ACCEPTED/UNKNOWN。所有演练状态有 `mode=shadow`，真实发送数为 0。

## 使用

从项目工作区根目录（含 `daily_runs` 与 `ai_daily_workflow`）执行。Python 需安装 `ai_daily_workflow/requirements.txt`；建议独立虚拟环境。

```sh
python ai_daily_workflow/scripts/run_ai_daily_v2.py preview \
  --content daily_runs/2026-09-14/ai_daily_email_content.marshall.json \
  --date 2026-09-14 --issue 44 \
  --materials daily_runs/2026-09-14/source_material.json
```

默认生成 `output/v2-shadow/ledger.sqlite3` 与 `previews/<revision_id>/index.html`。默认名单只有 `preview@example.invalid`；提供 `--recipients` 时读取显式 JSON 邮箱数组并冻结。编辑页显示人数和指纹，不公开邮箱。影子读者版移除评分占位链接，来源保持直达；接入正式反馈时应另冻结真实个性化 MIME 并验收。

`--selection` 接收选源快照，其中 `coverage_cutoff` 必须是实际采集覆盖截止，不能把抓取材料文件的生成时间或发送时间当作它。缺失时保留 `selection_coverage_cutoff_unknown`。`--editorial` 仅导入与输入正文签名匹配的已审核区块；默认没有隐式的日期目录覆盖。

```sh
python ai_daily_workflow/scripts/run_ai_daily_v2.py inspect REVISION_ID
python ai_daily_workflow/scripts/run_ai_daily_v2.py edit \
  --content draft.json --operations changes.json \
  --actor editor --reason '调整重点稿' --out draft-next.json
python ai_daily_workflow/scripts/run_ai_daily_v2.py review REVISION_ID --record review.json
python ai_daily_workflow/scripts/run_ai_daily_v2.py approve-shadow REVISION_ID --actor editor
python ai_daily_workflow/scripts/run_ai_daily_v2.py simulate-delivery RELEASE_ID --outcome UNKNOWN
python ai_daily_workflow/scripts/run_ai_daily_v2.py status RELEASE_ID
python ai_daily_workflow/scripts/run_ai_daily_v2.py reconcile RELEASE_ID RECIPIENT_ID \
  --outcome ACCEPTED --actor editor --evidence '模拟投递服务查询确认'
python ai_daily_workflow/scripts/run_ai_daily_v2.py backup output/v2-shadow/backup.sqlite3
```

`approve-shadow` 必须有真实填写的审核记录；软警告逐项通过 `--acknowledge WARNING` 记录确认，不能豁免缺失证据等硬问题。`simulate-delivery` 每次演练一个收件人，支持 ACCEPTED、REFUSED、UNKNOWN、CRASH；不会连接网络。影子批准仅用于测试，不构成正式群发授权。

人工审核文件示意（target_hash 从 inspect 读取，结论由实际审核者填写）：

```json
{
  "kind": "human",
  "actor": "实际审核者",
  "target_hash": "实际 revision document_hash",
  "result": "needs_human",
  "notes": "记录具体核查范围和仍未解决的问题",
  "issues": []
}
```

编辑操作数组示例：`[{"op":"update","card_id":"实际ID","fields":{"title":"修订后的标题"}}]`。还支持 remove、reorder（必须包含全部新闻 ID）、set_summary。AI 调用接口为 `daily_v2.execution.review_with_provider`，本轮用隔离的模拟响应验收，未额外消耗真实模型额度。自动定向改稿最多两轮的期级编排尚待接入；当前由编辑事务进行修改并使审核失效。

固定预览入口为 `output/v2-shadow/previews/index.html`，指向最近导出的不可变快照。`inspect` 不传 ID 时读取上海时区今日的最新版本，也可传 `--date YYYY-MM-DD`，不会因查看历史日期而重建或重新计数。

## 测试与边界

```sh
python -m unittest discover -s ai_daily_workflow/tests
python ai_daily_workflow/scripts/run_ai_daily_v2.py \
  --db output/v2-shadow/replay.sqlite3 replay --limit 20
python ai_daily_workflow/scripts/package_ai_daily_v2.py
```

本轮测试、回放和截图结果见工作区 `output/v2-shadow/validation-report.md`。故障测试使用模拟 HTTP/模型/投递，不证明真实 SMTP 已验收。回放是历史成稿适配与渲染验收，不重新抓取来源、不重跑选源和 AI 审稿，不能宣称 PRD 的全部黄金质量样本已通过。缺失证据或超过新版数量上限的旧稿留在阻断预览中，不为让指标好看自动删稿。

包通过显式白名单构建，包括 V2、所需共享模块、模板配置、说明和测试；不包含 `.env`、运行时私有配置、正式名单、生产账本、历史正文或 SMTP 发送器。包可独立解压，在包根执行 `python scripts/run_ai_daily_v2.py --help` 与 `python -m unittest discover -s tests`。包 SHA-256 和逐文件 manifest 可校验重建一致性。

## 逐步应用到邮件系统

1. **当前开发包**：离线导入今天与历史成稿；比对完整正文、版式、来源覆盖及版本变化。正式发送仍由原链完成。
2. **生产侧影子制作**：为 V2 建独立无 SMTP 凭据身份，只读取制作产物。先用 5–10 个工作日记录产出率、缺证率、人工修改和阶段耗时。统一候选池、三轨资格、语义归并和期级两轮修订编排在此阶段接入，重跑 PRD 指定漏报黄金样本。
3. **受控测试投递**：实现独立 publisher 身份和真实 SMTP 适配器、冻结实际发件人及每人反馈 token/MIME，完成 Foxmail 与一种移动客户端验收。具体测试名单沿用用户授权，增加名单需另授权；部署 timer 与 systemd 实际生效内容单独留存。
4. **正式切换**：暂停发送并确认无存活发布进程，备份并导入 V1 的逐人 ACCEPTED/UNKNOWN、周根和发布身份。未知历史状态不能当作 READY。所有入口共用同一发送账本后再切入 V2。
5. **回退**：先停止 V2，再恢复旧代码；继续使用迁移后的发送事实。不得恢复一个遗漏 V2 已投递记录的旧账本。本轮的 SQLite 备份测试不能代替这项 V1/V2 实际迁移演练。

本版保留原系统图解能力，但新 V2 包只支持默认纯文字。原生图片导入、图解血缘、CID 与实图审核仍需独立接入和验收。完整需求状态以 PRD 1.4 的 19.2 范围表为准。
