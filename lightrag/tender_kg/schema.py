"""Domain records used to build tender event knowledge graphs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


Metadata = dict[str, Any]


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    description: str = ""
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class Organization:
    id: str
    name: str
    organization_type: str = ""
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    organization_id: str | None = None
    role: str = ""
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class TenderEvent:
    id: str
    project_id: str
    tenderer_org_id: str
    name: str = ""
    agency_org_id: str | None = None
    description: str = ""
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class BidSubmission:
    id: str
    tender_id: str
    bidder_org_id: str
    document_id: str | None = None
    amount: str | None = None
    contact_person_ids: list[str] = field(default_factory=list)
    description: str = ""
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationEvent:
    id: str
    tender_id: str
    reviewer_person_ids: list[str] = field(default_factory=list)
    bid_submission_ids: list[str] = field(default_factory=list)
    description: str = ""
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class AwardEvent:
    id: str
    tender_id: str
    winner_org_id: str
    bid_submission_id: str | None = None
    description: str = ""
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class TenderDocument:
    id: str
    title: str
    content: str
    file_path: str = "custom_kg"
    description: str = ""
    metadata: Metadata = field(default_factory=dict)


@dataclass(frozen=True)
class TenderKGDataset:
    projects: list[Project] = field(default_factory=list)
    organizations: list[Organization] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    tender_events: list[TenderEvent] = field(default_factory=list)
    bid_submissions: list[BidSubmission] = field(default_factory=list)
    evaluation_events: list[EvaluationEvent] = field(default_factory=list)
    award_events: list[AwardEvent] = field(default_factory=list)
    documents: list[TenderDocument] = field(default_factory=list)

    def merge(self, other: "TenderKGDataset") -> "TenderKGDataset":
        return TenderKGDataset(
            projects=[*self.projects, *other.projects],
            organizations=[*self.organizations, *other.organizations],
            people=[*self.people, *other.people],
            tender_events=[*self.tender_events, *other.tender_events],
            bid_submissions=[*self.bid_submissions, *other.bid_submissions],
            evaluation_events=[*self.evaluation_events, *other.evaluation_events],
            award_events=[*self.award_events, *other.award_events],
            documents=[*self.documents, *other.documents],
        )
