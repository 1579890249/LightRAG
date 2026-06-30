# 动态实体类型注册表设计

## 目标

让 LightRAG 的实体类型支持运行时动态配置，通过文件持久化的注册表提供增删改查接口，并让文档抽取和结构化数据库 KG 映射使用同一套已批准实体类型。

## 范围

第一阶段采用 YAML 文件作为运行时配置存储。本阶段不做 UI、不新增 PostgreSQL 配置表、不做审批流，也不自动迁移已有图节点中的 `entity_type` 值。

## 注册表存储

实体类型注册表按 workspace 隔离，存储为 YAML 文件：

```text
/app/data/entity_types/{workspace}.yaml
```

本地测试和非容器运行时，基础目录可配置，默认值为：

```text
./data/entity_types
```

文件结构如下：

```yaml
schema_version: entity_type_registry_v1
workspace: audit_customer_ys
entity_types:
  Person:
    label: 人员
    description: 自然人、联系人、专家、项目人员等
    status: active
  Organization:
    label: 组织机构
    description: 企业、采购单位、代理机构、政府部门等
    status: active
```

实体类型名称是稳定标识符，要求是非空、类似 ASCII 的符号名，适合在 prompt 和 mapping 中引用，例如 `Person`、`Organization`、`BidSubmission`、`PhoneNumber`、`ShareholdingRecord`。`label` 和 `description` 可以使用中文。

当某个 workspace 的注册表文件不存在时，系统用内置种子类型自动初始化：

- `Person`
- `Organization`
- `Project`
- `BidSubmission`
- `Document`
- `Event`
- `Certificate`
- `RevenueRecord`
- `ShareholdingRecord`
- `PhoneNumber`
- `EmailAddress`
- `Identifier`
- `Other`

`Other` 是文档抽取兜底类型，作为保留类型，不允许删除。

## CRUD 接口

新增通用认证路由，不放到 audit 专属接口下面：

```text
GET    /entity-types
POST   /entity-types
PUT    /entity-types/{name}
DELETE /entity-types/{name}
```

所有接口使用现有的 combined auth dependency。

`GET /entity-types` 返回当前 workspace 的注册表。查询参数：

- `workspace`：可选，默认使用当前 `rag.workspace`。
- `include_inactive`：可选布尔值，默认 `false`。

`POST /entity-types` 创建 active 状态的实体类型。请求体：

```json
{
  "name": "PhoneNumber",
  "label": "电话号码",
  "description": "用于识别人员、组织或项目共用联系方式的电话号码。"
}
```

如果 active 类型已存在，返回 `409`。如果该类型已存在但状态是 inactive，则重新激活并更新 `label` / `description`。

`PUT /entity-types/{name}` 更新 `label`、`description` 和 `status`。第一阶段不支持重命名，因为结构化 mapping 和已有图节点都会引用实体类型名称。

`DELETE /entity-types/{name}` 做软删除，将 `status` 设置为 `inactive`。删除 `Other` 会被拒绝。删除操作不重写已有 mapping，也不重写已有图节点。

## 文档抽取集成

当前文档抽取通过 `addon_params` / prompt profile 注入 `entity_types_guidance`。新增注册表后，注册表成为默认 guidance 来源：

1. `LightRAG._refresh_addon_params_cache()` 仍然先解析原有 prompt profile。
2. 如果显式传入 `addon_params["entity_types_guidance"]`，继续保持兼容，显式配置优先。
3. 如果没有显式 guidance，则读取当前 workspace 的实体类型注册表，并转换为 prompt guidance。
4. 将解析后的 `_entity_extraction_prompt_profile["entity_types_guidance"]` 更新为注册表生成的内容。

生成的 guidance 格式：

```text
Classify each entity using one of the following approved types. If no type fits, use `Other`.

- Person: 人员。自然人、联系人、专家、项目人员等
- Organization: 组织机构。企业、采购单位、代理机构、政府部门等
```

这样可以保持现有抽取 prompt 的入参契约不变，同时让可用实体类型变成动态配置。

## 结构化 KG 映射集成

结构化数据库 KG mapping 当前会校验 YAML 内嵌的 `entity_types`。第一阶段保留这个字段，用于 mapping 可读性和 `id_prefix` 等类型级元数据，但当传入实体类型注册表时，会额外用共享注册表校验实体类型名称。

Audit KG sync 和 audit mapping 生成 / 发布路径会读取当前 workspace 的注册表，并校验：

- mapping `entity_types` 中的每个 key 都必须是注册表中的 active 类型；
- `entities[].entity_type` 都必须是注册表中的 active 类型；
- relationship 的 `src.entity_type` 和 `tgt.entity_type` 都必须是注册表中的 active 类型。

未知类型或 inactive 类型返回 `400`，错误信息明确列出问题类型。这样文档抽取和结构化入库会共享同一个已批准类型系统。

## 错误处理与并发

读取注册表时，如果文件不存在，则自动创建种子注册表。YAML 格式错误或 schema 无效时，直接读取 API 返回服务端错误；KG sync 路径返回清晰的校验错误。

写入采用原子替换：

1. 读取当前注册表；
2. 在内存中修改；
3. 在同一目录写入临时 YAML 文件；
4. 用临时文件替换目标文件。

第一阶段不增加跨进程分布式锁。原子替换可以避免半写入文件；并发写入采用最后写入者生效，这对第一版文件注册表可以接受。

## 测试

新增聚焦测试：

- 注册表种子创建、加载、保存、创建类型、更新类型、软删除；
- API 在现有认证依赖下的 CRUD 行为；
- 文档抽取默认使用注册表生成的 guidance；
- 显式 `addon_params["entity_types_guidance"]` 仍然覆盖注册表 guidance；
- audit KG sync 遇到 unknown 或 inactive 注册表类型时拒绝 mapping。

相关测试命令：

```bash
./scripts/test.sh tests/extraction/test_entity_extraction_stability.py
./scripts/test.sh tests/api/routes/test_entity_type_routes.py
./scripts/test.sh tests/kg_mapping/test_entity_type_registry.py
./scripts/test.sh tests/api/routes/test_audit_kg_sync_routes.py
```
