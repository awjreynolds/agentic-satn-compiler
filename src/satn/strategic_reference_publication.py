"""Immutable local provenance for a published strategic Reference replay.

The record is deliberately a content-addressed *publication sibling*, not a
selection or delivery authority.  Its SHA-256 fingerprints are replay and
staleness identities only: they are not signatures, credentials, trust roots,
or claims about a route's safety, feasibility, funding or adoption.
"""

from __future__ import annotations

import json
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satn.content_identity import canonical_json as _canonical_json
from satn.content_identity import content_fingerprint as _fingerprint
from satn.strategic_reference_application import StrategicReferenceApplicationPlan

STRATEGIC_REFERENCE_PUBLICATION_CONTRACT = "satn-strategic-reference-publication/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_object(value: str, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"strategic Reference {label} JSON is invalid") from error
    if not isinstance(parsed, dict) or _canonical_json(parsed) != value:
        raise ValueError(f"strategic Reference {label} JSON is not canonical")
    return parsed


class StrategicReferencePublicationRecord(BaseModel):
    """Exact local content binding for one materialised strategic Reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: Literal["satn-strategic-reference-publication/v1"] = (
        STRATEGIC_REFERENCE_PUBLICATION_CONTRACT
    )
    application_plan_json: str = Field(min_length=2)
    replay_diagnostics_json: str = Field(min_length=2)
    area_definition_sha256: str = Field(pattern=_SHA256.pattern)
    snapshot_manifest_sha256: str = Field(pattern=_SHA256.pattern)
    compilation_input_fingerprint: str = Field(pattern=_SHA256.pattern)
    governed_input_fingerprint: str = Field(pattern=_SHA256.pattern)
    compilation_dependency_manifest_json: str = Field(min_length=2)
    decision_contract: str = Field(min_length=1)
    decision_ledger_input_json: str = Field(min_length=2)
    accepted_decisions_json: str = Field(min_length=2)
    publication_created: Literal[False] = False
    agent_runtime_invoked: Literal[False] = False
    record_fingerprint: str = ""

    @field_validator(
        "application_plan_json",
        "replay_diagnostics_json",
        "compilation_dependency_manifest_json",
        "decision_ledger_input_json",
        "accepted_decisions_json",
    )
    @classmethod
    def canonical_json_field(cls, value: str) -> str:
        _canonical_object(value, "record")
        return value

    @model_validator(mode="after")
    def revalidate_record(self) -> Self:
        plan = StrategicReferenceApplicationPlan.model_validate(
            _canonical_object(self.application_plan_json, "application plan")
        )
        diagnostics = _canonical_object(self.replay_diagnostics_json, "replay diagnostics")
        if plan.publication_created or diagnostics.get("plan_fingerprint") != plan.plan_fingerprint:
            raise ValueError("strategic Reference publication replay diagnostics are stale")
        ledger = _canonical_object(self.decision_ledger_input_json, "decision ledger")
        accepted = _canonical_object(self.accepted_decisions_json, "accepted decisions")
        if not isinstance(ledger.get("responses"), list) or not isinstance(
            accepted.get("responses"), list
        ):
            raise ValueError("strategic Reference publication decision provenance is malformed")
        payload = self.model_dump(mode="json", exclude={"record_fingerprint"})
        expected = _fingerprint(payload)
        if self.record_fingerprint and self.record_fingerprint != expected:
            raise ValueError("strategic Reference publication fingerprint is stale")
        object.__setattr__(self, "record_fingerprint", expected)
        return self

    def publication_payload(self) -> dict[str, object]:
        """Return the deep-revalidated canonical public form."""

        record = StrategicReferencePublicationRecord.model_validate(self.model_dump(mode="python"))
        return {
            "contract": record.contract,
            "application_plan": _canonical_object(record.application_plan_json, "application plan"),
            "replay_diagnostics": _canonical_object(
                record.replay_diagnostics_json, "replay diagnostics"
            ),
            "area_definition_sha256": record.area_definition_sha256,
            "snapshot_manifest_sha256": record.snapshot_manifest_sha256,
            "compilation_input_fingerprint": record.compilation_input_fingerprint,
            "governed_input_fingerprint": record.governed_input_fingerprint,
            "compilation_dependency_manifest": _canonical_object(
                record.compilation_dependency_manifest_json, "dependency manifest"
            ),
            "decision_contract": record.decision_contract,
            "decision_ledger_input": _canonical_object(
                record.decision_ledger_input_json, "decision ledger"
            ),
            "accepted_decisions": _canonical_object(
                record.accepted_decisions_json, "accepted decisions"
            )["responses"],
            "publication_created": record.publication_created,
            "agent_runtime_invoked": record.agent_runtime_invoked,
            "record_fingerprint": record.record_fingerprint,
        }

    @classmethod
    def from_publication_payload(cls, payload: dict[str, object]) -> Self:
        """Round-trip a public payload without accepting non-canonical bytes."""

        return cls(
            contract=payload.get("contract"),
            application_plan_json=_canonical_json(payload.get("application_plan")),
            replay_diagnostics_json=_canonical_json(payload.get("replay_diagnostics")),
            area_definition_sha256=payload.get("area_definition_sha256"),
            snapshot_manifest_sha256=payload.get("snapshot_manifest_sha256"),
            compilation_input_fingerprint=payload.get("compilation_input_fingerprint"),
            governed_input_fingerprint=payload.get("governed_input_fingerprint"),
            compilation_dependency_manifest_json=_canonical_json(
                payload.get("compilation_dependency_manifest")
            ),
            decision_contract=payload.get("decision_contract"),
            decision_ledger_input_json=_canonical_json(payload.get("decision_ledger_input")),
            accepted_decisions_json=_canonical_json(
                {"responses": payload.get("accepted_decisions")}
            ),
            publication_created=payload.get("publication_created"),
            agent_runtime_invoked=payload.get("agent_runtime_invoked"),
            record_fingerprint=payload.get("record_fingerprint", ""),
        )


def build_strategic_reference_publication_record(
    *,
    plan: StrategicReferenceApplicationPlan,
    replay_diagnostics: dict[str, object],
    area_definition_sha256: str,
    snapshot_manifest_sha256: str,
    compilation_input_fingerprint: str,
    governed_input_fingerprint: str,
    compilation_dependency_manifest: dict[str, object],
    decision_contract: str,
    decision_ledger_input: dict[str, object],
    accepted_decisions: list[dict[str, str]],
) -> StrategicReferencePublicationRecord:
    """Create a record only from the fresh compiler-revalidated replay view."""

    validated_plan = StrategicReferenceApplicationPlan.model_validate(
        plan.model_dump(mode="python")
    )
    diagnostics = dict(replay_diagnostics)
    if diagnostics.get("plan_fingerprint") != validated_plan.plan_fingerprint:
        raise ValueError("strategic Reference publication requires exact replay diagnostics")
    return StrategicReferencePublicationRecord(
        application_plan_json=_canonical_json(validated_plan.model_dump(mode="json")),
        replay_diagnostics_json=_canonical_json(diagnostics),
        area_definition_sha256=area_definition_sha256,
        snapshot_manifest_sha256=snapshot_manifest_sha256,
        compilation_input_fingerprint=compilation_input_fingerprint,
        governed_input_fingerprint=governed_input_fingerprint,
        compilation_dependency_manifest_json=_canonical_json(compilation_dependency_manifest),
        decision_contract=decision_contract,
        decision_ledger_input_json=_canonical_json(decision_ledger_input),
        accepted_decisions_json=_canonical_json({"responses": accepted_decisions}),
    )
