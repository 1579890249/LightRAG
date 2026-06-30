"""Build LightRAG ``custom_kg`` payloads from tender-domain records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from .schema import (
    AwardEvent,
    BidSubmission,
    EvaluationEvent,
    Organization,
    Person,
    Project,
    TenderDocument,
    TenderEvent,
)


class TenderKGBuilder:
    """Converts tender event records into LightRAG's custom KG shape."""

    def build(
        self,
        *,
        projects: Iterable[Project] = (),
        organizations: Iterable[Organization] = (),
        people: Iterable[Person] = (),
        tender_events: Iterable[TenderEvent] = (),
        bid_submissions: Iterable[BidSubmission] = (),
        evaluation_events: Iterable[EvaluationEvent] = (),
        award_events: Iterable[AwardEvent] = (),
        documents: Iterable[TenderDocument] = (),
    ) -> dict[str, list[dict[str, Any]]]:
        payload = _Payload()

        project_list = list(projects)
        organization_list = list(organizations)
        person_list = list(people)
        tender_event_list = list(tender_events)
        bid_submission_list = list(bid_submissions)
        evaluation_event_list = list(evaluation_events)
        award_event_list = list(award_events)
        document_list = list(documents)

        for project in project_list:
            key = _key("Project", project.id)
            payload.add_entity(
                key,
                "Project",
                _description(project.name, project.description, project.metadata),
            )

        for organization in organization_list:
            key = _key("Organization", organization.id)
            payload.add_entity(
                key,
                "Organization",
                _description(
                    organization.name,
                    organization.description,
                    organization.metadata,
                    aliases=organization.aliases,
                    kind=organization.organization_type,
                ),
            )

        for person in person_list:
            key = _key("Person", person.id)
            payload.add_entity(
                key,
                "Person",
                _description(
                    person.name,
                    person.description,
                    person.metadata,
                    aliases=person.aliases,
                    kind=person.role,
                ),
            )
            if person.organization_id:
                payload.add_relationship(
                    key,
                    _key("Organization", person.organization_id),
                    "EMPLOYED_BY",
                    f"{key} belongs to organization {person.organization_id}.",
                    key,
                )

        for document in document_list:
            key = _key("Document", document.id)
            payload.add_entity(
                key,
                "Document",
                _description(document.title, document.description, document.metadata),
                source_id=_doc_source(key),
            )
            payload.add_chunk(
                _doc_source(key),
                _document_content(document),
                document.file_path,
            )

        for tender_event in tender_event_list:
            key = _key("TenderEvent", tender_event.id)
            payload.add_entity(
                key,
                "TenderEvent",
                _description(
                    tender_event.name or tender_event.id,
                    tender_event.description,
                    tender_event.metadata,
                ),
            )
            payload.add_relationship(
                key,
                _key("Project", tender_event.project_id),
                "FOR_PROJECT",
                f"{key} is a tender event for project {tender_event.project_id}.",
                key,
            )
            payload.add_relationship(
                key,
                _key("Organization", tender_event.tenderer_org_id),
                "TENDERER",
                f"{tender_event.tenderer_org_id} is the tendering organization for {key}.",
                key,
            )
            if tender_event.agency_org_id:
                payload.add_relationship(
                    key,
                    _key("Organization", tender_event.agency_org_id),
                    "AGENCY",
                    f"{tender_event.agency_org_id} is the tender agency for {key}.",
                    key,
                )

        for bid_submission in bid_submission_list:
            key = _key("BidSubmission", bid_submission.id)
            payload.add_entity(
                key,
                "BidSubmission",
                _bid_description(bid_submission),
            )
            payload.add_relationship(
                key,
                _key("TenderEvent", bid_submission.tender_id),
                "FOR_TENDER",
                f"{key} is submitted for tender {bid_submission.tender_id}.",
                key,
            )
            payload.add_relationship(
                key,
                _key("Organization", bid_submission.bidder_org_id),
                "BIDDER",
                f"{bid_submission.bidder_org_id} is the bidder for {key}.",
                key,
            )
            if bid_submission.document_id:
                document_key = _key("Document", bid_submission.document_id)
                payload.add_relationship(
                    key,
                    document_key,
                    "HAS_DOCUMENT",
                    f"{key} has tender document {bid_submission.document_id}.",
                    _doc_source(document_key),
                )
            for person_id in bid_submission.contact_person_ids:
                payload.add_relationship(
                    _key("Person", person_id),
                    key,
                    "REPRESENTS_BID",
                    f"{person_id} represents bid submission {key}.",
                    key,
                )

        for evaluation_event in evaluation_event_list:
            key = _key("EvaluationEvent", evaluation_event.id)
            payload.add_entity(
                key,
                "EvaluationEvent",
                _description(
                    evaluation_event.id,
                    evaluation_event.description,
                    evaluation_event.metadata,
                ),
            )
            payload.add_relationship(
                key,
                _key("TenderEvent", evaluation_event.tender_id),
                "EVALUATES_TENDER",
                f"{key} evaluates tender {evaluation_event.tender_id}.",
                key,
            )
            for person_id in evaluation_event.reviewer_person_ids:
                payload.add_relationship(
                    key,
                    _key("Person", person_id),
                    "REVIEWER",
                    f"{person_id} is a reviewer in {key}.",
                    key,
                )
            for bid_submission_id in evaluation_event.bid_submission_ids:
                payload.add_relationship(
                    key,
                    _key("BidSubmission", bid_submission_id),
                    "EVALUATES",
                    f"{key} evaluates bid submission {bid_submission_id}.",
                    key,
                )

        for award_event in award_event_list:
            key = _key("AwardEvent", award_event.id)
            payload.add_entity(
                key,
                "AwardEvent",
                _description(award_event.id, award_event.description, award_event.metadata),
            )
            payload.add_relationship(
                key,
                _key("TenderEvent", award_event.tender_id),
                "AWARDS_TENDER",
                f"{key} awards tender {award_event.tender_id}.",
                key,
            )
            payload.add_relationship(
                key,
                _key("Organization", award_event.winner_org_id),
                "WINNER",
                f"{award_event.winner_org_id} is the winner in {key}.",
                key,
            )
            if award_event.bid_submission_id:
                payload.add_relationship(
                    key,
                    _key("BidSubmission", award_event.bid_submission_id),
                    "WINNING_BID",
                    f"{award_event.bid_submission_id} is the winning bid in {key}.",
                    key,
                )

        return payload.to_custom_kg()


