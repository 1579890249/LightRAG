# KG Mappings 自动生成接口文档

## 基础信息

- 服务地址：`http://172.16.1.203:9621`
- Swagger：`http://172.16.1.203:9621/docs`
- OpenAPI 分组：`kg-mapping-generation`

当前自动生成流程用于根据 PostgreSQL 表结构和可选的 ER/DDL/Excel/CSV 关系文件生成 `kg_mappings` YAML，并支持预览、发布和回滚。

## 整体流程

1. 调用 `POST /audit/kg-mapping/generate` 生成 mapping 草稿和生成记录。
2. 调用 `GET /audit/kg-mapping/generation/{generation_id}` 查看完整生成记录。
3. 调用 `POST /audit/kg-mapping/generation/{generation_id}/preview` 预览将要生成的 `custom_kg` 数据。
4. 调用 `POST /audit/kg-mapping/generation/{generation_id}/publish` 发布 mapping，并可选择是否立即同步生成图谱。
5. 调用 `POST /audit/kg-mapping/generation/{generation_id}/rollback` 回滚到上一版已发布 mapping。

`generate` 只生成 mapping 文件和生成记录，不会直接写入 LightRAG 图谱。真正写入图谱发生在 `publish` 且 `apply=true` 时。

## 默认排除表

audit 路由默认排除以下表，不会进入生成的图谱 mapping：

- `audit_rule`
- `audit_result`
- `project_alias`
- `audit_rule*_backup_*`

其中 `project_alias` 可用于项目匹配，但默认不进入知识图谱。

## 1. 生成 Mapping

```http
POST /audit/kg-mapping/generate
```

### JSON 请求(不推荐)

适合不上传 ER/DDL/Excel/CSV 文件，仅通过数据库结构和数据覆盖率推断关系。

```json
{
  "connection_url": "postgresql://rag:rag@postgres:5432/audit",
  "schema": "public",
  "database_name": "audit_auto_test",
  "workspace": "audit_customer_ys",
  "mode": "review_only",
  "sample_limit": 100,
  "enable_llm_enhancement": false
}
```

### Multipart 请求

适合上传客户提供的 ER/DDL/Excel/CSV 关系文件。

```bash
curl -X POST http://172.16.1.203:9621/audit/kg-mapping/generate \
  -F connection_url=postgresql://rag:rag@postgres:5432/audit \
  -F schema=public \
  -F database_name=audit_auto_with_ddl \
  -F workspace=audit_customer_ys \
  -F mode=merge \
  -F sample_limit=100 \
  -F metadata_files=@audit-kg-relationship-metadata.sql
```

支持多文件上传，重复传 `metadata_files` 即可。

### 支持的元数据文件类型

- `.json`
- `.yaml`
- `.yml`
- `.csv`
- `.sql`
- `.ddl`
- `.xlsx`

关系文件用于提供表之间的明确关系。系统会把这些关系作为 `er_declared`，默认按高置信关系处理。

### 请求参数

