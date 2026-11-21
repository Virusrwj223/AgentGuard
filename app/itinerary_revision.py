"""Resolve an atomic revision against the booked itinerary and obtain its fare."""
import re
import time
from datetime import date, timedelta

from .contracts import Goal, ItineraryFields
from .trip_intake import FIELDS, NUM, apply_fields, number, patch_from_text

FARE_FIELDS = {"origin", "destination", "departure", "return_date", "travellers", "cabin"}
LABELS = {"departure": "Departure", "return_date": "Return", "lead_name": "Lead traveller",
          "seat_preference": "Seat preference", "airline_preference": "Airline preference"}
OFFSET = re.compile(r"\b(" + NUM + r"|a|an)\s+(days?|weeks?)\b", re.I)


def read_revision(message, booking):
    """Return a goal, a clarification, or neither for an existing negotiation intent."""
    text = message.strip()
    fields, errors = patch_from_text(text, booking, None, date.today())
    # A financial phrase such as "take SGD 10 from this fare" contains no origin.
    if re.search(r"\bfrom (?:this|the|my|our) (?:fare|price|total|booking)\b", text, re.I):
        fields.pop("origin", None)
    count = re.search(r"\b(?:travell?ers?|passengers?|party size|adults?)\s+(?:to|is)\s+(" + NUM + r")\b", text, re.I)
    if count:
        fields["travellers"] = number(count[1])
    if re.search(r"\b(?:cabin|class)\s+to\s+first\b", text, re.I):
        fields["cabin"] = "FIRST"
    # Relative shifts use the selected booking, including any accepted amendments.
    # A whole-trip shift moves both dates. A named endpoint moves only that date.
    matches = list(OFFSET.finditer(text))
    for match in matches:
        prefix = text[:match.start()].lower()
        next_words = text[match.end():].lower()
        clause = re.split(r'\band\b|[,;.]', prefix)[-1]
        if re.search(r'\b(?:for|stay(?: to| of)?)\s*$', clause):
            fields["nights"] = number(match[1]) or 1
            if match[2].lower().startswith("week"):
                fields["nights"] *= 7
            continue
        if re.search(r'\bin\s*$', clause) or re.match(r'\s+from (?:today|now)', next_words):
            continue
        if not re.search(r'\b(?:move|push|shift|depart\w*|leav\w*|return\w*|trip|earlier|later|back|delay\w*|postpone\w*)\b', clause + " " + next_words):
            continue
        count = (number(match[1]) or 1) * (7 if match[2].lower().startswith("week") else 1)
        direction = clause + " " + re.split(r'\band\b|[,;.]', next_words)[0]
        if re.search(r'\b(?:earlier|sooner|before|undo)\b', direction):
            count = -count
        elif re.search(r'\bback\b', direction) and not re.search(r'\b(?:later|postpone\w*|delay\w*)\b', direction):
            start = date.fromisoformat(booking["departure" if "return" not in clause else "return_date"])
            return None, f"Do you mean {count} days earlier ({start-timedelta(days=count)}) or later ({start+timedelta(days=count)})?"
        target = ("return_date",) if "return" in clause else ("departure",) if re.search(r'\b(?:depart\w*|leav\w*)', clause) else ("departure","return_date")
        for key in target:
            fields[key] = str(date.fromisoformat(booking[key]) + timedelta(days=count))
    airline = re.search(r'\b(?:airline(?: preference)?\s+(?:to|is)|(?:fly|switch to|change to)\s+(?:with\s+)?)\s*([\w .’\'-]+?)(?=\s+and\b|[,;.!?]|$)', text, re.I)
    if airline and ("airline" in text.casefold() or airline[1].casefold().strip() in
                    {r["name"].casefold() for r in booking.get("route_airlines", [])}):
        fields["airline_preference"] = airline[1].strip()
    if re.search(r"\b(?:any airline|no airline preference|(?:clear|remove) (?:my |the )?airline preference)\b",text,re.I):
        fields["airline_preference"] = "ANY"
    if errors:
        return None, " ".join(errors)
    if not fields:
        return None, None
    updated, errors = apply_fields(booking, fields, date.today())
    if errors:
        return None, " ".join(errors)
    changes = {key: updated[key] for key in (*FIELDS,"seat_preference") if updated.get(key) != booking.get(key) and key in updated}
    if "airline_preference" in fields:
        changes["airline_preference"] = fields["airline_preference"]
    if not changes:
        return None, "Those details already match this reservation."
    if set(changes) == {"seat_preference"}:
        return Goal(type="seat_preference", seat_preference=changes["seat_preference"]), None
    if set(changes) == {"departure","return_date"} and len(matches) == 1:
        days = (date.fromisoformat(changes["departure"]) - date.fromisoformat(booking["departure"])).days
        if str(days) in booking["slots"] and str(date.fromisoformat(booking["return_date"]) + timedelta(days=days)) == changes["return_date"]:
            return Goal(type="amend", days=days), None
    return Goal(type="itinerary", changes=ItineraryFields(**changes)), None


def prepare_revision(booking, goal, travel):
    if not booking.get("self_service"):
        raise ValueError("The original itinerary has insufficient details for a revised fare.")
    patch = goal.changes.model_dump(exclude_none=True)
    updated, errors = apply_fields(booking, patch, date.today())
    if errors:
        raise ValueError(" ".join(errors))
    inventory = bool(FARE_FIELDS.intersection(patch))
    fields = {key: updated[key] for key in patch if key in updated}
    if inventory:
        updated.pop("fare", None)
        updated, errors = travel.enrich(updated)
        if errors or not updated.get("fare"):
            raise ValueError(" ".join(errors) or "A fare for the revised itinerary is currently unavailable.")
        fields.update({key: updated.get(key) for key in (*patch, "origin_location","destination_location",
                      "route_airlines","photo","fare","nights")})
    preference = patch.get("airline_preference", booking.get("airline_preference"))
    if preference == "ANY":
        fields["airline_preference"] = None
    elif preference:
        options = [r["name"] for r in updated.get("route_airlines", [])] + (updated.get("fare") or {}).get("airlines", [])
        match = next((name for name in options if name.casefold() == preference.casefold()), None)
        if not match:
            raise ValueError(f"I couldn’t verify {preference} on the revised route. Please choose a published airline or change the request.")
        fields["airline_preference"] = match
    old_list = booking.get("list_cents", booking["paid_cents"])
    new_list = fields["fare"]["amount_cents"] if inventory else old_list
    new_total = max(0, new_list - (old_list - booking["paid_cents"]))
    credit = goal.discount_cents or 0
    if credit > new_total:
        raise ValueError("The requested credit exceeds the revised fare.")
    new_total -= credit
    if inventory and fields["fare"]["expires"] <= time.time():
        raise ValueError("The revised fare expired. Please request a fresh quote.")
    return dict(fields=fields, changes={key: fields[key] for key in patch}, inventory=inventory,
                credit_cents=credit, prior_credit_cents=booking.get("credit_cents",0),
                dates=[updated["departure"], updated["return_date"]] if inventory else None,
                list_cents=new_list, total_cents=new_total, customer_delta=new_total-booking["paid_cents"],
                supplier_delta=new_list-old_list)


def describe_revision(revision):
    parts = [f"{LABELS.get(key,key.title())}: {str(value or 'No preference').replace('_',' ')}" for key,value in revision["changes"].items()]
    return " · ".join(parts)
