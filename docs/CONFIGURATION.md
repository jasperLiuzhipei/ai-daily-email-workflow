# AI 日报配置说明

配置是中台化前的稳定接口：日常调整来源、名单、评分或栏目时，优先改 `config/`，而不是直接改脚本或 Prompt。

## 当前配置清单

| 文件 | 作用 | 何时调整 |
| --- | --- | --- |
| `source_registry.json` | 信息源、抓取方式、权威等级与候选池角色 | 新增/停用信息源，或调整官方直连源 |
| `selection_policy.json` | 评分权重、加减分、时效窗口、最低新闻数、阅读预算、主题数量限制 | 漏选、重复、阅读成本或排序异常复盘后 |
| `audience_profiles.json` | 产品、用户运营、运营中台等画像的偏好权重 | 更换读者群或需要对比不同职能视角时 |
| `content_sections.json` | 研究前沿与新闻子栏目的名称、顺序、分类词和锚点 | 调整日报信息架构或栏目命名时 |
| `editorial_policy.json` | 版头默认值、观察标签、研究解读标签、边界提示展示条件 | 调整编辑语气、栏目字段或边界提示密度时 |
| `recipients.json` | 测试/正式名单的版本化分组 | 增删名单；修改时递增组版本 |
| `email_settings.json` | SMTP 发送连接及反馈公开地址对应的环境变量名 | 仅本地或中台配置中心维护，不应提交真实密码或域名密钥 |
| `ai_runtime.json` | 文本/图片 API 模型、端点路径、超时和环境变量名 | 切换获批网关或模型版本时；本地文件不提交 |
| `image_providers.json` | 研究图 Provider 的质量基线、候选和发布门禁，不保存 API 地址 | Provider 通过稳定性与 A/B 审核时 |
| `editor_settings.json` | 日报署名和试运行说明 | 调整编辑身份或邮件尾注时 |

模型实际使用的系统 Prompt 位于 `prompts/api_research_system_prompt.md` 和 `prompts/api_daily_content_system_prompt.md`。调整编辑结构时修改 Prompt 文件并运行测试，不在 Python 中复制另一份 Prompt。

## 配置改动原则

1. 每次只改一个明确问题：例如“技巧类总是漏选”，先改 `selection_policy.json` 的补偿/阈值，不顺手调整来源和视觉。
2. 调整前后都保留一期 `selection_review.json` 作为依据；不要凭单条新闻追着改权重。
3. `balanced_product_ops` 是默认画像。切换到 `product`、`user_operations` 或 `operations_platform` 只改变排序偏好，不应让日报失去行业新闻性。
4. 新闻与论文使用不同窗口：普通新闻默认最多回看 48 小时；研究前沿默认可回看 72 小时。
5. 正式名单、SMTP 和 API Key 属于运行配置；策略、栏目和来源配置可以随代码一起版本管理。
6. 反馈公开域名、签名密钥和后台密钥分别通过 `AI_DAILY_FEEDBACK_BASE_URL`、`AI_DAILY_FEEDBACK_SECRET`、`AI_DAILY_FEEDBACK_ADMIN_KEY` 注入；`email_settings.json` 只保存环境变量名。

## 调试与回放

选源输出会记录：

- 配置文件路径和使用的读者画像。
- `config_snapshot.selection_policy`：本期真实生效的评分/窗口/阅读预算。
- `config_snapshot.audience_profile`：本期真实生效的画像。

因此即使之后修改了 JSON，也可以复盘某一期当时为什么被选中或拒选。

```bash
# 默认平衡画像
python3 scripts/run_ai_daily.py select --date YYYY-MM-DD

# 用产品画像做一次不影响正式发送的对比选源
python3 scripts/select_aihot_sources.py \
  --date YYYY-MM-DD \
  --audience-profile product \
  --output daily_runs/YYYY-MM-DD/source_selection_product_compare.json
```

`source_selection_product_compare.json` 只用于对比，不应替代正式选择结果，除非编辑确认采用。
