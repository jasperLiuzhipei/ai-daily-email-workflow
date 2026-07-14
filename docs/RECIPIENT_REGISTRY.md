# 收件人名单注册表

## 目标

将日报收件人从临时邮箱字符串升级为可审计的名单组。当前用本地 JSON 实现同一份数据契约；中台后由数据库或组织架构服务实现，不改变邮件打包和发送调用方式。

## 本地格式

```json
{
  "schema_version": 1,
  "groups": {
    "prod": {
      "id": "ai-daily-prod",
      "name": "AI Daily Production",
      "version": 3,
      "enabled": true,
      "members": [
        {"employee_id": "u123", "name": "Example", "email": "example@company.com"}
      ]
    }
  }
}
```

- `id`：稳定的名单组标识；中台数据库中的 `recipient_group.id` 对应此字段。
- `version`：每次成员增删、禁用或邮件地址变更时递增。
- `enabled`：关闭后不允许打包或发送该名单组。
- `employee_id`：建议使用公司员工 ID，而不是仅以邮箱作为身份主键。

旧的 `{"test": [...], "prod": [...]}` 仍兼容，但会被标识为 `legacy v0`，不应作为中台正式格式。

本地迁移命令如下；它会在同目录保留一个被 Git 忽略的备份：

```bash
python3 scripts/migrate_recipient_registry.py --write
```

## 发送快照

`create_email_send_package.py` 会在每个邮件包写入 `recipient_snapshot.json`，包含名单组元数据、实际成员、人数和指纹。

后续发送优先读取快照，不会重新查询实时名单；这保证了“预览、审批、打包、发送”使用同一批收件人。发送 ledger 仅保存组 ID、版本、人数、指纹和快照路径，避免在运行记录中重复展开邮箱。

## 中台映射

建议最小表结构：

- `recipient_group(id, name, version, enabled, updated_at)`
- `recipient(id, employee_id, name, email, active)`
- `recipient_group_member(group_id, recipient_id)`
- `delivery_recipient_snapshot(delivery_id, group_id, group_version, fingerprint, payload_uri, recipient_count)`

编辑者只能选择允许的名单组；正式发送时服务端创建并冻结快照，不能由浏览器提交一串邮箱替代。
