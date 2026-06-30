import sqlite3

from lightrag.tender_kg import (
    DatabaseTenderSource,
    DocumentDirectorySource,
    TenderKGImporter,
    TenderKGQueries,
)


def test_database_source_loads_tender_records_from_sqlite(tmp_path):
    db_path = tmp_path / "tender.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, description TEXT);
            CREATE TABLE organizations (
                id TEXT PRIMARY KEY,
                name TEXT,
                organization_type TEXT,
                aliases TEXT
            );
            CREATE TABLE people (
                id TEXT PRIMARY KEY,
                name TEXT,
                organization_id TEXT,
                aliases TEXT
            );
            CREATE TABLE tenders (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                tenderer_org_id TEXT,
                name TEXT
            );
            CREATE TABLE bids (
                id TEXT PRIMARY KEY,
                tender_id TEXT,
                bidder_org_id TEXT,
                document_id TEXT,
                amount TEXT,
                contact_person_ids TEXT
            );
            INSERT INTO projects VALUES ('P2024-001', '智慧园区建设项目', '园区弱电与平台建设');
            INSERT INTO organizations VALUES ('91300001', '招标科技有限公司', 'tenderer', '');
            INSERT INTO organizations VALUES ('91300002', '投标建设有限公司', 'bidder', '投标建设,建设公司');
            INSERT INTO people VALUES ('ID10001', '王工', '91300002', '王经理');
            INSERT INTO tenders VALUES ('T2024-001-A', 'P2024-001', '91300001', '智慧园区建设项目一标段');
            INSERT INTO bids VALUES (
                'T2024-001-A:91300002',
                'T2024-001-A',
                '91300002',
                'BIDBOOK-0001',
                '1234万元',
                'ID10001'
            );
            """
        )

    source = DatabaseTenderSource(
        connection_url=f"sqlite:///{db_path}",
        queries=TenderKGQueries(
            projects="SELECT id, name, description FROM projects",
            organizations=(
                "SELECT id, name, organization_type, aliases FROM organizations"
            ),
            people="SELECT id, name, organization_id, aliases FROM people",
            tender_events=(
                "SELECT id, project_id, tenderer_org_id, name FROM tenders"
            ),
            bid_submissions=(
                "SELECT id, tender_id, bidder_org_id, document_id, amount, "
                "contact_person_ids FROM bids"
            ),
        ),
    )

    dataset = source.load()

    assert dataset.projects[0].id == "P2024-001"
    assert dataset.organizations[1].aliases == ["投标建设", "建设公司"]
    assert dataset.people[0].organization_id == "91300002"
    assert dataset.bid_submissions[0].contact_person_ids == ["ID10001"]


def test_importer_builds_custom_kg_from_database_and_real_documents(tmp_path):
    db_path = tmp_path / "tender.db"
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "BIDBOOK-0001.txt").write_text(
        "投标建设有限公司提交投标文件，报价1234万元，项目经理王工。",
        encoding="utf-8",
    )

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE organizations (id TEXT PRIMARY KEY, name TEXT);
            CREATE TABLE tenders (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                tenderer_org_id TEXT,
                name TEXT
            );
            CREATE TABLE bids (
                id TEXT PRIMARY KEY,
                tender_id TEXT,
                bidder_org_id TEXT,
                document_id TEXT
            );
            INSERT INTO projects VALUES ('P2024-001', '智慧园区建设项目');
            INSERT INTO organizations VALUES ('91300001', '招标科技有限公司');
            INSERT INTO organizations VALUES ('91300002', '投标建设有限公司');
            INSERT INTO tenders VALUES ('T2024-001-A', 'P2024-001', '91300001', '智慧园区建设项目一标段');
            INSERT INTO bids VALUES ('T2024-001-A:91300002', 'T2024-001-A', '91300002', 'BIDBOOK-0001');
            """
        )

    importer = TenderKGImporter(
        database_source=DatabaseTenderSource(
            connection_url=f"sqlite:///{db_path}",
            queries=TenderKGQueries(
                projects="SELECT id, name FROM projects",
                organizations="SELECT id, name FROM organizations",
                tender_events=(
                    "SELECT id, project_id, tenderer_org_id, name FROM tenders"
                ),
                bid_submissions=(
                    "SELECT id, tender_id, bidder_org_id, document_id FROM bids"
                ),
            ),
        ),
        document_source=DocumentDirectorySource(docs_dir),
    )

    custom_kg = importer.build_custom_kg()

    assert any(
        chunk["source_id"] == "doc:Document:BIDBOOK-0001"
        and "报价1234万元" in chunk["content"]
        and chunk["file_path"].endswith("BIDBOOK-0001.txt")
        for chunk in custom_kg["chunks"]
    )
    assert any(
        rel["src_id"] == "BidSubmission:T2024-001-A:91300002"
        and rel["tgt_id"] == "Document:BIDBOOK-0001"
        and rel["keywords"] == "HAS_DOCUMENT"
        for rel in custom_kg["relationships"]
    )
