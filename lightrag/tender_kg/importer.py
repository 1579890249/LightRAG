"""Orchestration for loading tender sources and building LightRAG custom KG."""

from __future__ import annotations

from .builder import TenderKGBuilder
from .schema import TenderKGDataset
from .sources import DatabaseTenderSource, DocumentDirectorySource


class TenderKGImporter:
    def __init__(
        self,
        *,
        database_source: DatabaseTenderSource | None = None,
        document_source: DocumentDirectorySource | None = None,
        builder: TenderKGBuilder | None = None,
    ) -> None:
        self.database_source = database_source
        self.document_source = document_source
        self.builder = builder or TenderKGBuilder()

    def load_dataset(self) -> TenderKGDataset:
        dataset = TenderKGDataset()
        if self.database_source is not None:
            dataset = dataset.merge(self.database_source.load())
        if self.document_source is not None:
            dataset = dataset.merge(self.document_source.load())
        return dataset

    def build_custom_kg(self) -> dict:
        dataset = self.load_dataset()
        return self.builder.build(
            projects=dataset.projects,
            organizations=dataset.organizations,
            people=dataset.people,
            tender_events=dataset.tender_events,
            bid_submissions=dataset.bid_submissions,
            evaluation_events=dataset.evaluation_events,
            award_events=dataset.award_events,
            documents=dataset.documents,
        )