class _Payload:
    def __init__(self) -> None:
        self.entities: list[dict[str, Any]] = []
        self.relationships: list[dict[str, Any]] = []
        self.chunks: list[dict[str, Any]] = []
        self._entity_keys: set[str] = set()
        self._chunk_sources: set[str] = set()

    def add_entity(
        self,
        entity_name: str,
        entity_type: str,
        description: str,
        *,
        source_id: str | None = None,
    ) -> None:
        if entity_name in self._entity_keys:
            raise ValueError(f"Duplicate tender KG entity key: {entity_name}")
        self._entity_keys.add(entity_name)
        record_source_id = source_id or _record_source(entity_name)
        self.entities.append(
            {
                "entity_name": entity_name,
                "entity_type": entity_type,
                "description": description,
                "source_id": record_source_id,
            }
        )
        if source_id is None:
            self.add_chunk(record_source_id, f"{entity_name}: {description}", "custom_kg")

    def add_relationship(
        self,
        src_id: str,
        tgt_id: str,
        keywords: str,
        description: str,
        source_entity_name: str,
    ) -> None:
        self.relationships.append(
            {
                "src_id": src_id,
                "tgt_id": tgt_id,
                "description": description,
                "keywords": keywords,
                "weight": 1.0,
                "source_id": _normalize_source(source_entity_name),
                "file_path": "custom_kg",
            }
        )

    def add_chunk(self, source_id: str, content: str, file_path: str) -> None:
        if source_id in self._chunk_sources:
            return
        self._chunk_sources.add(source_id)
        self.chunks.append(
            {
                "content": content,
                "source_id": source_id,
                "chunk_order_index": len(self.chunks),
                "file_path": file_path,
            }
        )

    def to_custom_kg(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "entities": self.entities,
            "relationships": self.relationships,
            "chunks": self.chunks,
        }


def _key(entity_type: str, entity_id: str) -> str:
    if not str(entity_id).strip():
        raise ValueError(f"{entity_type} id cannot be empty")
    return f"{entity_type}:{entity_id}"


def _record_source(entity_name: str) -> str:
    return f"record:{entity_name}"


def _doc_source(document_key: str) -> str:
    return f"doc:{document_key}"


def _normalize_source(source_entity_name: str) -> str:
    if source_entity_name.startswith("doc:") or source_entity_name.startswith("record:"):
        return source_entity_name
    return _record_source(source_entity_name)


def _description(
    name: str,
    description: str,
    metadata: dict[str, Any],
    *,
    aliases: list[str] | None = None,
    kind: str = "",
) -> str:
    parts = [f"name={name}"]
    if kind:
        parts.append(f"type={kind}")
    if aliases:
        parts.append(f"aliases={', '.join(aliases)}")
    if description:
        parts.append(description)
    for key, value in metadata.items():
        if value is not None and value != "":
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def _bid_description(bid_submission: BidSubmission) -> str:
    metadata = dict(bid_submission.metadata)
    if bid_submission.amount:
        metadata["amount"] = bid_submission.amount
    return _description(
        bid_submission.id,
        bid_submission.description,
        metadata,
    )


def _document_content(document: TenderDocument) -> str:
    data = asdict(document)
    metadata = data.pop("metadata", {})
    content = data.pop("content")
    parts = [f"Document:{document.id}", f"title={document.title}", content]
    for key, value in metadata.items():
        if value is not None and value != "":
            parts.append(f"{key}={value}")
    return "\n".join(parts)
