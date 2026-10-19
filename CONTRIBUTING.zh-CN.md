# 贡献指南

[English](CONTRIBUTING.md) | **简体中文**

欢迎提交问题复现、文档修正及范围明确的改进。可通过 GitHub Issue 讨论缺陷或影响编辑流程的方案；安全漏洞请使用[安全报告流程](SECURITY.md#简体中文)。

## 开发环境

按[快速配置](README.zh-CN.md#首次配置)准备环境，使用示例收件人和临时运行目录。提交或 Issue 附件不得包含凭据、真实邮箱、邮件包、数据库及生产 trace。

```bash
python3 -m unittest discover -s tests
cd feedback_web
npm ci
npm run typecheck
```

模拟模型和 SMTP 调用。传输测试可以使用本机 HTTP 服务；回归测试不得调用真实模型或发送邮件。

## Pull Request

- 说明具体问题和修改后的行为。
- 控制修改范围，并说明验证结果。
- 保留证据归属、不可变审核历史、收件人快照及防重发约束。
- 抓取内容一律作为不可信数据，不作为指令执行。
- 修改公开配置或使用方式时，同步更新中英文入口文档；详细技术资料可在文档索引中注明语言。

贡献遵循项目的 [MIT 许可证](LICENSE)。引用第三方内容时保留所需归属说明。
