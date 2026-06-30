from lightrag.kg_mapping import ConfigurableKGBuilder, load_mapping_config
from lightrag.utils import compute_mdhash_id


def _entity_names(custom_kg):
    return {item["entity_name"] for item in custom_kg["entities"]}


def _relation_keys(custom_kg):
    return {
        (item["src_id"], item["tgt_id"], item["keywords"])
        for item in custom_kg["relationships"]
    }


def test_builder_maps_configured_entities_and_relationships_to_custom_kg():
    config = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "enterprise": {
                    "primary_key": "enterprise_id",
                },
                "person": {
                    "primary_key": "person_id",
                },
            },
            "entity_types": {
                "Organization": {"id_prefix": "Organization"},
                "Person": {"id_prefix": "Person"},
            },
            "entities": [
                {
                    "source": "enterprise",
                    "entity_type": "Organization",
                    "id_field": "enterprise_id",
                    "name_field": "enterprise_name",
                    "description_template": (
                        "{enterprise_name}; industry={industry}"
                    ),
                    "metadata_fields": ["status"],
                },
                {
                    "source": "person",
                    "entity_type": "Person",
                    "id_field": "person_id",
                    "name_field": "name",
                    "metadata_fields": ["position"],
                },
            ],
            "relationships": [
                {
                    "source": "person",
                    "relation_type": "EMPLOYED_BY",
                    "src": {"entity_type": "Person", "id_field": "person_id"},
                    "tgt": {
                        "entity_type": "Organization",
                        "id_field": "enterprise_id",
                    },
                    "description_template": "{name} works for {enterprise_id}.",
                }
            ],
        }
    )

    result = ConfigurableKGBuilder(config).build(
        {
            "enterprise": [
                {
                    "enterprise_id": "E001",
                    "enterprise_name": "Shenzhen Huaxin Technology Co Ltd",
                    "industry": "IT services",
                    "status": "active",
                }
            ],
            "person": [
                {
                    "person_id": "P001",
                    "name": "Zhang Wei",
                    "enterprise_id": "E001",
                    "position": "legal representative",
                },
                {
                    "person_id": "P009",
                    "name": "Chen Gong",
                    "enterprise_id": None,
                    "position": "evaluator",
                },
            ],
        }
    )

    assert result.custom_kg["chunks"] == [
        {
            "source_id": "db://audit/enterprise/E001",
            "content": (
                "source=enterprise; enterprise_id=E001; "
                "enterprise_name=Shenzhen Huaxin Technology Co Ltd; "
                "industry=IT services; status=active"
            ),
            "file_path": "db://audit/enterprise/E001",
        },
        {
            "source_id": "db://audit/person/P001",
            "content": (
                "source=person; enterprise_id=E001; name=Zhang Wei; "
                "person_id=P001; position=legal representative"
            ),
            "file_path": "db://audit/person/P001",
        },
        {
            "source_id": "db://audit/person/P009",
            "content": (
                "source=person; enterprise_id=; name=Chen Gong; person_id=P009; "
                "position=evaluator"
            ),
            "file_path": "db://audit/person/P009",
        },
    ]
    assert _entity_names(result.custom_kg) == {
        "Organization:E001",
        "Person:P001",
        "Person:P009",
    }
    assert _relation_keys(result.custom_kg) == {
        ("Person:P001", "Organization:E001", "EMPLOYED_BY"),
    }

    organization = next(
        item
        for item in result.custom_kg["entities"]
        if item["entity_name"] == "Organization:E001"
    )
    assert organization["source_id"] == "db://audit/enterprise/E001"
    assert organization["description"] == (
        "Shenzhen Huaxin Technology Co Ltd; industry=IT services; status=active"
    )


