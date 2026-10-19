# 日报实际工作日规则

2026-09-20：按公司实际工作日制作，默认采用国务院年度放假调休安排。优先级：`config/work_calendar.json` 日期覆盖 > 官方补班 > 官方休假 > 普通周一至周五。缺少当年日历或格式错误时退出报错；systemd 记录失败状态与日志。每年官方安排公布后更新 `years`，附原始公告来源，并跑日历测试。

公司覆盖示例（写入 `overrides`，布尔值和原因必填）：

```json
{"2026-09-20": {"workday": false, "reason": "公司安排休息"}, "2026-09-25": {"workday": true, "reason": "临时补刊"}}
```

每天北京时间 08:30 触发，脚本先查日历。定时服务 `--scheduled` 限定当日中午 12:00 前补启动，防止宕机后深夜补制作。手动制作不受此时间限制，但受同一日历约束。只检查日期、不触发制作：

```sh
python ai_daily_workflow/scripts/work_calendar.py --date 2026-09-20
```

`prepare`、`preview` 和实际 `prod --send` 共用日历与每期 OS 文件锁。制作成功写入 `preparation_completed.json`，失败可以重试；成功后默认跳过，明确修订可加 `--rebuild`。已发布不重新制作。发送继续保留原有防重和质量门。低层构建、选源与历史回放命令不受工作日日历限制。

常规 prepare 未指定 `--start` 时，从最近已发布期刊选源文件的 `window.end_at` 接续，沿用已发布内容去重。2026-10-10 起，选源入口以本次 `end_at` 为基准，将实际起点限制为 `max(上次发布截止, end_at - 96小时)`；即使长假或连续未发送，也最多回看四天。研究回看、官方补漏和未刊候选回流共用此上限，按原始发布时间过滤；手动 `--start`、`--allow-wide-news-window` 和较大的论文回看参数不能扩大此上限。未发布预览不推进发布边界。没有历史发布状态时沿用现有首次选源规则并应用同一上限；存在已发布状态但缺少选源截止信息时明确报错。

自动发送入口也查日历。安装不得改变其原有启用状态；2026-09-20 服务器自动发送 timer 为 disabled。服务器制作 service 有 9 月 18 日留下的失败状态，部署后需要单独排查旧失败原因。

## 部署状态

用户明确授权后，运行脚本与日历已于 2026-09-20 部署到 ths@192.168.2.219，服务器 9 项日历测试和休假日 CLI 验证通过。备份：/home/ths/ai-daily/deploy_backups/work_calendar_20260920T054610Z。用户完成管理员安装后，已远程核验系统定时器正式生效：制作 timer 为每天 08:30 Asia/Shanghai，enabled 且 active；prepare/preview 均带 --scheduled。下次触发为 2026-09-21 08:30 CST。自动发送 timer 保持 disabled 且 inactive。日历实测 2026-09-20 为补班日、2026-09-25 为休假日。

部署需先备份服务端文件，并针对服务器现有 run_ai_daily.py / auto_publish.py 应用本次局部补丁，不覆盖本地其他未发布变更。更新 scripts/work_calendar.py 和 config/work_calendar.json。制作 service 保留现有参数，只为 prepare、preview 增加 --scheduled。两个 timer 使用本地模板中的每天触发与 Asia/Shanghai 显式时区。

管理员安装暂存后的 unit 文件，执行 systemctl daemon-reload，再重启原本启用的 ai-daily-prepare.timer。不要启用 disabled 的 ai-daily-autopublish.timer；不要启动制作或发送 service 来验证。重载后的 Persistent 补触发由 12:00 截止规则拦截。验证官方补班、中秋休息、下次触发时间及自动发送仍 disabled。

## 管理员安装命令（已执行并验证）

在本机终端执行以下命令，并在服务器的 sudo 提示中输入管理员密码（不要在聊天中发送密码）：

```sh
ssh -t ths@192.168.2.219 'sudo sh /home/ths/ai-daily/deploy_staging/work_calendar_20260920/install_units.sh'
```

脚本安装暂存的三个 unit，重载 systemd 并重启原已启用的制作 timer，输出下一次触发时间和两个 timer 的启用状态；自动发送仍保持 disabled。每天 08:30 Asia/Shanghai 的表达式已由服务器 systemd-analyze calendar 验证有效。
