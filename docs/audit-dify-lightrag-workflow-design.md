# Dify + LightRAG 审计智能体流程设计

## 1. 目标

本文档描述如何把“用户问题 -> LightRAG 检索 -> 审计分析 -> 审计结果输出”的流程编排到 Dify 智能体或 Chatflow/Workflow 中。

目标不是让大模型直接凭问题生成审计结论，而是把审计过程拆成可控步骤：

```text
用户问题
  -> 问题解析
  -> 审查规则检索
  -> LightRAG 图谱/文档检索
  -> PostgreSQL 精确事实补充
  -> 证据归并
  -> 审计判断
  -> 结构化结果输出
```

核心原则：

- Dify 负责流程编排和最终交互。
- LightRAG 负责图谱关系、文档上下文和证据片段检索。
- audit PostgreSQL 负责规则、证书、营收、人员任职等精确事实查询。
- LLM 只在证据和规则已准备好的前提下做审计判断。
- 最终结论必须能回溯到规则和证据。

## 2. 系统边界

### Dify

Dify 作为审计智能体入口，负责：

- 接收用户自然语言问题。
- 调用 LLM 节点解析意图。
- 通过 HTTP 节点调用 LightRAG 和审计后端接口。
- 通过 Code 节点归并证据。
- 通过 LLM 节点生成审计结论。
- 通过 Answer 节点返回用户可读结果。

建议第一版使用 Dify Chatflow/Workflow 的固定流程，而不是完全自由的 Agent。审计场景强调可控、可复核、可追溯，固定流程更适合第一阶段落地。

### LightRAG

LightRAG 负责：

- 基于知识图谱检索公司、人员、项目、证书、营收、投标记录等关系。
- 基于文档向量和图谱混合检索相关上下文。
- 返回可引用的文档片段、实体、关系和检索元数据。

当前 LightRAG 已有通用查询接口：

- `POST /query`
- `POST /query/data`
- `POST /query/stream`

面向 Dify 审计流程，优先建议使用 `POST /query/data`，因为它更适合取回结构化上下文、实体、关系和引用信息，再交给 Dify 后续节点处理。

### 审计业务后端

审计业务后端负责：

- 查询 `audit_rule` 审查规则。
- 查询证书、营收、人员任职等精确数据库事实。
- 必要时封装面向 Dify 的审计证据接口。

当前已存在的审查规则接口：

- `GET /audit/rules/page`
- `GET /audit/rules/detail`
- `POST /audit/rules/add`
- `POST /audit/rules/update`
- `GET /audit/rules/delete`

当前已存在的图谱同步接口：

- `POST /audit/kg-sync`

`/audit/kg-sync` 用于把业务数据库结构化事实同步到 LightRAG 图谱，不建议在用户每次提问时调用。

## 3. 数据来源

### 进入图谱的数据

以下数据适合进入 LightRAG 图谱：

- 公司。
- 人员。
- 项目。
- 投标记录。
- 公司证书。
- 公司营收。
- 人员与公司任职中间表。
- 公司与证书一对多关系。
- 公司与营收一对多关系。
- 人员与公司多对多任职关系。

图谱用于发现和扩展关系，例如：

- 某人员在哪些公司任职。
- 某公司有哪些证书。
- 某公司近几年营收情况。
- 某公司参与过哪些项目。
- 某人员和项目、公司之间是否存在关联风险。

### 不进入图谱的数据

`audit_rule` 审查规则表不进入图谱。

原因：

- 审查规则是判定依据，不是业务事实。
- 规则有启用/停用状态，需要按状态过滤。
- 规则应通过精确查询和规则匹配获得，避免图谱检索误召回。

## 4. Dify 推荐编排流程

### 4.1 Start 节点

输入字段：

```json
{
  "question": "用户审计问题"
}
```

可选输入：

