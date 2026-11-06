"""Versioned runtime state for the bounded planning interview."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

StopReason = Literal["question_budget", "pass_limit", "no_progress"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["supported", "unresolved", "not_applicable"]
    evidence: str = Field(min_length=1)


class Readiness(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Evidence
    users: Evidence
    scope: Evidence
    constraints: Evidence
    acceptance: Evidence


class PlanningQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node: str
    key: str = Field(min_length=1)
    question: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    tradeoff: str = Field(min_length=1)
    reopen_reason: str | None = Field(default=None, min_length=1)


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    criteria: Readiness
    blockers: list[str]
    unknowns: list[str]
    settled: list[str]
    questions: list[PlanningQuestion] = Field(max_length=3)

    @property
    def ready(self) -> bool:
        return (
            all(
                item.status != "unresolved"
                for item in (
                    self.criteria.outcome,
                    self.criteria.users,
                    self.criteria.scope,
                    self.criteria.constraints,
                    self.criteria.acceptance,
                )
            )
            and not self.blockers
            and not self.questions
        )


class Decision(BaseModel):
    question: PlanningQuestion
    answer: str
    deferred: bool = False
    resolved: bool = False


class PlanningState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    question_limit: int = Field(ge=0)
    answered: int = Field(default=0, ge=0)
    phase: Literal[
        "assessing", "validating", "asking", "revising", "confirming", "correcting", "approved"
    ] = "assessing"
    assessment: Assessment | None = None
    batch: list[PlanningQuestion] = Field(default_factory=list, max_length=3)
    pending_validations: list[str] = Field(default_factory=list)
    answer: str | None = None
    history: list[Decision] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    no_progress: int = Field(default=0, ge=0)
    progress: set[str] = Field(default_factory=set)
    round_progress: bool = False
    completed_rounds: int = Field(default=0, ge=0)
    stop_reason: StopReason | None = None
    stop_detail: str = ""
    approved_hash: str | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.answered > self.question_limit or self.answered != (
            len(self.history) + (self.phase == "revising")
        ):
            raise ValueError("Interview question accounting does not match saved answers")
        if (self.answer is not None) != (self.phase in {"revising", "correcting"}):
            raise ValueError("Saved interview input does not match its phase")
        if self.phase == "revising" and not self.batch:
            raise ValueError("Saved interview revision has no question")
        if self.phase == "validating" and self.assessment is None:
            raise ValueError("Saved validation has no assessment")
        if self.pending_validations and self.phase != "validating":
            raise ValueError("Pending validations do not match the interview phase")
        if self.phase in {"confirming", "approved"} and (
            self.assessment is None or not self.assessment.ready or self.batch
        ):
            raise ValueError("Saved approval lacks a ready assessment")
        if (self.approved_hash is not None) != (self.phase == "approved"):
            raise ValueError("Saved approval does not match its phase")
        return self

    @property
    def remaining(self) -> int:
        return self.question_limit - self.answered

    @property
    def repeated_question(self) -> bool:
        return any(
            (
                (question.node == old.question.node and question.key == old.question.key)
                or question.question.casefold() == old.question.question.casefold()
            )
            and (not question.reopen_reason or question.reopen_reason == old.question.reopen_reason)
            for question in self.batch
            for old in self.history
        )

    @property
    def no_progress_detail(self) -> str:
        """Explain saved pauses independently of legacy stop-detail wording."""
        if self.no_progress >= 2:
            return (
                "Two consecutive completed questioning rounds made no material progress: "
                "no new decision was settled or planning topic discovered."
            )
        if not self.batch:
            if self.assessment is None:
                return "No next question or readiness assessment is saved."
            return "The plan is not ready, but the assessment supplied no next question."
        if self.repeated_question:
            return "A previously answered question was repeated without an explained new gap."
        return (
            "The exact cause of this saved progress pause is not recorded. "
            "Review the saved details."
        )
