"""The same closed contracts drive the API, the agent catalog and export files."""
from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

Id = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")]
Cents = Annotated[int, Field(ge=0, le=100_000_000)]
Days = Annotated[int, Field(ge=1, le=90)]

class Keep(Closed):
    op: Literal["KEEP_BOOKING"]
class Request(Closed):
    op: Literal["REQUEST_AMENDMENT", "GUARANTEE_AMENDMENT"]
    days: Days
class Confirm(Closed):
    op: Literal["CONFIRM_AMENDMENT"]
class Price(Closed):
    op: Literal["ADJUST_PRICE"]
    discount_cents: Cents
class Upgrade(Closed):
    op: Literal["UPGRADE"]
    charge_cents: Cents
class Sequence(Closed):
    op: Literal["SEQUENCE"]
    steps: list["Plan"] = Field(min_length=1, max_length=8)
class Conditional(Closed):
    op: Literal["IF"]
    predicate: Literal["AMENDMENT_APPROVED"]
    then: "Plan"
    otherwise: "Plan"

Plan = Annotated[Union[Keep, Request, Confirm, Price, Upgrade, Sequence, Conditional], Field(discriminator="op")]
Sequence.model_rebuild()
Conditional.model_rebuild()

class Goal(Closed):
    type: Literal["amend","sale","upgrade","amend_upgrade","unknown"]
    days: Days | None = None
    discount_cents: Cents | None = None
    max_extra_charge_cents: Cents = 0

class Strategy(Closed):
    strategy_id: Id
    plan: Plan

class Proposal(Closed):
    request_id: Id
    booking_id: Id
    goal: Goal
    needs_clarification: bool
    candidate_strategies: list[Strategy] = Field(max_length=5)

    @model_validator(mode="after")
    def bounded(self):
        ids = [x.strategy_id for x in self.candidate_strategies]
        if len(set(ids)) != len(ids) or "baseline" in ids:
            raise ValueError("Reserved or repeated strategy ID")
        if self.needs_clarification or self.goal.type == "unknown":
            return self
        if self.goal.type in ("amend","amend_upgrade") and self.goal.days is None:
            raise ValueError("Missing amendment interval")
        if self.goal.type == "sale" and self.goal.discount_cents is None:
            raise ValueError("Missing discount")
        if not ids:
            raise ValueError("A supported request needs at least one strategy")
        def count(p, depth):
            if depth > 5:
                raise ValueError("Plan nesting exceeds limit")
            children = p.steps if isinstance(p,Sequence) else [p.then,p.otherwise] if isinstance(p,Conditional) else []
            return 1 + sum(count(c,depth+1) for c in children)
        if any(count(x.plan,1)>20 for x in self.candidate_strategies):
            raise ValueError("Plan length exceeds limit")
        return self

class Start(Closed):
    fixture: Literal["B001","B002","B003"] = "B001"
class Inquiry(Closed):
    message: str = Field(min_length=3,max_length=1800)
class Consent(Closed):
    decision_id: Id
    terms_digest: str = Field(pattern="^[0-9a-f]{64}$")
    consent: Literal[True]
class Draft(Closed):
    record_id: Id
    value: int | None = Field(ge=0, le=100_000_000)
    source: str = Field(min_length=3,max_length=320)
    evidence: str = Field(min_length=3,max_length=800)
    limitations: str = Field(min_length=3,max_length=400)
    context: str = Field(min_length=2,max_length=200)
    sample_size: int | None = Field(ge=1,le=10000000)
    evidence_status: Literal["synthetic","estimate","observed","unknown"]
class Profile(Closed):
    loss_multiplier: int = Field(ge=1,le=10)
    minimum_gain_cents: Cents
    floor_cents: Annotated[int,Field(ge=-100_000_000, le=100_000_000)] | None
class Gap(Closed):
    operation: Id
    severity: Literal["material","minor"]
    finding: str = Field(min_length=3,max_length=500)
class Summary(Closed):
    event_id: int = Field(ge=1)
    field: Literal["status","strategy","reason","oracle","scenario","model_hash"]
    value: str = Field(max_length=300)

class CustomerResult(Closed):
    decision_id: Id
    request_id: Id
    mode: Literal["Exact preview","PAT"]
    status: Literal["APPROVED","COUNTEROFFER","REJECTED","CLARIFY","NEEDS_REVIEW","ERROR"]
    message: str
    terms: dict | None = None
    terms_digest: str | None = None