```json
{
  "company_id": "已知公司 ID",
  "person_id": "已知人员 ID",
  "project_id": "已知项目 ID",
  "time_range": "审计时间范围"
}
```

### 4.2 LLM 节点：问题解析

把用户问题解析为结构化审计任务。

建议输出：

```json
{
  "intent": "certificate_compliance_check",
  "subjects": [
    {
      "type": "enterprise",
      "name": "某公司",
      "id": null
    }
  ],
  "time_range": null,
  "audit_rule_type": "资质审查",
  "need_rule": true,
  "need_lightrag": true,
  "need_precise_facts": true,
  "missing_fields": []
}
```

常见 `intent`：

- `certificate_compliance_check`：证书/资质合规检查。
- `revenue_anomaly_check`：营收异常检查。
- `person_affiliation_check`：人员任职和关联关系检查。
- `project_bid_risk_check`：项目投标风险检查。
- `general_audit_question`：一般审计问答。

### 4.3 If-Else 节点：关键信息检查

判断是否缺少必要主体。

例如：

- 用户问“这家公司证书是否过期”，但未提供公司名称或公司 ID。
- 用户问“张三是否有关联风险”，但无法定位人员。

如果缺少关键信息，直接返回澄清问题：

```text
请提供要审查的公司名称或公司 ID。
```

### 4.4 HTTP 节点：审查规则检索

根据问题解析结果查询规则。

第一阶段可以复用当前规则分页接口：

```http
GET /audit/rules/page?pageNum=1&pageSize=100
```

Dify 在 Code 节点或 LLM 节点中过滤：

- `rule_status` 为启用状态。
- `rule_type` 与问题解析结果匹配。
- `rule_name`、`rule_basis` 与用户问题相关。

更推荐后续新增面向 Dify 的规则搜索接口：

```http
POST /audit/rules/search
```

建议请求：

```json
{
  "query": "证书是否过期仍参与投标",
  "rule_type": "资质审查",
  "status": "enabled",
  "limit": 10
}
```

建议响应：

```json
{
  "rules": [
    {
      "id": "R001",
      "rule_name": "投标人资质有效性审查",
      "rule_basis": "投标人应具备有效期内的相关资质证书",
      "rule_status": "enabled",
      "rule_type": "资质审查",
      "remark": ""
    }
  ]
}
```

### 4.5 HTTP 节点：LightRAG 检索

调用 LightRAG 查询图谱和文档证据。

推荐使用：

```http
POST /query/data
```

建议参数：

```json
{
  "query": "查询某公司证书、营收、项目投标记录以及相关人员关系，用于判断是否存在审计风险",
  "mode": "mix",
  "top_k": 60,
  "chunk_top_k": 20,
  "max_entity_tokens": 6000,
  "max_relation_tokens": 8000,
  "max_total_tokens": 30000,
  "include_references": true
}
```

说明：

- `mode` 优先使用 `mix` 或 `hybrid`。
- 需要图谱关系时使用 `mix` 更稳。
- `user_prompt` 不参与检索召回，只影响检索后 LLM 如何组织答案；如果 Dify 自己负责最终审计分析，可以不依赖 LightRAG 的最终自然语言答案。

### 4.6 HTTP 节点：精确事实查询

日期、金额、状态等字段不建议只依赖 LightRAG 生成结果。

建议新增统一事实查询接口：

```http
POST /audit/facts/query
```

建议请求：

```json
{
  "subjects": [
    {
      "type": "enterprise",
      "id": "E001",
      "name": "某公司"
    }
  ],
  "fact_types": [
    "certificate",
    "revenue",
    "person_position",
    "project_bid"
  ],
  "time_range": null
}
```

建议响应：

```json
{
  "facts": [
    {
      "source_type": "db",
      "source_table": "enterprise_certificate",
      "subject_type": "enterprise",
      "subject_id": "E001",
      "fact_type": "certificate",
      "fact": "安全生产许可证有效期至 2024-12-31",
      "data": {
        "certificate_name": "安全生产许可证",
        "certificate_type": "安全生产",
        "valid_start_time": "2022-01-01",
        "valid_end_time": "2024-12-31",
        "status": "expired"
      }
    }
  ]
}
```