def test_builder_tracks_emitted_graph_items_per_source_row():
    config = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "bid_record": {"primary_key": "bid_id"},
            },
            "entity_types": {
                "BidSubmission": {"id_prefix": "BidSubmission"},
                "Project": {"id_prefix": "Project"},
                "Organization": {"id_prefix": "Organization"},
            },
            "entities": [
                {
                    "source": "bid_record",
                    "entity_type": "BidSubmission",
                    "id_field": "bid_id",
                    "name_template": "Bid {bid_id}",
                    "metadata_fields": "*",
                }
            ],
            "relationships": [
                {
                    "source": "bid_record",
                    "relation_type": "FOR_PROJECT",
                    "src": {"entity_type": "BidSubmission", "id_field": "bid_id"},
                    "tgt": {"entity_type": "Project", "id_field": "project_id"},
                },
                {
                    "source": "bid_record",
                    "relation_type": "BIDDER",
                    "src": {"entity_type": "BidSubmission", "id_field": "bid_id"},
                    "tgt": {
                        "entity_type": "Organization",
                        "id_field": "enterprise_id",
                    },
                },
            ],
        }
    )

    result = ConfigurableKGBuilder(config).build(
        {
            "bid_record": [
                {
                    "bid_id": "B1",
                    "project_id": "PJT001",
                    "enterprise_id": "E001",
                    "bid_amount": "10100000",
                    "rank": 1,
                }
            ]
        }
    )

    assert list(result.sync_records) == [
        {
            "source": "bid_record",
            "primary_key": "B1",
            "source_id": "db://audit/bid_record/B1",
            "row_hash": result.sync_records[0]["row_hash"],
            "entities": ["BidSubmission:B1"],
            "relationships": [
                {
                    "src_id": "BidSubmission:B1",
                    "tgt_id": "Project:PJT001",
                    "keywords": "FOR_PROJECT",
                },
                {
                    "src_id": "BidSubmission:B1",
                    "tgt_id": "Organization:E001",
                    "keywords": "BIDDER",
                },
            ],
            "chunks": ["db://audit/bid_record/B1"],
            "chunk_ids": [
                compute_mdhash_id(
                    "source=bid_record; bid_amount=10100000; "
                    "bid_id=B1; enterprise_id=E001; project_id=PJT001; rank=1",
                    prefix="chunk-",
                )
            ],
        }
    ]
    assert result.sync_records[0]["row_hash"]


def test_builder_exposes_related_entity_names_to_relationship_templates():
    config = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "person": {"primary_key": "person_id"},
                "person_relation": {"primary_key": "id"},
            },
            "entity_types": {
                "Person": {"id_prefix": "Person"},
            },
            "entities": [
                {
                    "source": "person",
                    "entity_type": "Person",
                    "id_field": "person_id",
                    "name_field": "name",
                    "metadata_fields": ["position"],
                }
            ],
            "relationships": [
                {
                    "source": "person_relation",
                    "relation_type": "PERSON_RELATED",
                    "src": {"entity_type": "Person", "id_field": "person_id_1"},
                    "tgt": {"entity_type": "Person", "id_field": "person_id_2"},
                    "description_template": (
                        "{person_id_1_name} and {person_id_2_name} "
                        "have relation {relation_type}."
                    ),
                }
            ],
        }
    )

    result = ConfigurableKGBuilder(config).build(
        {
            "person": [
                {
                    "person_id": "P001",
                    "name": "Zhang Wei",
                    "position": "legal representative",
                },
                {
                    "person_id": "P009",
                    "name": "Chen Gong",
                    "position": "expert",
                },
            ],
            "person_relation": [
                {
                    "id": 1,
                    "person_id_1": "P001",
                    "person_id_2": "P009",
                    "relation_type": "relative",
                }
            ],
        }
    )

    relation = result.custom_kg["relationships"][0]
    assert relation["src_id"] == "Person:P001"
    assert relation["tgt_id"] == "Person:P009"
    assert relation["description"] == "Zhang Wei and Chen Gong have relation relative."


