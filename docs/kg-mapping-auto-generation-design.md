# KG Mapping 自动生成设计方案

## 1. 背景

客户会将已经治理过的业务表数据导入 PostgreSQL，并提供表结构关系信息，例如 ER 元数据、DDL 或 Excel 说明。目标是减少人工编写和审核 `kg_mappings` 的工作量，同时保证生成过程可解释、可追溯、可回滚。

客户侧期望流程应尽量简单：

1. 导入业务数据。
2. 上传 ER/DDL/Excel 元数据，或让系统直接读取数据库结构。
3. 点击“生成知识图谱配置”。
4. 只审核低置信度或阻断项。
5. 点击“发布并同步”。

系统侧负责自动完成结构解析、关系发现、置信度评分、配置生成、预览、发布和记录留痕。

## 2. 目标

- 根据客户数据库结构和关系元数据生成客户专属 `kg_mappings`。
- 对高置信度实体和关系自动通过，减少人工审核工作量。
- 支持数据库未设置物理外键时，通过字段名、注释和数据分布推断关系。
- 支持关系表/事件表只有 ID 字段时，自动生成带 `LEFT JOIN` 的 source query，补齐名称字段，生成可读的图谱实体和关系文本。
- 支持全量和增量元数据上传。
- 保存生成记录、置信度、证据、版本和发布历史，支持回溯。
- 客户侧流程保持简单，复杂判断由后台完成。

## 3. 非目标

- 不把 ER 图片作为唯一可信输入。
- 不在客户只上传部分 ER 时自动删除旧映射。
- 不要求客户直接编辑 YAML。
- 不完全替代业务人员对低置信度关系的确认。

## 4. 输入来源

生成器应支持多种输入来源。

### 4.1 数据库结构读取

直接从 PostgreSQL 读取：

- schema
- 表
- 字段
- 字段类型
- 主键
- 外键
- 索引
- 表注释
- 字段注释
- 行数
- 抽样数据

这是最稳定的基础输入，因为它反映客户已经导入的真实数据库结构。

### 4.2 ER/关系元数据

优先支持结构化格式：

- JSON
- YAML
- Excel
- CSV
- DDL SQL

ER 输入建议包含：

- 表名
- 字段名
- 字段中文名
- 主键
- 逻辑外键
- 关系名称
- 业务说明
- 本次上传是全量还是增量

ER 图片可以作为辅助材料，但不建议作为唯一可信输入。

## 5. 客户侧流程

### 5.1 首次生成

1. 客户导入业务表数据。
2. 客户选择数据库连接和 schema。
3. 客户上传 ER/DDL/Excel 元数据，若没有则直接使用数据库结构读取。
4. 客户点击“生成”。
5. 系统生成：
   - `kg_mappings` 草案
   - 审核报告
   - 置信度评分
   - dry-run 图谱预览
6. 客户只审核异常项和阻断项。
7. 客户点击发布。
8. 系统应用 mapping 并执行 KG 同步。

### 5.2 增量更新

默认模式应为 `merge`。

1. 客户导入新增或变更后的表。
2. 客户上传部分 ER 元数据，或选择数据库结构读取。
3. 系统与当前正式 mapping 做对比。
4. 系统只新增或更新本次涉及的 sources、entities、relationships。
5. 未出现在本次上传中的旧配置不自动删除。
6. dry-run 成功后，客户发布新版本。

### 5.3 全量替换

`full_replace` 应作为高级选项，由客户明确选择。

该模式下，本次上传未出现的表和关系可以被标记为待删除，但仍应进入审核报告，发布前需要确认。

### 5.4 仅预览

`review_only` 只生成报告和 mapping 草案，不写正式文件，也不同步 LightRAG。

## 6. 自动生成流程

```text
数据库结构读取 / ER 元数据 / DDL / Excel
  -> 元数据标准化
  -> 识别主键和候选键
  -> 识别显式关系和推断关系
  -> 使用抽样数据验证关系
  -> 将表分类为实体表、关系表、事件表、属性表或忽略表
  -> 识别展示名称字段
  -> 自动生成 join query
  -> 自动生成 entities 和 relationships
  -> 生成 kg_mappings YAML
  -> dry-run 构建 custom_kg
  -> 评分并生成审核报告
  -> 保存生成记录
  -> 审核通过后发布
```

## 7. 关系发现

关系应按可信度从高到低发现。

### 7.1 物理外键

数据库中真实存在的外键可信度最高。

示例：

```text
bid_record.project_id -> project.id
```

### 7.2 ER 声明关系

如果客户提供的 ER/Excel 元数据声明了逻辑关系，即使数据库中没有物理外键，也应作为高置信度关系。

### 7.3 字段名推断

没有外键时，根据字段命名推断关系：

```text
company_id -> company.id
project_id -> project.id
supplier_code -> supplier.supplier_code
person_no -> person.person_no
```

### 7.4 注释推断

根据表注释和字段注释辅助判断：

```text
所属公司ID
项目编号
供应商编码
人员ID
```

### 7.5 数据分布验证

用真实数据验证推断关系。

示例：

```text
child.company_id 非空值中，98% 能在 company.company_id 中找到。
```