第一阶段如果暂不新增统一接口，也可以由 Dify 分别调用多个业务接口；但长期建议后端统一封装，减少 Dify 流程复杂度。

### 4.7 Code 节点：证据归并

把规则、LightRAG 检索结果、数据库事实合并成统一证据包。

建议证据结构：

```json
{
  "evidence_pack": {
    "question": "用户原始问题",
    "parsed_task": {},
    "matched_rules": [],
    "graph_evidence": [],
    "document_evidence": [],
    "db_facts": [],
    "warnings": []
  }
}
```

证据归并规则：

- 相同来源、相同事实去重。
- 数据库精确事实优先级高于 LLM 自然语言描述。
- 文档片段只作为佐证，不覆盖数据库字段。
- 对缺失字段记录 `warnings`，不要让模型自行补全。

### 4.8 LLM 节点：审计判断

该节点负责基于证据和规则生成审计判断。

输入应包含：

- 用户原始问题。
- 结构化任务。
- 匹配到的审查规则。
- LightRAG 图谱/文档证据。
- PostgreSQL 精确事实。
- 输出格式约束。

提示词核心要求：

```text
你是审计分析助手。只能基于提供的规则和证据作出判断。
如果证据不足，必须输出“证据不足”，不能自行推断。
每一个风险结论必须引用至少一条规则或一条证据。
日期、金额、状态以数据库事实为准。
```

### 4.9 Answer 节点：结果输出

建议最终输出同时支持结构化 JSON 和用户可读摘要。

结构化结果：

```json
{
  "conclusion": "存在疑似风险",
  "risk_level": "中",
  "findings": [
    {
      "issue": "公司证书在项目投标时可能已过期",
      "rule_ids": ["R001"],
      "rule_basis": "投标人应具备有效期内的相关资质证书",
      "evidence": [
        {
          "source_type": "db",
          "source_table": "enterprise_certificate",
          "fact": "安全生产许可证有效期至 2024-12-31"
        },
        {
          "source_type": "db",
          "source_table": "project",
          "fact": "项目投标时间为 2025-03-10"
        }
      ],
      "reasoning": "投标时间晚于证书有效结束时间，因此存在资质有效性风险。",
      "suggestion": "建议核验投标时提交的证书原件或主管部门备案信息。"
    }
  ],
  "missing_materials": []
}
```

用户可读摘要：

```text
审计结论：存在疑似资质有效性风险。

主要依据：
1. 安全生产许可证有效期至 2024-12-31。
2. 项目投标时间为 2025-03-10。
3. 规则 R001 要求投标人具备有效期内的相关资质证书。

建议：核验投标时提交的证书原件或主管部门备案信息。
```

## 5. 推荐新增后端接口

为了让 Dify 流程更简洁，建议后续新增以下接口。接口可以继续放在 `lightrag/kg_mapping` 对应扩展模块中，FastAPI 路由层只做挂载，避免污染 LightRAG 核心项目结构。

### 5.1 规则搜索

```http
POST /audit/rules/search
```

用途：

- 按问题、规则类型、状态检索审查规则。
- 返回启用规则和匹配分数。

### 5.2 精确事实查询

```http
POST /audit/facts/query
```

用途：

- 查询公司证书。
- 查询公司营收。
- 查询人员任职。
- 查询项目投标相关事实。

### 5.3 审计证据聚合

```http
POST /audit/evidence/query
```

用途：

- 后端一次性完成规则检索、精确事实查询和 LightRAG 查询。
- Dify 只负责传入用户问题和解析结果。

这是第二阶段推荐接口，可以减少 Dify 节点数量。

### 5.4 审计结果保存

```http
POST /audit/result/save
```