def test_builder_can_use_display_names_as_graph_entity_ids():
    config = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "person": {"primary_key": "person_id"},
                "person_relation": {"primary_key": "id"},
            },
            "entity_types": {
                "Person": {"id_prefix": "Person"},
            },
            "entities": [
                {
                    "source": "person",
                    "entity_type": "Person",
                    "id_field": "person_id",
                    "name_field": "name",
                    "entity_name_template": "{name}",
                    "metadata_fields": ["person_id"],
                }
            ],
            "relationships": [
                {
                    "source": "person_relation",
                    "relation_type": "PERSON_RELATED",
                    "src": {"entity_type": "Person", "id_field": "person_id_1"},
                    "tgt": {"entity_type": "Person", "id_field": "person_id_2"},
                    "description_template": (
                        "{person_id_1_name} 与 {person_id_2_name} "
                        "存在人员关系：{relation_type}。"
                    ),
                }
            ],
        }
    )

    result = ConfigurableKGBuilder(config).build(
        {
            "person": [
                {"person_id": "P002", "name": "张敏"},
                {"person_id": "P004", "name": "李娜"},
            ],
            "person_relation": [
                {
                    "id": 2,
                    "person_id_1": "P002",
                    "person_id_2": "P004",
                    "relation_type": "夫妻",
                }
            ],
        }
    )

    assert _entity_names(result.custom_kg) == {"张敏", "李娜"}
    relation = result.custom_kg["relationships"][0]
    assert relation["src_id"] == "张敏"
    assert relation["tgt_id"] == "李娜"
    assert relation["description"] == "张敏 与 李娜 存在人员关系：夫妻。"
    relation_chunk = result.custom_kg["chunks"][2]
    assert relation_chunk == {
        "source_id": "db://audit/person_relation/2",
        "content": (
            "source=person_relation; relationship=张敏 与 李娜 存在人员关系：夫妻。; "
            "person_id_1_name=张敏; person_id_2_name=李娜; relation_type=夫妻; id=2"
        ),
        "file_path": "db://audit/person_relation/2",
    }
    assert result.sync_records[2]["relationships"] == [
        {"src_id": "张敏", "tgt_id": "李娜", "keywords": "PERSON_RELATED"}
    ]
    assert result.sync_records[2]["chunk_ids"] == [
        compute_mdhash_id(relation_chunk["content"], prefix="chunk-")
    ]


def test_builder_hides_raw_endpoint_ids_in_relationship_only_chunks():
    config = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "person": {"primary_key": "person_id"},
                "person_relation": {"primary_key": "id"},
            },
            "entity_types": {
                "Person": {"id_prefix": "Person"},
            },
            "entities": [
                {
                    "source": "person",
                    "entity_type": "Person",
                    "id_field": "person_id",
                    "name_field": "name",
                    "entity_name_template": "{name}",
                }
            ],
            "relationships": [
                {
                    "source": "person_relation",
                    "relation_type": "PERSON_RELATED",
                    "src": {"entity_type": "Person", "id_field": "person_id_1"},
                    "tgt": {"entity_type": "Person", "id_field": "person_id_2"},
                    "description_template": (
                        "{person_id_1_name} 与 {person_id_2_name} "
                        "存在人员关系：{relation_type}。"
                    ),
                }
            ],
        }
    )

    result = ConfigurableKGBuilder(config).build(
        {
            "person": [
                {"person_id": "P002", "name": "张敏"},
                {"person_id": "P004", "name": "李娜"},
            ],
            "person_relation": [
                {
                    "id": 2,
                    "person_id_1": "P002",
                    "person_id_2": "P004",
                    "relation_type": "夫妻",
                }
            ],
        }
    )

    relation_chunk = next(
        chunk
        for chunk in result.custom_kg["chunks"]
        if chunk["source_id"] == "db://audit/person_relation/2"
    )

    assert "张敏 与 李娜 存在人员关系：夫妻。" in relation_chunk["content"]
    assert "person_id_1=P002" not in relation_chunk["content"]
    assert "person_id_2=P004" not in relation_chunk["content"]


