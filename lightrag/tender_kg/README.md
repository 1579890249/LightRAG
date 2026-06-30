# Tender KG Adapter

This package builds LightRAG `custom_kg` payloads for tender and bidding
scenarios. It is intentionally a domain adapter, not a replacement for
LightRAG's document pipeline.

## Model

The adapter uses stable business IDs as graph node names:

- `Project:<project_id>`
- `TenderEvent:<tender_id>`
- `BidSubmission:<bid_submission_id>`
- `EvaluationEvent:<evaluation_id>`
- `AwardEvent:<award_id>`
- `Organization:<organization_id>`
- `Person:<person_id>`
- `Document:<document_id>`

Tender participation is modeled as an event node (`BidSubmission`) instead of a
direct company-project edge. This keeps bid amount, bid document, contact
people, evaluation, award status, and evidence attached to the concrete tender
event.

## Usage

```python
from lightrag.tender_kg import (
    BidSubmission,
    Organization,
    Project,
    TenderDocument,
    TenderEvent,
    TenderKGBuilder,
)

custom_kg = TenderKGBuilder().build(
    projects=[Project(id="P2024-001", name="智慧园区建设项目")],
    organizations=[
        Organization(id="91300001", name="招标科技有限公司"),
        Organization(id="91300002", name="投标建设有限公司"),
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

await rag.ainsert_custom_kg(custom_kg)
```

Future SQL loaders should produce these dataclasses from business tables, then
pass them to `TenderKGBuilder`. Keep database-specific extraction outside this
package boundary until the tender schema is stable.

## Loading Real Sources

`DatabaseTenderSource` executes caller-provided SQL. Alias query columns to the
dataclass field names so the adapter does not depend on a fixed business schema.

```python
from lightrag.tender_kg import (
    DatabaseTenderSource,
    DocumentDirectorySource,
    TenderKGImporter,
    TenderKGQueries,
)

database_source = DatabaseTenderSource(
    connection_url="sqlite:////data/tender.db",
    queries=TenderKGQueries(
        projects="SELECT project_id AS id, project_name AS name FROM project",
        organizations="SELECT org_id AS id, org_name AS name FROM organization",
        tender_events="""
            SELECT tender_id AS id,
                   project_id,
                   tenderer_org_id,
                   tender_name AS name
            FROM tender
        """,
        bid_submissions="""
            SELECT bid_id AS id,
                   tender_id,
                   bidder_org_id,
                   document_id,
                   bid_amount AS amount
            FROM bid_submission
        """,
    ),
)

document_source = DocumentDirectorySource("/data/tender-documents")

custom_kg = TenderKGImporter(
    database_source=database_source,
    document_source=document_source,
).build_custom_kg()

await rag.ainsert_custom_kg(custom_kg)
```

Supported database URL schemes in this first adapter slice:

- `sqlite:///path/to/file.db`
- `postgresql://...` with optional `psycopg` installed
- `mysql://...` / `mariadb://...` with optional `pymysql` installed

The document directory loader currently reads real `.txt` and `.md` files. For
PDF, DOCX, and scanned documents, run them through LightRAG's existing document
pipeline or add a parser-specific source that produces `TenderDocument` records.