用途：

- 保存审计问题。
- 保存证据快照。
- 保存审计结论。
- 保存人工复核状态。

该接口不是第一阶段必需，但后续做审计闭环时需要。

## 6. 两种落地模式

### 模式 A：Dify 细粒度编排

Dify 节点分别调用规则、LightRAG、事实查询接口。

优点：

- 流程透明。
- 调试方便。
- 每个节点输出可观察。

缺点：

- Dify 流程较长。
- 错误处理逻辑分散。
- 后续维护成本偏高。

适合第一版验证。

### 模式 B：后端聚合，Dify 简编排

后端提供 `/audit/evidence/query`，Dify 只负责：

```text
问题解析 -> 调用证据聚合接口 -> LLM 审计判断 -> 输出
```

优点：

- Dify 流程简单。
- 业务逻辑集中在代码中，便于测试。
- 权限、日志、异常处理更统一。

缺点：

- 后端实现工作量更大。
- 初期调试不如 Dify 细粒度节点直观。

适合第二阶段生产化。

建议路线：

```text
第一阶段：模式 A，快速验证流程。
第二阶段：沉淀稳定逻辑到 /audit/evidence/query。
第三阶段：增加审计结果保存、人工复核、报告导出。
```

## 7. 异常和边界处理

### 缺少主体

如果问题无法定位公司、人员或项目，直接向用户追问，不进入检索流程。

### 规则为空

如果没有匹配规则：

- 可以回答一般风险分析。
- 但必须标记“未匹配到明确审查规则”。
- 不应输出确定违规结论。

### LightRAG 无相关上下文

如果 LightRAG 未召回有效图谱或文档证据：

- 保留数据库事实。
- 标记“未检索到相关文档证据”。
- 不应让模型编造证据。

### 数据库事实冲突

例如图谱描述和 PostgreSQL 字段不一致：

- PostgreSQL 精确字段优先。
- 输出中标记存在数据不一致。
- 建议人工复核源表和同步状态。

### 证据不足

如果缺少关键日期、金额、状态或文件依据：

```json
{
  "conclusion": "证据不足",
  "missing_materials": [
    "缺少项目投标时间",
    "缺少证书有效结束时间"
  ]
}
```

## 8. 安全与审计可追溯性

建议记录每次审计请求：

- 用户原始问题。
- 问题解析结果。
- 调用的规则 ID。
- LightRAG 查询参数。
- LightRAG 返回的引用。
- PostgreSQL 事实快照。
- 最终模型提示词版本。
- 最终审计结论。
- 人工复核状态。

这样后续可以解释每个结论是如何生成的，也方便定位误判来源。

## 9. 第一阶段最小可行方案

第一阶段不需要一次性实现全部接口。

最小流程：

```text
Dify Start
  -> LLM 问题解析
  -> HTTP /audit/rules/page
  -> HTTP /query/data
  -> Code 节点合并规则和 LightRAG 结果
  -> LLM 审计判断
  -> Answer 输出
```

如果涉及证书有效期、营收额、任职状态等精确字段，建议尽快补充：

```http
POST /audit/facts/query
```

否则 Dify 只能从 LightRAG 召回文本中读取这些字段，准确性不够稳定。

## 10. 后续演进

后续可以逐步增加：

- 规则搜索接口。
- 精确事实查询接口。
- 证据聚合接口。
- 审计结果保存接口。
- 审计报告导出。
- 人工复核状态流转。
- 定时或事件驱动的业务库到图谱同步。
- 数据库事实和图谱事实一致性检查。

最终形态建议：

```text
Dify
  -> 审计任务解析
  -> /audit/evidence/query
  -> 审计判断 LLM
  -> /audit/result/save
  -> 用户结果展示

LightRAG
  -> 图谱关系检索
  -> 文档证据检索

Audit PostgreSQL
  -> 审查规则
  -> 精确业务事实
```