def test_builder_enriches_person_relation_chunks_with_project_bid_context():
    config = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "person": {"primary_key": "person_id"},
                "person_relation": {"primary_key": "id"},
                "person_enterprise_position": {"primary_key": "id"},
                "bid_record": {"primary_key": "bid_id"},
                "project": {"primary_key": "project_id"},
            },
            "entity_types": {
                "Person": {"id_prefix": "Person"},
            },
            "entities": [
                {
                    "source": "person",
                    "entity_type": "Person",
                    "id_field": "person_id",
                    "name_field": "name",
                    "entity_name_template": "{name}",
                }
            ],
            "relationships": [
                {
                    "source": "person_relation",
                    "relation_type": "PERSON_RELATED",
                    "src": {"entity_type": "Person", "id_field": "person_id_1"},
                    "tgt": {"entity_type": "Person", "id_field": "person_id_2"},
                    "description_template": (
                        "{person_id_1_name} 与 {person_id_2_name} "
                        "存在人员关系：{relation_type}。"
                    ),
                }
            ],
        }
    )

    result = ConfigurableKGBuilder(config).build(
        {
            "person": [
                {"person_id": "P002", "name": "张敏"},
                {"person_id": "P004", "name": "李娜"},
            ],
            "person_relation": [
                {
                    "id": 2,
                    "person_id_1": "P002",
                    "person_id_2": "P004",
                    "relation_type": "夫妻",
                }
            ],
            "person_enterprise_position": [
                {
                    "id": 1,
                    "person_id": "P002",
                    "person_name": "张敏",
                    "enterprise_id": "E001",
                    "enterprise_name": "深圳华信科技有限公司",
                    "position": "高管",
                    "status": 1,
                },
                {
                    "id": 2,
                    "person_id": "P004",
                    "person_name": "李娜",
                    "enterprise_id": "E002",
                    "enterprise_name": "深圳智达信息有限公司",
                    "position": "高管",
                    "status": 1,
                },
            ],
            "bid_record": [
                {"bid_id": "B10", "project_id": "PJT004", "enterprise_id": "E001"},
                {"bid_id": "B11", "project_id": "PJT004", "enterprise_id": "E002"},
            ],
            "project": [
                {"project_id": "PJT004", "project_name": "公共安全AI审计系统"},
            ],
        }
    )

    relation_chunk = next(
        chunk
        for chunk in result.custom_kg["chunks"]
        if chunk["source_id"] == "db://audit/person_relation/2"
    )

    assert "person_id_1_enterprise_name=深圳华信科技有限公司" in relation_chunk["content"]
    assert "person_id_2_enterprise_name=深圳智达信息有限公司" in relation_chunk["content"]
    assert "common_project_names=公共安全AI审计系统" in relation_chunk["content"]
    assert (
        "relationship_context=张敏（深圳华信科技有限公司，高管）与"
        "李娜（深圳智达信息有限公司，高管）存在人员关系：夫妻；"
        "双方企业共同参与投标项目：公共安全AI审计系统。"
    ) in relation_chunk["content"]


def test_audit_mapping_uses_display_names_for_project_and_organization_nodes():
    config = load_mapping_config("configs/kg_mappings/audit_customer_ys.yaml")

    result = ConfigurableKGBuilder(config).build(
        {
            "enterprise": [
                {
                    "enterprise_id": "E001",
                    "enterprise_name": "深圳华信科技有限公司",
                    "industry": "软件开发",
                    "business_scope": "AI审计系统研发",
                    "registered_address": "深圳市南山区",
                    "status": "存续",
                }
            ],
            "project": [
                {
                    "project_id": "PJT004",
                    "project_name": "公共安全AI审计系统",
                    "tender_org": "市公安局",
                    "budget": "10000000",
                    "bid_time": "2026-01-01",
                    "status": "招标中",
                }
            ],
            "bid_record": [
                {
                    "bid_id": "B10",
                    "project_id": "PJT004",
                    "project_name": "公共安全AI审计系统",
                    "enterprise_id": "E001",
                    "enterprise_name": "深圳华信科技有限公司",
                    "bid_amount": "9800000",
                    "rank": 1,
                }
            ],
        }
    )

    bid_name = "深圳华信科技有限公司投标公共安全AI审计系统（B10）"
    assert "深圳华信科技有限公司" in _entity_names(result.custom_kg)
    assert "公共安全AI审计系统" in _entity_names(result.custom_kg)
    assert bid_name in _entity_names(result.custom_kg)
    assert "Organization:E001" not in _entity_names(result.custom_kg)
    assert "Project:PJT004" not in _entity_names(result.custom_kg)
    assert "BidSubmission:B10" not in _entity_names(result.custom_kg)
    assert _relation_keys(result.custom_kg) == {
        (bid_name, "公共安全AI审计系统", "FOR_PROJECT"),
        (bid_name, "深圳华信科技有限公司", "BIDDER"),
    }

    organization = next(
        item
        for item in result.custom_kg["entities"]
        if item["entity_name"] == "深圳华信科技有限公司"
    )
    project = next(
        item
        for item in result.custom_kg["entities"]
        if item["entity_name"] == "公共安全AI审计系统"
    )
    assert "enterprise_id=E001" in organization["description"]
    assert "project_id=PJT004" in project["description"]
    bid = next(
        item
        for item in result.custom_kg["entities"]
        if item["entity_name"] == bid_name
    )
    assert "bid_id=B10" in bid["description"]
    assert "enterprise_id=E001" in bid["description"]
    assert "project_id=PJT004" in bid["description"]


