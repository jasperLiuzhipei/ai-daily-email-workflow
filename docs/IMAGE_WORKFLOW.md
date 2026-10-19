# AI 日报图解生产约定

目标：信息充实、可独立读懂的报纸墨线图解。用户已认可 2026-09-28 的 `image_policy_v14_preview/research-preview-v2.png` 密度与效果；它是风格参考，环节数和构图随新内容决定。

## 规则职责

- `prompts/api_research_system_prompt.md`：编辑原文，生成正文和六项 Visual Handoff。
- `config/research_image_policy.json`：唯一绘图政策；编译器与自动图审共同读取。
- `scripts/research_image_contract.py`：规范化交接、注入一次绘图政策，保存哈希记录。
- `skills/ai-daily-imagegen/SKILL.md`：操作入口。服务器定时任务直接读取上述文件，不加载 skill。

本项目约定替代通用 skills 中全部旧视觉模板，包括固定分层、标签预算、配色与路由启发式；保留源核验、正文编辑、工具使用和来源记录要求。

## 六项交接

| 字段 | 职责 |
| --- | --- |
| subject | 主题和短标题 |
| takeaway | 核心判断 |
| mechanism | 主体、动作、原因或条件、关系及来源特有细节 |
| evidence | 数量、口径、观察及证据性质 |
| scope | 影响结论的限定与例外 |
| labels | 必须准确呈现的名称、数值单位及短说明 |

字段为字符串或字符串数组。mechanism 中需要表达关键方向时，可另行写 `关系：起点｜动作或传递对象｜终点`；统一主体命名。编译器校验格式；图审只核对影响读者理解的主机制，不要求把每条关系画成箭头。只删重复措辞，不按固定字数或标签数裁剪。labels 不重复整份交接，也不收录绘制禁令。缺事实回到编辑步骤；旧交接只有场景描述而无机制事实时拒绝编译，防止旧布局回流。

## 生成、修正和使用

1. 核验来源，完成六项交接；风格和构图指令只放在绘图政策。
2. 编译提示词，保存交接、Prompt 和编译记录。保持事实完整，通过精简规则控制长度，不截断证据。
3. 服务器使用配置的 API 路由；Codex 交互任务默认内置 image_gen，明确要求服务器/API 测试时走服务器。原生模型直接生成图文；精确排版确有需要时才选混合路线。
4. 保存原图与真实调用记录。路由回退依配置记录，指定模型验收使用 `--forbid-fallback`（流程入口为 `--strict-image-route`）。不把编译记录当作调用成功证明。
5. 打开实际图片，在邮件宽度下检查三点：主结论与关键字能否读懂、主要机制或方向是否正确、数字名称及能力边界是否有依据。画风按用户认可的手绘报纸图作参考；局部笔触、装饰、非关键小字和未画出的辅助关系不构成退稿理由。不要为已认可的风格反复生图。
6. 默认只生图一次。只有会误导读者的事实或关键文字错误才定向修正一次；新提示词 = 原编译提示词 + 一段当前问题的修正，不累计历次修正。仍有实质错误则不批准候选，回到正文或交接编辑处理，不盲目继续扣费。
7. 正式使用前须有绑定该图片与交接哈希的三项图审记录。无人值守发布如需自动模型看图，只做一次简短的实质事实检查；不重复全套逐箭头审查。发送仍按任务授权和现有发布门禁。

## 工具入口

在工作区根目录运行，编译输出路径须尚不存在：

```bash
python3 ai_daily_workflow/scripts/research_image_contract.py \
  --brief-json path/to/brief.json \
  --prompt-out path/to/prompt.txt \
  --contract-out path/to/compilation.json
```

API 生图、图审、入库和来源验证分别使用 `generate_research_image.py`、`review_research_image_editorial.py`、`stage_research_image.py`、`verify_research_image_lineage.py`。参数以各脚本 `--help` 为准。

哈希绑定可检测材料变化，不能证明谁真正看过图。历史 v1 记录只允许 `--historical-read-only` 核查，不用于新稿批准；已发送产物与旧编译记录不追改。
