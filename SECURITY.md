# Security

English · [简体中文](#简体中文)

Use the repository's **Security → Report a vulnerability** form to report a suspected vulnerability privately. Include affected components, reproduction steps using synthetic data, the impact, and a proposed fix if available. Do not publish credentials, signed production links, recipient data, or working exploit details in a public issue.

Security fixes target the current `main` branch; there is no separate long-term support branch or guaranteed response time. Revoke exposed credentials and invalidate affected signing secrets in your deployment before sharing diagnostic material.

## Deployment data

Keep `.env` files, SMTP settings, recipient registries and backups, databases, generated mail, and delivery traces outside Git. Use separate test and production recipients, distinct administrator and signing secrets, and HTTPS for externally reachable feedback services.

The public configuration disables automatic production delivery. Enable it only for your own deployment after validating recipients, review gates, and an explicit authorization record. Review fetched source content as untrusted input.

## 简体中文

请通过仓库 **Security → Report a vulnerability** 表单私下报告疑似漏洞。提供受影响模块、使用合成数据的复现步骤、影响及可选修复建议；不要在公开 Issue 中提交凭据、生产签名链接、收件人数据或可直接利用的漏洞细节。

安全修复以当前 `main` 分支为目标，暂未设独立长期支持分支或响应时间承诺。凭据泄漏时，应先在部署环境撤销凭据或更新受影响的签名密钥，再分享诊断材料。

环境文件、SMTP 设置、收件名单及备份、数据库、邮件和投递 trace 应保留在 Git 之外。测试与正式名单应分开，管理和签名密钥应独立，外部反馈服务使用 HTTPS。开源配置默认关闭自动正式发送；部署方验证收件人、审核门禁并明确记录授权后，才自行启用。抓取来源始终按不可信输入处理。