def test_audit_mapping_links_project_and_bid_person_roles():
    config = load_mapping_config("configs/kg_mappings/audit_customer_ys.yaml")

    result = ConfigurableKGBuilder(config).build(
        {
            "enterprise": [
                {
                    "enterprise_id": "E001",
                    "enterprise_name": "投标建设有限公司",
                    "industry": "建筑",
                    "business_scope": "工程施工",
                    "registered_address": "深圳",
                    "status": "存续",
                },
                {
                    "enterprise_id": "E002",
                    "enterprise_name": "招标科技有限公司",
                    "industry": "采购",
                    "business_scope": "招标采购",
                    "registered_address": "广州",
                    "status": "存续",
                },
            ],
            "person": [
                {"person_id": "P001", "name": "王投标", "phone": "", "address": "", "is_expert": 0},
                {"person_id": "P002", "name": "赵招标", "phone": "", "address": "", "is_expert": 0},
            ],
            "project": [
                {
                    "project_id": "PJT001",
                    "project_name": "智慧园区项目",
                    "tender_org_id": "E002",
                    "tender_org": "招标科技有限公司",
                    "budget": "1000000",
                    "bid_time": "2026-01-01",
                    "status": "招标中",
                }
            ],
            "bid_record": [
                {
                    "bid_id": "B001",
                    "project_id": "PJT001",
                    "project_name": "智慧园区项目",
                    "enterprise_id": "E001",
                    "enterprise_name": "投标建设有限公司",
                    "bid_amount": "990000",
                    "rank": 1,
                }
            ],
            "project_person_role": [
                {
                    "id": 1,
                    "project_id": "PJT001",
                    "project_name": "智慧园区项目",
                    "person_id": "P002",
                    "person_name": "赵招标",
                    "enterprise_id": "E002",
                    "enterprise_name": "招标科技有限公司",
                    "side": "TENDERER",
                    "role_type": "TENDER_CONTACT",
                    "start_date": "2025-12-01",
                    "end_date": "",
                    "status": "active",
                    "remark": "招标经办人",
                }
            ],
            "bid_person_role": [
                {
                    "id": 1,
                    "bid_id": "B001",
                    "bid_name": "投标建设有限公司投标智慧园区项目（B001）",
                    "project_id": "PJT001",
                    "project_name": "智慧园区项目",
                    "enterprise_id": "E001",
                    "enterprise_name": "投标建设有限公司",
                    "person_id": "P001",
                    "person_name": "王投标",
                    "role_type": "BID_CONTACT",
                    "start_date": "2025-12-01",
                    "end_date": "",
                    "status": "active",
                    "remark": "投标联系人",
                }
            ],
        }
    )

    bid_name = "投标建设有限公司投标智慧园区项目（B001）"
    assert _relation_keys(result.custom_kg) >= {
        ("智慧园区项目", "招标科技有限公司", "TENDERED_BY"),
        ("智慧园区项目", "赵招标", "PROJECT_PERSON_ROLE"),
        ("赵招标", "招标科技有限公司", "PROJECT_ROLE_ORG"),
        (bid_name, "王投标", "BID_PERSON_ROLE"),
        ("王投标", "投标建设有限公司", "BID_ROLE_ORG"),
    }

    project_role_chunk = next(
        chunk
        for chunk in result.custom_kg["chunks"]
        if chunk["source_id"] == "db://audit/project_person_role/1"
    )
    assert "role_type=TENDER_CONTACT" in project_role_chunk["content"]
    assert "side=TENDERER" in project_role_chunk["content"]

    bid_role_chunk = next(
        chunk
        for chunk in result.custom_kg["chunks"]
        if chunk["source_id"] == "db://audit/bid_person_role/1"
    )
    assert "role_type=BID_CONTACT" in bid_role_chunk["content"]
    assert "person_name=王投标" in bid_role_chunk["content"]