| 参数 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `connection_url` | 是 | 无 | 业务 PostgreSQL 连接串。容器内访问 audit 库一般使用 `postgresql://rag:rag@postgres:5432/audit`。 |
| `schema` | 否 | `public` | PostgreSQL schema。接口也兼容字段名 `db_schema`。 |
| `database_name` | 是 | 无 | 生成 mapping 文件名前缀，例如会生成 `{database_name}.{generation_id}.yaml`。 |
| `workspace` | 否 | `null` | 发布同步图谱时使用的 LightRAG workspace。 |
| `mode` | 否 | `merge` | 生成模式：`merge`、`full_replace`、`review_only`。 |
| `business_domain` | 否 | `generic` | 业务域标识，主要传给 LLM 增强上下文。 |
| `mapping_dir` | 否 | `/app/data/kg_mappings` | mapping 文件输出目录。 |
| `record_dir` | 否 | `/app/data/kg_mapping_generations` | 生成记录输出目录。 |
| `sample_limit` | 否 | `500` | 数据覆盖率抽样数量，范围 `1-10000`。 |
| `auto_approve_threshold` | 否 | `0.85` | 自动通过阈值。 |
| `review_threshold` | 否 | `0.65` | 进入待审核候选关系的阈值。 |
| `excluded_tables` | 否 | `[]` | 本次额外排除的表名列表。multipart 可重复传，也可逗号分隔。 |
| `excluded_table_patterns` | 否 | `[]` | 本次额外排除的表名通配符列表。multipart 可重复传，也可逗号分隔。 |
| `enable_llm_enhancement` | 否 | `false` | 是否启用 LLM 增强。 |
| `prebuilt_mapping` | 否 | `null` | 直接传入已构造 mapping，用于特殊场景。 |
| `metadata_files` | 否 | 无 | multipart 文件字段，可上传 ER/DDL/Excel/CSV 等关系元数据。 |

### mode 说明

- `review_only`：生成草稿和记录，不合并已有 current mapping，适合验证。
- `merge`：如果存在 `{database_name}.current.yaml`，会把本次生成内容合并到已有 mapping 中。
- `full_replace`：生成新 mapping，不读取旧 current mapping。

当前接口不会因为 `mode=review_only` 禁止后续发布；是否发布由调用方决定。

### LLM 增强说明

当 `enable_llm_enhancement=true` 时，系统会在确定性生成结果基础上调用 LLM 增强可读性字段。

LLM 只允许修改：

- entity type label
- `entity_name_template`
- `description_template`
- `relation_type`

LLM 不允许修改：

- SQL 查询
- source 名称
- 主键字段
- ID 字段
- 关系端点
- 数据库连接信息

完整 LLM 输入和输出 trace 会写入生成记录，接口响应只返回摘要。

### 返回示例

```json
{
  "generation_id": "gen_20260625094648101617",
  "mapping_path": "/app/data/kg_mappings/audit_rebuild_smoke.gen_20260625094648101617.yaml",
  "record_path": "/app/data/kg_mapping_generations/gen_20260625094648101617.json",
  "summary": {
    "tables": 8,
    "entities": 8,
    "relationships": 8,
    "auto_approved": 16,
    "need_review": 0,
    "blocked": 0
  },
  "excluded_tables": [
    "audit_result",
    "audit_rule",
    "audit_rule_text_id_backup_20260625",
    "project_alias"
  ],
  "can_publish": true
}
```

如果启用了 LLM 增强，响应会额外包含：

```json
{
  "llm_enhancement": {
    "enabled": true,
    "status": "applied",
    "applied": {
      "entity_labels": 8,
      "entities": 8,
      "relationships": 8
    },
    "ignored_count": 0
  }
}
```

### summary 字段说明

| 字段 | 说明 |
| --- | --- |
| `tables` | 参与生成的表数量。 |
| `entities` | 生成的实体配置数量。 |
| `relationships` | 生成的关系配置数量，只统计自动通过并进入 mapping 的关系。 |
| `auto_approved` | 自动通过项数量，包含实体配置和自动通过关系。 |
| `need_review` | 低于自动通过阈值但高于审核阈值的候选关系数量。 |
| `blocked` | 阻断项数量。当前生成逻辑主要保留该字段用于发布前检查。 |

## 2. 查看生成记录

```http
GET /audit/kg-mapping/generation/{generation_id}
```

### 请求示例

```bash
curl http://172.16.1.203:9621/audit/kg-mapping/generation/gen_20260625094648101617
```

可选 query 参数：

| 参数 | 说明 |
| --- | --- |
| `record_dir` | 指定生成记录目录。一般不需要传，默认 `/app/data/kg_mapping_generations`。 |

### 返回内容

返回完整 generation record，主要字段如下：