覆盖率高则提高置信度。覆盖率低则进入人工审核或阻断自动生成。

## 8. 置信度评分

每个自动生成的实体和关系都应有置信度和证据。

示例评分因子：

```text
存在物理外键：+0.40
ER 声明关系：+0.35
字段名强匹配：+0.25
表名前缀匹配：+0.15
字段类型一致：+0.10
数据覆盖率 >= 95%：+0.25
字段或表注释匹配：+0.10
展示名称字段明确：+0.10
```

建议阈值：

```text
score >= 0.85：自动通过
0.65 <= score < 0.85：进入审核
score < 0.65：阻断或忽略
```

阈值应支持按客户或业务域配置。

## 9. 表分类

生成器需要判断每张表在图谱中的角色。

### 9.1 实体表

典型特征：

- 有主键。
- 有明确名称字段。
- 被其他表引用。
- 表示公司、项目、人员、合同等业务对象。

生成示例：

```yaml
entities:
  - source: company
    entity_type: Organization
    id_field: company_id
    name_field: company_name
```

### 9.2 关系表

典型特征：

- 包含两个类似外键的字段。
- 除关联字段外，业务属性较少。
- 表示两个实体之间的关联。

这种表通常可以直接生成 relationship。

当前自动生成策略先保持保守：如果一张表有主键且可被解释为独立业务记录，
生成器可以继续把它生成为中间实体节点，再连接相关实体。这样不会过早丢失
记录级别的可追溯性，也避免把多条业务记录折叠到同一条边上。

后续可扩展为更细的表角色判定：当关系表只是简单关联时，直接生成实体之间的
relationship；当关系表本身是可审计、可引用、带生命周期的业务记录时，保留
中间节点。

示例：

```text
简单任职关联：
Person --[HOLDS_POSITION]--> Organization

带审批、任期、附件或风险结论的任职记录：
Person <-[OF_PERSON]- PositionRecord -[AT_ORGANIZATION]-> Organization
```

判断原则：

- 如果表只有两个主要外键和少量描述字段，优先作为边。
- 如果表有独立主键、状态、开始/结束时间、审批号、附件、来源文件、风险结论、
  创建/更新时间等记录级字段，优先作为中间业务记录节点。
- 如果同一对实体之间可能有多条不同记录，优先保留中间节点，避免关系边被合并
  后丢失明细。
- 如果用户更关注图谱简洁和路径推理，可倾向直接边；如果用户更关注审计留痕和
  事实溯源，可倾向中间节点。

这部分判断可以由规则先给出候选角色，再由大模型基于表注释、字段中文名和样例
数据辅助解释灰区判断。大模型只应增强 `table_role`、`reason`、说明文本和模板，
不应直接绕过外键、覆盖率、字段类型等确定性证据。

### 9.3 事件表

典型特征：

- 有独立生命周期或业务含义。
- 包含金额、日期、状态、排名等字段。
- 表示投标、合同、付款、审批等业务事件。

这类表通常应生成事件实体，再连接相关实体。

示例：

```text
BidSubmission -> Project
BidSubmission -> Organization
```

### 9.4 属性表

典型特征：

- 从属于一个实体。
- 表示证书、营收、资质、年度指标等。

可根据检索需求选择生成属性实体加关系，或仅生成文本 chunk。

## 10. Join Query 自动生成

很多关系表或事件表只保存 ID。生成 mapping 时应自动 join 关联实体的展示字段，让图谱文本可读。

输入表：

```text
bid_record(id, project_id, company_id, bid_amount)
project(id, project_name)
company(id, company_name)
```

自动生成 source：

```yaml
sources:
  bid_record:
    primary_key: id
    query: >
      SELECT
        br.*,
        p.project_name AS project_name,
        c.company_name AS company_name
      FROM bid_record br
      LEFT JOIN project p ON p.id = br.project_id
      LEFT JOIN company c ON c.id = br.company_id
```

自动生成 entity：

```yaml
entities:
  - source: bid_record
    entity_type: BidSubmission
    id_field: id
    entity_name_template: "{company_name}投标{project_name}（{id}）"
    description_template: "{company_name}投标{project_name}; bid_amount={bid_amount}"
```

如果多个字段关联同一张表，alias 必须带角色前缀。

示例：

```sql
SELECT
  ct.*,
  signer.name AS signer_company_name,
  supplier.name AS supplier_company_name
FROM contract ct
LEFT JOIN company signer ON signer.id = ct.signer_company_id
LEFT JOIN company supplier ON supplier.id = ct.supplier_company_id
```

## 11. 审核报告

客户默认不需要看完整 YAML，只看摘要和异常项。

示例：

```json
{
  "summary": {
    "tables": 28,
    "entities": 12,
    "relationships": 34,
    "auto_approved": 41,
    "need_review": 3,
    "blocked": 1
  },
  "need_review": [
    {
      "type": "ambiguous_name_field",
      "table": "contract",
      "candidates": ["contract_name", "title", "subject"],
      "suggestion": "contract_name",
      "score": 0.74
    }
  ],
  "blocked": [
    {
      "type": "missing_primary_key",
      "table": "tmp_import_log"
    }
  ]
}
```