def test_audit_mapping_links_shareholding_records_by_name():
    config = load_mapping_config("configs/kg_mappings/audit_customer_ys.yaml")

    result = ConfigurableKGBuilder(config).build(
        {
            "enterprise": [
                {
                    "enterprise_id": "E001",
                    "enterprise_name": "深圳华信科技有限公司",
                    "industry": "软件开发",
                    "business_scope": "AI审计系统研发",
                    "registered_address": "深圳",
                    "status": "存续",
                },
                {
                    "enterprise_id": "E002",
                    "enterprise_name": "深圳智达信息有限公司",
                    "industry": "数据服务",
                    "business_scope": "数据治理",
                    "registered_address": "深圳",
                    "status": "存续",
                },
            ],
            "person": [
                {
                    "person_id": "P001",
                    "name": "张伟",
                    "phone": "",
                    "address": "",
                    "is_expert": 0,
                }
            ],
            "enterprise_shareholding": [
                {
                    "id": 1,
                    "enterprise_name": "深圳华信科技有限公司",
                    "target_enterprise_id": "E001",
                    "target_enterprise_name": "深圳华信科技有限公司",
                    "holder_type": 1,
                    "holder_name": "张伟",
                    "holder_person_id": "P001",
                    "holder_person_name": "张伟",
                    "holder_enterprise_id": "",
                    "holder_enterprise_name": "",
                    "shareholding_ratio": "35.5",
                },
                {
                    "id": 2,
                    "enterprise_name": "深圳华信科技有限公司",
                    "target_enterprise_id": "E001",
                    "target_enterprise_name": "深圳华信科技有限公司",
                    "holder_type": 2,
                    "holder_name": "深圳智达信息有限公司",
                    "holder_person_id": "",
                    "holder_person_name": "",
                    "holder_enterprise_id": "E002",
                    "holder_enterprise_name": "深圳智达信息有限公司",
                    "shareholding_ratio": "15",
                },
            ],
        }
    )

    natural_person_record = "张伟持有深圳华信科技有限公司35.5%股权（1）"
    enterprise_record = "深圳智达信息有限公司持有深圳华信科技有限公司15%股权（2）"
    assert natural_person_record in _entity_names(result.custom_kg)
    assert enterprise_record in _entity_names(result.custom_kg)
    assert _relation_keys(result.custom_kg) >= {
        (
            natural_person_record,
            "深圳华信科技有限公司",
            "SHAREHOLDING_TARGET",
        ),
        (natural_person_record, "张伟", "NATURAL_PERSON_SHAREHOLDER"),
        (
            enterprise_record,
            "深圳华信科技有限公司",
            "SHAREHOLDING_TARGET",
        ),
        (enterprise_record, "深圳智达信息有限公司", "ENTERPRISE_SHAREHOLDER"),
    }
    assert not any(
        relation["keywords"] == "ENTERPRISE_SHAREHOLDER"
        and relation["src_id"] == natural_person_record
        for relation in result.custom_kg["relationships"]
    )
    assert not any(
        relation["keywords"] == "NATURAL_PERSON_SHAREHOLDER"
        and relation["src_id"] == enterprise_record
        for relation in result.custom_kg["relationships"]
    )

    descriptions = "\n".join(
        item["description"] for item in result.custom_kg["relationships"]
    )
    assert "张伟 holds 35.5% of 深圳华信科技有限公司." in descriptions
    assert (
        "深圳智达信息有限公司 holds 15% of 深圳华信科技有限公司."
        in descriptions
    )


def test_builder_sync_hash_changes_when_mapping_projection_changes():
    base_config = {
        "schema_version": "audit_kg_v1",
        "database_name": "audit",
        "sources": {
            "person": {"primary_key": "person_id"},
        },
        "entity_types": {
            "Person": {"id_prefix": "Person"},
        },
        "entities": [
            {
                "source": "person",
                "entity_type": "Person",
                "id_field": "person_id",
                "name_field": "name",
            }
        ],
    }
    rows_by_source = {
        "person": [
            {
                "person_id": "P004",
                "name": "李娜",
            }
        ]
    }

    id_based = ConfigurableKGBuilder(load_mapping_config(base_config)).build(
        rows_by_source
    )
    display_config = dict(base_config)
    display_config["entities"] = [
        {
            **base_config["entities"][0],
            "entity_name_template": "{name}",
        }
    ]
    display_based = ConfigurableKGBuilder(load_mapping_config(display_config)).build(
        rows_by_source
    )

    assert id_based.sync_records[0]["entities"] == ["Person:P004"]
    assert display_based.sync_records[0]["entities"] == ["李娜"]
    assert id_based.sync_records[0]["row_hash"] != display_based.sync_records[0][
        "row_hash"
    ]