| 字段 | 说明 |
| --- | --- |
| `generation_id` | 生成记录 ID。 |
| `database_name` | 生成时传入的数据库逻辑名。 |
| `schema` | PostgreSQL schema。 |
| `workspace` | 目标 LightRAG workspace。 |
| `mode` | 生成模式。 |
| `business_domain` | 业务域标识。 |
| `connection_url` | 业务库连接串。 |
| `mapping_path` | 生成的 mapping YAML 路径。 |
| `summary` | 生成摘要。 |
| `mapping` | 完整 mapping 内容。 |
| `relationships` | 所有关系候选及其来源、分数、证据、决策。 |
| `excluded_tables` | 本次排除的表。 |
| `input_sources` | 上传的元数据文件摘要。 |
| `publish_status` | 发布状态，初始为 `draft`。 |
| `created_at` | 生成时间。 |
| `llm_enhancement` | LLM 增强记录，只有启用后才会出现。 |

上传了 ER/DDL/Excel/CSV 文件时，`input_sources` 示例：

```json
[
  {
    "filename": "audit-kg-relationship-metadata.sql",
    "sha256": "60a09c32ac499936c466c497f384482ca86289f4f71de3bff886b9aea51e8de9",
    "relationships": 9
  }
]
```

## 3. 预览生成图谱

```http
POST /audit/kg-mapping/generation/{generation_id}/preview
```

### 请求示例

```bash
curl -X POST http://172.16.1.203:9621/audit/kg-mapping/generation/gen_20260625094648101617/preview
```

可选 query 参数：

| 参数 | 说明 |
| --- | --- |
| `record_dir` | 指定生成记录目录。一般不需要传。 |

### 处理逻辑

1. 读取 generation record。
2. 根据 `mapping_path` 加载 mapping YAML。
3. 使用 `connection_url` 连接业务库读取 source 数据。
4. 使用 mapping 构建 `custom_kg`。
5. 返回统计和样例数据，不写入 LightRAG 图谱。

### 返回示例

```json
{
  "sources": {
    "enterprise": 5,
    "person": 10,
    "project": 3,
    "bid_record": 9
  },
  "custom_kg": {
    "chunks": 72,
    "entities": 72,
    "relationships": 76
  },
  "sample_entities": [],
  "sample_relationships": [],
  "sample_chunks": []
}
```

## 4. 发布 Mapping

```http
POST /audit/kg-mapping/generation/{generation_id}/publish
```

### 请求体

```json
{
  "apply": true,
  "write_state": true,
  "state": null
}
```

### 参数说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `apply` | `true` | 是否真正写入 LightRAG 图谱。传 `false` 时可用于 dry-run。 |
| `write_state` | `true` | 是否写入同步状态文件。 |
| `state` | `null` | 自定义同步状态文件路径。一般不需要传。 |

可选 query 参数：

| 参数 | 说明 |
| --- | --- |
| `record_dir` | 指定生成记录目录。一般不需要传。 |

### 请求示例

```bash
curl -X POST http://172.16.1.203:9621/audit/kg-mapping/generation/gen_20260625094648101617/publish \
  -H "Content-Type: application/json" \
  -d '{"apply":true,"write_state":true}'
```

### 处理逻辑

1. 读取 generation record。
2. 如果 `summary.blocked > 0`，拒绝发布。
3. 调用 audit KG sync，把 mapping 对应的数据同步到 LightRAG。
4. 将本次 mapping 复制为 `{database_name}.current.yaml`。
5. 查找同一 `database_name` 的上一版已发布记录，写入 `previous_published_generation_id`。
6. 更新当前记录：
   - `publish_status=published`
   - `published_at`
   - `published_mapping_path`
   - `sync_result`

### 返回示例

```json
{
  "code": 200,
  "msg": "Publish succeeded",
  "generation_id": "gen_20260625094648101617",
  "current_mapping_path": "/app/data/kg_mappings/audit_rebuild_smoke.current.yaml",
  "previous_published_generation_id": "gen_20260625093231025834",
  "sync_result": {}
}
```

