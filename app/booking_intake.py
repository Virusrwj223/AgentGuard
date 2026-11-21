"""Interpret a traveller turn once, then validate and enrich the resulting draft."""
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from .sales_desk import negotiation_text
from .trip_intake import (
    DATE_RE, FIELDS, QUESTIONS, ExtractedFields, apply_fields, missing, patch_from_text,
)

PLACE_FIELDS = ("origin", "destination")


@dataclass
class IntakeResult:
    draft: dict[str, Any]
    errors: list[str]
    negotiation: str | None
    awaiting: str | None


def next_field(draft):
    for field in PLACE_FIELDS:
        if draft.get(field) and not draft.get(field + "_location"):
            return field
    return next(iter(missing(draft)), None)


def split_negotiation(text, suggested=None):
    """Keep draft edits separate from requests to change the eventual booking."""
    masked = re.sub(r"\bbusiness class\b", lambda match: " " * len(match[0]), text, flags=re.I)
    negotiation = negotiation_text(masked)
    if not negotiation and suggested and suggested in text and "business class" not in suggested.casefold():
        negotiation = suggested
    if re.search(r"\b(?:change|correct|update) (?:my |the |our )?(?:draft |return date|departure date|name|number of travellers)", text, re.I):
        if not re.search(r"discount|off|upgrade|extra (?:cost|charge)|fee", text, re.I):
            negotiation = None
    if not negotiation:
        return text, None
    if text.endswith(negotiation) and negotiation != text:
        return text[:-len(negotiation)].rstrip(" ,;"), negotiation
    if not re.search(r"\b(?:from|book a trip|book an itinerary|plan a trip|travell?ers?|people|pax|nights?)\b", text, re.I):
        return "", negotiation
    return text, negotiation


class BookingIntake:
    def __init__(self, planners, travel):
        self.planners = planners
        self.travel = travel

    def interpret(self, text: str, draft: dict, today: date) -> IntakeResult:
        awaiting = draft.get("_awaiting") or next_field(draft)
        native, errors = patch_from_text(text, draft, awaiting, today)
        if self.is_route_only(text, native):
            return self.apply(draft, native, errors, None, today)

        model_fields = self.extract_from_agent(text, draft, awaiting, today)
        intake_text, negotiation = split_negotiation(text, model_fields.pop("negotiation_text", None))
        if intake_text != text:
            native, errors = patch_from_text(intake_text, draft, awaiting, today)
        model_fields = self.ground_model_fields(model_fields, intake_text, native, draft)
        return self.apply(draft, model_fields | native, errors, negotiation, today)

    @staticmethod
    def is_route_only(text, fields):
        if fields.keys() != set(PLACE_FIELDS):
            return False
        origin, destination = (re.escape(fields[field]) for field in PLACE_FIELDS)
        return bool(re.fullmatch(r"(?:from\s+)?" + origin + r"\s+(?:to|→)\s+" + destination + r"[.!]?", text, re.I))

    def refresh(self, draft):
        enriched, errors = self.travel.enrich({key: value for key, value in draft.items() if key != "fare"})
        return IntakeResult(enriched, errors, None, next_field(enriched))

    def apply(self, draft, fields, errors, negotiation, today):
        proposed, validation_errors = apply_fields(draft, fields, today)
        enriched, travel_errors = self.travel.enrich(proposed)
        awaiting = next_field(enriched)
        return IntakeResult(enriched, errors + validation_errors + travel_errors, negotiation, awaiting)

    def extract_from_agent(self, text, draft, awaiting, today):
        payload = {
            "message": text,
            "draft": {field: draft[field] for field in FIELDS if field in draft},
            "awaiting": awaiting,
            "question": QUESTIONS.get(awaiting),
            "today": today.isoformat(),
            "fields": [*FIELDS, "nights", "seat_preference"],
        }
        for completion in self.planners.answers("intake", payload):
            try:
                return ExtractedFields.model_validate(completion.answer).model_dump(exclude_none=True)
            except ValidationError:
                continue
        return {}

    @staticmethod
    def ground_model_fields(fields, text, native, draft):
        if not text:
            return {}
        words = " " + " ".join(re.findall(r"\w+", text.casefold())) + " "
        result = dict(fields)
        for field in (*PLACE_FIELDS, "lead_name"):
            if field not in result:
                continue
            value = " " + " ".join(re.findall(r"\w+", result[field].casefold())) + " "
            if value not in words:
                result.pop(field)
        # A single bare answer and a locally recognized date or name each have
        # one role. Keep an agent from also treating them as another field.
        if native.get("lead_name"):
            for field in PLACE_FIELDS:
                if result.get(field, "").casefold() == native["lead_name"].casefold():
                    result.pop(field)
        for field in PLACE_FIELDS:
            other = "destination" if field == "origin" else "origin"
            if field not in native and (draft.get(field + "_location") or native.keys() - set(PLACE_FIELDS)):
                result.pop(field, None)
            if other in native and result.get(field, "").casefold() == native[other].casefold():
                result.pop(field, None)
        date_fields = {"departure", "return_date"} & native.keys()
        if date_fields:
            remainder = DATE_RE.sub(" ", text)
            if re.fullmatch(r"[\s,.!?]*(?:(?:i|we|will|leave|leaving|depart|departure|return|back|on|please)\b[\s,.!?]*)*", remainder, re.I):
                return {field: value for field, value in result.items() if field in date_fields}
            for field in {"departure", "return_date"} - date_fields:
                if result.get(field) in {native[date_field] for date_field in date_fields}:
                    result.pop(field)
        return result