客户只需要处理 `need_review` 和 `blocked`。

## 12. 生成记录

每次生成都应保存可回溯记录。

字段建议：

```text
generation_id
customer_id
database_name
schema_name
workspace
mode: merge | full_replace | review_only
input_sources
input_file_hashes
schema_snapshot_hash
base_mapping_version
generated_mapping_version
entity_candidates
relationship_candidates
confidence_scores
evidence
warnings
blocked_items
dry_run_result
publish_status
published_at
published_by
rollback_from
created_at
```

关系证据示例：

```json
{
  "relation": "bid_record.company_id -> company.id",
  "source": "inferred_by_name_and_data",
  "score": 0.93,
  "evidence": {
    "name_match": true,
    "type_match": true,
    "data_coverage": 0.98,
    "matched_values": 1200,
    "unmatched_values": 18
  },
  "decision": "auto_approved"
}
```

## 13. Mapping 版本管理

每个客户应维护版本化 mapping 文件。

示例：

```text
configs/kg_mappings/customer_x.v1.yaml
configs/kg_mappings/customer_x.v2.yaml
configs/kg_mappings/customer_x.current.yaml
docs/kg_mappings/customer_x.v2.md
```

当前 MVP 的运行时默认落盘路径为：

```text
/app/data/kg_mappings
/app/data/kg_mapping_generations
```

部署时建议将这两个目录挂载到宿主机，避免容器重建后丢失生成记录：

```yaml
volumes:
  - ./data/kg_mappings:/app/data/kg_mappings
  - ./data/kg_mapping_generations:/app/data/kg_mapping_generations
```

发布一个版本时：

1. 保存生成的 YAML。
2. 保存生成的 Markdown 说明文档。
3. 更新 current 指针。
4. 执行 dry-run 校验。
5. 执行 `kg-sync apply`。
6. 保存同步结果。

回滚时，将 current 指回上一个已发布版本，并按需重新同步。

## 14. 建议接口

### 14.1 生成

```http
POST /kg-mapping/generate
```

请求：

```json
{
  "connection_url": "postgresql://...",
  "schema": "public",
  "database_name": "customer_audit",
  "workspace": "customer_audit",
  "mode": "merge",
  "business_domain": "tender_audit",
  "input_files": ["er.xlsx"]
}
```

响应：

```json
{
  "generation_id": "gen_20260625_001",
  "summary": {
    "tables": 28,
    "entities": 12,
    "relationships": 34,
    "auto_approved": 41,
    "need_review": 3,
    "blocked": 1
  },
  "can_publish": false
}
```

### 14.2 查询生成记录

```http
GET /kg-mapping/generation/{generation_id}
```

### 14.3 发布

```http
POST /kg-mapping/generation/{generation_id}/publish
```

### 14.4 回滚

```http
POST /kg-mapping/generation/{generation_id}/rollback
```

### 14.5 预览

```http
POST /kg-mapping/generation/{generation_id}/preview
```

响应：

```json
{
  "sources": {
    "company": 120,
    "project": 30,
    "bid_record": 300
  },
  "custom_kg": {
    "chunks": 450,
    "entities": 180,
    "relationships": 600
  },
  "sample_entities": [],
  "sample_relationships": [],
  "sample_chunks": []
}
```

## 15. 大模型使用边界

大模型可以作为辅助，但不应作为唯一决策依据。

适合使用大模型的场景：

- 根据表注释和字段注释推断业务实体类型名称。
- 生成关系类型名称。
- 生成更自然的 `description_template`。
- 生成 Markdown 说明文档。
- 总结审核报告中的风险项。

最终是否自动通过，应主要依赖确定性证据：

- 外键或 ER 来源
- 字段名匹配
- 字段类型匹配
- 数据覆盖率
- 置信度阈值

## 16. 分阶段落地

### 第一阶段：PostgreSQL 结构读取生成器

- 读取表、字段、主键、外键、注释。
- 生成 mapping 草案。
- 发现外键关系和字段名推断关系。
- 使用抽样数据验证关系。
- 生成 dry-run 预览。
- 保存生成记录。

### 第二阶段：合并和版本管理

- 支持 `merge`、`full_replace`、`review_only`。
- 保存 mapping 版本。
- 生成 Markdown 文档。
- 增加发布和回滚接口。

### 第三阶段：ER/Excel/DDL 上传

- 解析客户提供的结构化元数据。
- 与数据库结构读取结果合并。
- ER 声明关系优先级高于系统推断关系。

### 第四阶段：大模型辅助语义生成

- 生成更好的实体类型和关系名称。
- 生成更可读的模板。
- 生成客户侧审核说明。

## 17. 设计原则

客户侧简单，系统侧严谨。

客户侧流程：

```text
上传或选择数据库 -> 生成 -> 审核异常项 -> 发布
```

系统侧流程：

```text
证据采集 -> 评分 -> 版本管理 -> 预览 -> 可追溯发布
```

这样可以在客户数据治理较好的前提下，大幅减少人工审核工作，同时保留控制、解释和回滚能力。
