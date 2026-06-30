from lightrag.tender_kg import (
    AwardEvent,
    BidSubmission,
    EvaluationEvent,
    Organization,
    Person,
    Project,
    TenderDocument,
    TenderEvent,
    TenderKGBuilder,
)


def _by_name(items):
    return {item["entity_name"]: item for item in items}


def _relationship_keys(relationships):
    return {
        (item["src_id"], item["tgt_id"], item["keywords"])
        for item in relationships
    }


def test_builder_creates_event_centered_custom_kg():
    builder = TenderKGBuilder()

    custom_kg = builder.build(
        projects=[
            Project(id="P2024-001", name="智慧园区建设项目"),
        ],
        organizations=[
            Organization(id="91300001", name="招标科技有限公司"),
            Organization(id="91300002", name="投标建设有限公司"),
        ],
        people=[
            Person(id="ID10001", name="王工", organization_id="91300002"),
            Person(id="ID20001", name="李评委"),
        ],
        tender_events=[
            TenderEvent(
                id="T2024-001-A",
                project_id="P2024-001",
                tenderer_org_id="91300001",
                name="智慧园区建设项目一标段",
            )
        ],
        bid_submissions=[
            BidSubmission(
                id="T2024-001-A:91300002",
                tender_id="T2024-001-A",
                bidder_org_id="91300002",
                document_id="BIDBOOK-0001",
                amount="1234万元",
            )
        ],
        evaluation_events=[
            EvaluationEvent(
                id="EVAL-T2024-001-A",
                tender_id="T2024-001-A",
                reviewer_person_ids=["ID20001"],
                bid_submission_ids=["T2024-001-A:91300002"],
            )
        ],
        award_events=[
            AwardEvent(
                id="AWARD-T2024-001-A",
                tender_id="T2024-001-A",
                winner_org_id="91300002",
                bid_submission_id="T2024-001-A:91300002",
            )
        ],
        documents=[
            TenderDocument(
                id="BIDBOOK-0001",
                title="投标建设有限公司投标文件",
                content="报价1234万元，项目经理王工，工期180天。",
                file_path="bids/BIDBOOK-0001.pdf",
            )
        ],
    )

    entities = _by_name(custom_kg["entities"])
    assert "Project:P2024-001" in entities
    assert "TenderEvent:T2024-001-A" in entities
    assert "BidSubmission:T2024-001-A:91300002" in entities
    assert "EvaluationEvent:EVAL-T2024-001-A" in entities
    assert "AwardEvent:AWARD-T2024-001-A" in entities
    assert "Organization:91300001" in entities
    assert "Organization:91300002" in entities
    assert "Person:ID10001" in entities
    assert "Document:BIDBOOK-0001" in entities

    relationships = _relationship_keys(custom_kg["relationships"])
    assert (
        "TenderEvent:T2024-001-A",
        "Project:P2024-001",
        "FOR_PROJECT",
    ) in relationships
    assert (
        "TenderEvent:T2024-001-A",
        "Organization:91300001",
        "TENDERER",
    ) in relationships
    assert (
        "BidSubmission:T2024-001-A:91300002",
        "Organization:91300002",
        "BIDDER",
    ) in relationships
    assert (
        "BidSubmission:T2024-001-A:91300002",
        "Document:BIDBOOK-0001",
        "HAS_DOCUMENT",
    ) in relationships
    assert (
        "EvaluationEvent:EVAL-T2024-001-A",
        "Person:ID20001",
        "REVIEWER",
    ) in relationships
    assert (
        "AwardEvent:AWARD-T2024-001-A",
        "Organization:91300002",
        "WINNER",
    ) in relationships

    assert any(
        chunk["source_id"] == "doc:Document:BIDBOOK-0001"
        and "报价1234万元" in chunk["content"]
        and chunk["file_path"] == "bids/BIDBOOK-0001.pdf"
        for chunk in custom_kg["chunks"]
    )


def test_builder_keeps_document_evidence_as_relationship_source():
    custom_kg = TenderKGBuilder().build(
        organizations=[
            Organization(id="91300002", name="投标建设有限公司"),
        ],
        tender_events=[
            TenderEvent(
                id="T2024-001-A",
                project_id="P2024-001",
                tenderer_org_id="91300001",
            )
        ],
        bid_submissions=[
            BidSubmission(
                id="T2024-001-A:91300002",
                tender_id="T2024-001-A",
                bidder_org_id="91300002",
                document_id="BIDBOOK-0001",
            )
        ],
        documents=[
            TenderDocument(
                id="BIDBOOK-0001",
                title="投标文件",
                content="投标建设有限公司提交投标文件。",
            )
        ],
    )

    has_document = next(
        item
        for item in custom_kg["relationships"]
        if item["keywords"] == "HAS_DOCUMENT"
    )

    assert has_document["source_id"] == "doc:Document:BIDBOOK-0001"
    assert has_document["file_path"] == "custom_kg"


def test_builder_rejects_duplicate_entity_keys():
    builder = TenderKGBuilder()

    try:
        builder.build(
            organizations=[
                Organization(id="91300002", name="投标建设有限公司"),
                Organization(id="91300002", name="重复公司"),
            ]
        )
    except ValueError as exc:
        assert "Duplicate tender KG entity key: Organization:91300002" in str(exc)
    else:
        raise AssertionError("Expected duplicate organization IDs to fail")


def test_builder_includes_aliases_and_bid_contact_relationships():
    custom_kg = TenderKGBuilder().build(
        organizations=[
            Organization(
                id="91300002",
                name="投标建设有限公司",
                aliases=["投标建设", "建设公司"],
            )
        ],
        people=[
            Person(
                id="ID10001",
                name="王工",
                organization_id="91300002",
                aliases=["王经理"],
            )
        ],
        bid_submissions=[
            BidSubmission(
                id="T2024-001-A:91300002",
                tender_id="T2024-001-A",
                bidder_org_id="91300002",
                contact_person_ids=["ID10001"],
            )
        ],
    )

    entities = _by_name(custom_kg["entities"])
    assert "aliases=投标建设, 建设公司" in entities["Organization:91300002"][
        "description"
    ]
    assert "aliases=王经理" in entities["Person:ID10001"]["description"]

    relationships = _relationship_keys(custom_kg["relationships"])
    assert (
        "Person:ID10001",
        "BidSubmission:T2024-001-A:91300002",
        "REPRESENTS_BID",
    ) in relationships