实际返回中还会展开 audit KG sync 的返回字段，因此字段可能比上例更多。

## 5. 回滚发布

```http
POST /audit/kg-mapping/generation/{generation_id}/rollback
```

### 请求体

请求体可以为空。为空时使用默认值：

```json
{
  "apply": true,
  "write_state": true,
  "state": null
}
```

### 参数说明

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `apply` | `true` | 是否真正写入 LightRAG 图谱。传 `false` 时可用于 dry-run。 |
| `write_state` | `true` | 是否写入同步状态文件。 |
| `state` | `null` | 自定义同步状态文件路径。一般不需要传。 |

可选 query 参数：

| 参数 | 说明 |
| --- | --- |
| `record_dir` | 指定生成记录目录。一般不需要传。 |

### 请求示例

```bash
curl -X POST http://172.16.1.203:9621/audit/kg-mapping/generation/gen_20260625094648101617/rollback \
  -H "Content-Type: application/json" \
  -d '{"apply":true,"write_state":true}'
```

### 处理逻辑

1. 读取当前 generation record。
2. 从当前记录读取 `previous_published_generation_id`。
3. 读取上一版 generation record。
4. 调用 audit KG sync，将上一版 mapping 同步到 LightRAG。
5. 将上一版 mapping 复制为 `{database_name}.current.yaml`。
6. 更新当前记录：
   - `publish_status=rolled_back`
   - `rolled_back_at`
   - `rolled_back_to_generation_id`
   - `rollback_sync_result`
7. 更新上一版记录：
   - `publish_status=published_after_rollback`
   - `published_mapping_path`
   - `republished_at`
   - `rollback_from_generation_id`
   - `rollback_sync_result`

### 返回示例

```json
{
  "code": 200,
  "msg": "Rollback succeeded",
  "generation_id": "gen_20260625094648101617",
  "rolled_back_to_generation_id": "gen_20260625093231025834",
  "current_mapping_path": "/app/data/kg_mappings/audit_rebuild_smoke.current.yaml",
  "sync_result": {}
}
```

## 错误码和常见错误

| 状态码 | 场景 |
| --- | --- |
| `400` | 发布时存在 blocked 项；回滚时没有上一版已发布记录。 |
| `404` | generation record 不存在；回滚目标记录不存在。 |
| `422` | 请求参数校验失败，例如缺少必填字段或字段类型错误。 |
| `501` | publish/rollback 的 sync callback 未配置。当前 audit 路由已配置。 |
| `500` | 数据库连接失败、关系文件解析失败、LLM 增强异常等未处理异常。 |

## 当前大文件处理限制

当前上传文件处理方式是一次性读取到内存：

```text
await value.read()
```

随后按格式整体解析：

- DDL/SQL：整体字符串正则解析。
- CSV：整体读取为 `csv.DictReader` 记录列表。
- JSON/YAML：整体反序列化。
- XLSX：使用 `openpyxl` 读取后遍历行。

因此，小到中等规模文件可直接处理；特别大的 ER/DDL/Excel 文档存在内存占用、请求超时和请求体大小限制风险。

生产建议：

- 优先让客户上传关系表 CSV/Excel，而不是超大的完整 DDL。
- 增加上传大小限制。
- 将上传文件落临时文件。
- 对 CSV/DDL 做流式解析。
- 将生成任务改为后台任务并提供进度查询。

## 验证样例文件

当前仓库提供了一个可用于验证自动生成流程的 DDL 关系元数据样例：

```text
docs/examples/audit-kg-relationship-metadata.sql
```

远程服务器路径：

```text
/mnt/github/LightRAG/docs/examples/audit-kg-relationship-metadata.sql
```

该文件只用于作为 `metadata_files` 上传验证，不建议直接在数据库执行。
