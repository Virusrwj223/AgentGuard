"""Conversational intake for a locally priced, simulated travel reservation."""
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from dateutil.parser import parse as parse_date
from pydantic import BaseModel, ConfigDict, Field

from .contracts import SeatPreference

FIELDS = ("destination", "origin", "departure", "return_date", "travellers", "lead_name", "cabin")
QUESTIONS = {
    "origin": "Which city are you leaving from?",
    "destination": "Where would you like to go?",
    "departure": "What date would you like to leave? You can say “12 March 2027”.",
    "return_date": "When would you like to return? A date or a number of nights works.",
    "travellers": "How many travellers should I include?",
    "lead_name": "What name should I put on the demo reservation?",
    "cabin": "Which cabin would you like: economy, premium economy, business or first class?",
}
WORD_NUM = dict(one=1, two=2, three=3, four=4, five=5, six=6, seven=7, eight=8,
                nine=9, ten=10, eleven=11, twelve=12, thirteen=13, fourteen=14)
NUM = r"(?:\d{1,4}|" + "|".join(WORD_NUM) + r")"
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
RELATIVE_OFFSET = r"(?:a|an|" + NUM + r")\s+(?:days?|weeks?)"
RELATIVE_DATE = r"(?:in\s+" + RELATIVE_OFFSET + r"|" + RELATIVE_OFFSET + r"\s+from\s+(?:today|now))"
CALENDAR_DATE = (
    r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}(?:st|nd|rd|th)?\s+" + MONTH
    + r"(?:\s*,?\s*\d{4})?|" + MONTH
    + r"\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?|day after tomorrow|tomorrow|today"
)
DATE_RE = re.compile(r"\b(?:" + RELATIVE_DATE + r"|" + CALENDAR_DATE + r")\b", re.I)
PLACE_FIELD = Annotated[str, Field(min_length=1, max_length=160)]
SEAT_PREFERENCES = {"WINDOW": "Window", "AISLE": "Aisle", "MIDDLE": "Middle",
                    "TOGETHER": "Sit together", "ANY": "No preference"}
NAME_FIELD = Annotated[str, Field(min_length=1, max_length=80)]


class ExtractedFields(BaseModel):
    """Untrusted intake output. Dates and the completed record are validated locally."""
    model_config = ConfigDict(extra="forbid", strict=True)
    origin: PLACE_FIELD | None = None
    destination: PLACE_FIELD | None = None
    departure: str | None = Field(default=None, max_length=32)
    return_date: str | None = Field(default=None, max_length=32)
    travellers: int | None = Field(default=None, ge=1, le=12)
    lead_name: NAME_FIELD | None = None
    cabin: str | None = None
    nights: int | None = Field(default=None, ge=1, le=90)
    seat_preference: SeatPreference | None = None
    negotiation_text: str | None = Field(default=None,min_length=3,max_length=1800)


def number(text):
    value = text.lower()
    return int(value) if value.isdecimal() else WORD_NUM.get(value)


def money(cents):
    return f"SGD {Decimal(cents) / 100:,.2f}"


def human_date(value):
    day = date.fromisoformat(value)
    return f"{day.day} {day:%b %Y}"


def invite_dates(today=None):
    today = today or date.today()
    return (today + timedelta(days=30)).isoformat(), (today + timedelta(days=34)).isoformat()


def civil_date(text, reference, *, after=None):
    clean = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", text.lower().strip())
    if re.fullmatch(RELATIVE_DATE, clean):
        count, unit, *_ = clean.removeprefix("in ").split()
        days = (1 if count in {"a", "an"} else number(count)) * (7 if unit.startswith("week") else 1)
        return reference + timedelta(days=days)
    relative = {"today": 0, "tomorrow": 1, "day after tomorrow": 2}
    if clean in relative:
        return reference + timedelta(days=relative[clean])
    # Explicitly disallow ambiguous numeric local formats.
    if "/" in clean or not DATE_RE.fullmatch(text.strip()):
        raise ValueError("Use a date such as 12 March 2027 or 2027-03-12.")
    from datetime import datetime
    d = parse_date(clean, default=datetime(reference.year, 1, 1)).date()
    if not re.search(r"\d{4}", clean):
        anchor = after or reference
        d = d.replace(year=anchor.year)
        if d < anchor:
            d = d.replace(year=d.year + 1)
    return d


def inventory_slots(departure):
    start = date.fromisoformat(departure)
    return {str(i): (start + timedelta(days=i)).isoformat() for i in range(-90, 91)
            if i and start + timedelta(days=i) > date.today()}


def missing(draft):
    return [key for key in FIELDS if not draft.get(key)]


def total(draft):
    if "fare" in draft:
        return draft["fare"]["amount_cents"]
    return 0


def valid_label(value):
    return (1 < len(value) <= 80 and any(c.isalpha() for c in value)
            and all(c.isalpha() or c in " .,’'-" for c in value))


def valid_place(value):
    return len(value)<=160 and all(valid_label(part.strip()) for part in value.split(','))


def label(text):
    return re.sub(r"\s+", " ", text).strip(" ,.;:!?–—\n")


STOP = (r"\s+(?:(?:to|from|on|for|with|going|flying|heading|travelling|traveling|depart(?:ing|ure)?|leav(?:e|ing)|arriv(?:e|ing|al)|return(?:ing)?|back|"
        r"travell?ers?|guests?|passengers?|lead|under|named|my name|please|and|but|can|could|"
        r"want|trip|next|today|tomorrow|window seat|aisle seat|middle seat|"
        r"(?:in\s+)?(?:premium economy|economy|business(?: class)?|first class)|" + MONTH + r")\b|\d)|[,;.!?\n→]|$")
PLACE_VALUE = r"[^\d;:!?\n]{1,80}?"
PLACE = "(" + PLACE_VALUE + ")(?=" + STOP + ")"


def location(text, prefix):
    for match in re.finditer(r"(?=(?:" + prefix + r")\s+" + PLACE + r")", text, re.I):
        part = label(match[1])
        if valid_label(part) and not re.match(r"^(?:another|under|book|travel|go|leave|fly|plan|visit|take|move|change|get|return|know|make|my)\b", part, re.I):
            # Keep supplied country/region qualifiers, but stop before another field.
            tail=text[match.end(1):]
            for _ in range(2):
                qualifier=re.match(r"\s*,\s*"+PLACE,tail,re.I)
                if not qualifier:break
                value=label(qualifier[1])
                if not valid_label(value) or re.match(r'^(?:i|going|flying|heading|travelling|traveling|leaving|arriving|visiting|for|from|to|with|and|but|can|could|would)\b',value,re.I) or re.search(r"\b(?:destination|origin|depart\w*|return\w*|under|traveller\w*|people|cabin|class|name|economy|business|first|please|book)\b",value,re.I):break
                part+=', '+value
                tail=tail[qualifier.end(1):]
            return part
    return None


def route_fields(text):
    # A generic “to” can introduce a date, name or seat edit. Take it as a
    # destination only with a travel cue or as part of a standalone city pair.
    named_origin = r"\borigin(?:\s*(?:is|:|=))?(?:\s+to)?"
    named_destination = r"\bdestination(?:\s*(?:is|:|=))?(?:\s+to)?"
    origin = location(text, named_origin + r"|\bfrom|\bleaving out of")
    destination = location(
        text, named_destination + r"|^to|\b(?:go|going|fly|flying|travel|travelling|traveling|head|heading)\s+to"
        r"|\b(?:visiting|visit|arriv(?:e|ing|al)\s+(?:in|at))|\b(?:trip|itinerary|flight|ticket|holiday|vacation)\s+to"
        r"|(?:\bfrom|" + named_origin + r")\s+" + PLACE_VALUE + r"\s+(?:to|→)"
    )
    pair = re.match(r"^([\w .’'-]{2,65}?)\s+(?:to|→)\s+", text, re.I)
    if pair and valid_label(label(pair[1])) and not re.search(
        r"\b(?:i|we|want|plan|trip|fly|flight|book|going|travel|go|move|from|origin|destination|"
        r"change|correct|switch|set|update|amend|adjust|seat|date|name|cabin|class|discount|please|thanks)\b",
        pair[1], re.I,
    ):
        destination = destination or location(text, re.escape(pair[0].rstrip()))
        if destination:
            origin = origin or label(pair[1])
    return {field: value for field, value in (("origin", origin), ("destination", destination)) if value}


DEPARTURE_WORDS = r"(?:depart(?:ing|ure)?|leav(?:e|ing)|start date)"
RETURN_WORDS = r"(?:return(?:ing)?|back|until|through|end date)"


def date_intent(message, match, expecting, *, allow_trailing):
    before = message[:match.start()]
    after = message[match.end():]
    cues = (("return_date", RETURN_WORDS), ("departure", DEPARTURE_WORDS))
    for key, cue in cues:
        leading = re.search(r"\b" + cue + r"(?:\s+(?:on|date|is|to|around))*[\s:=]*$", before, re.I)
        if leading:
            return key
    for key, cue in cues if allow_trailing else ():
        trailing = re.match(r"\s*,?\s*(?:i |we )?(?:will |'ll )?" + cue + r"\b", after, re.I)
        if trailing:
            return key
    return "return_date" if expecting == "return_date" else "departure"


def seat_preference(text):
    if re.search(r"\b(?:(?:sit|seats?)\s+together|side by side)\b", text, re.I):
        return "TOGETHER"
    match = re.search(r"\b(window|aisle|middle)\s+seats?\b", text, re.I)
    if not match:
        match = re.search(
            r"\b(?:seat preference\s+(?:to|is)|prefer|make (?:it|that))\s+(?:a |the )?(window|aisle|middle)\b",
            text, re.I,
        )
    if not match:
        match = re.fullmatch(r"(window|aisle|middle)(?: please)?[.!]?", text.strip(), re.I)
    if match:
        return match[1].upper()
    if re.search(r"\b(?:any seat|no seat preference|no preference for seats)\b", text, re.I):
        return "ANY"
    return None


def patch_from_text(text, current, expecting, today):
    fields, errors = {}, []
    raw = text.strip()
    if re.fullmatch(r"(?:book|plan|start|create)(?:\s+(?:a|an|another|new))*\s+(?:trip|itinerary|booking)[.!]?",raw,re.I):
        return {},[]
    if preference := seat_preference(raw):
        fields["seat_preference"] = preference
    for cabin in ("premium economy", "business", "first", "economy"):
        suffix = r"\s+class" if cabin == "first" and expecting != "cabin" and raw.casefold().strip(".!") != "first" else r"(?: class)?"
        if re.search(r"\b" + cabin + suffix + r"\b", raw, re.I):
            fields["cabin"] = cabin.upper().replace(" ","_")
            break
    # A named field may itself contain spaces, hyphens, apostrophes or non-Latin letters.
    place_text = re.sub(r"\b" + RELATIVE_DATE + r"\b", " on ", raw, flags=re.I)
    fields.update(route_fields(place_text))
    if "destination" not in fields:
        city = location(place_text, r"\b(?:book|visit|explore)(?: me)?")
        if city and not re.search(r"\b(?:trip|flight|booking|hotel|it|me|for)\b", city,re.I):
            fields["destination"] = city
    name = re.search(r"\b(?:my name is|name(?:\s+(?:is|to)|\s*[:=])|under(?: the name(?: of)?)?|lead (?:travell?er|guest)(?: is)?|call me|for the attention of)\s+(.+?)(?=\s+(?:and|from|to|departing|for|with|on|in\s+(?:economy|business|first|premium))\b|[,;.!?\n]|$)", raw, re.I)
    if name and valid_label(label(name[1])):
        fields["lead_name"] = label(name[1])
        if fields.get("destination") == fields["lead_name"]:
            fields.pop("destination")

    counts = list(re.finditer(r"\b(" + NUM + r")\s+(?:travell?ers?|people|persons?|passengers?|guests?|pax|adults?|kids?|children|child|friends?)\b", raw, re.I))
    if counts:
        fields["travellers"] = sum(number(m[1]) for m in counts)
    elif expecting == "travellers" and re.fullmatch(NUM, raw, re.I):
        fields["travellers"] = number(raw)
    elif re.search(r"\b(?:solo|just me|by myself|on my own)\b", raw, re.I):
        fields["travellers"] = 1
    nights = re.search(r"\b(" + NUM + r")\s*nights?\b", raw, re.I)
    if not nights:
        nights = re.search(r"\bfor (" + NUM + r") days?(?: of travel)?\b", raw, re.I)
    if nights:
        fields["nights"] = number(nights[1])

    # Expand short human ranges before extracting their endpoints.
    expanded = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:to|until|through|[-–])\s*(\d{1,2})(?:st|nd|rd|th)?\s+(" + MONTH + r")(\s+\d{4})?\b",
                      lambda m: f"{m[1]} {m[3]}{m[4] or ''} to {m[2]} {m[3]}{m[4] or ''}", raw, flags=re.I)
    expanded = re.sub(r"\b(" + MONTH + r")\s+(\d{1,2})\s*(?:to|until|through|[-–])\s*(\d{1,2})(?!\d)(?:,?\s+(\d{4}))?(?![-\d])\b",
                      lambda m: f"{m[1]} {m[2]} {m[4] or ''} to {m[1]} {m[3]} {m[4] or ''}", expanded, flags=re.I)
    found_dates = list(DATE_RE.finditer(expanded))
    if re.search(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", raw):
        errors.append("Please spell out the month or use YYYY-MM-DD so I get the date right.")
    for i, match in enumerate(found_dates[:2]):
        expected = expecting if len(found_dates) == 1 else ("departure" if i == 0 else "return_date")
        key = date_intent(expanded, match, expected, allow_trailing=len(found_dates) == 1)
        anchor = fields.get("departure") or current.get("departure")
        try:
            result = civil_date(match[0], today, after=date.fromisoformat(anchor) if key=="return_date" and anchor else None)
            fields[key] = result.isoformat()
        except (ValueError, OverflowError):
            errors.append("That date isn't on the calendar. Please give me the date again.")
    if expecting in ("origin","destination","lead_name") and not fields and not errors:
        clean = label(raw)
        if (valid_place(clean) if expecting in ('origin','destination') else valid_label(clean)) and len(clean.split()) <= 10 and not re.search(r"\b(?:please|book|trip|travel|plan|help|want|show|why|discount|upgrade|offer|later|tomorrow|confirm|cancel|thanks|hello|hi|actually|can|would|could|yes|no)\b", clean, re.I):
            fields[expecting] = clean
    return fields, errors


def apply_fields(draft, patch, today):
    updated = dict(draft)
    errors = []
    if patch.get("seat_preference") in SEAT_PREFERENCES:
        updated["seat_preference"] = patch["seat_preference"]
    if "cabin" in patch:
        cabin = str(patch["cabin"]).upper().replace(" ","_").replace("_CLASS","")
        if cabin in ("ECONOMY","PREMIUM_ECONOMY","BUSINESS","FIRST"):
            updated["cabin"] = cabin
        else:
            errors.append("Choose economy, premium economy, business or first class.")
    for key in ("origin","destination","lead_name"):
        if key in patch:
            value = label(str(patch[key]))
            if (valid_place(value) if key in ('origin','destination') else valid_label(value)):
                updated[key] = value
            else:
                errors.append(f"Please enter the {'lead traveller name' if key=='lead_name' else key} in words.")
    if updated.get("origin") and updated["origin"].casefold() == updated.get("destination", "").casefold():
        updated.pop("destination")
        errors.append("Please give me a destination different from your starting city.")
    if "travellers" in patch:
        if type(patch["travellers"]) is int and 1 <= patch["travellers"] <= 12:
            updated["travellers"] = patch["travellers"]
        else:
            errors.append("A demo itinerary can include 1 to 12 travellers.")
    for key in ("departure","return_date"):
        if key not in patch:
            continue
        try:
            value = date.fromisoformat(patch[key])
            if value <= today or (value - today).days > 730:
                raise ValueError
            updated[key] = value.isoformat()
        except (ValueError, TypeError):
            errors.append("Choose a future travel date within the next two years.")
    if "nights" in patch:
        if 1 <= patch["nights"] <= 90:
            updated["nights"] = patch["nights"]
            if "return_date" not in patch:
                updated.pop("return_date", None)
        else:
            errors.append("Choose a stay of 1 to 90 nights.")
    if updated.get("departure") and "return_date" not in updated and updated.get("nights"):
        updated["return_date"] = (date.fromisoformat(updated["departure"])+timedelta(days=updated["nights"])).isoformat()
    if updated.get("departure") and updated.get("return_date"):
        duration = (date.fromisoformat(updated["return_date"])-date.fromisoformat(updated["departure"])).days
        if 1 <= duration <= 90:
            updated["nights"] = duration
        else:
            updated.pop("return_date", None)
            updated.pop("nights", None)
            errors.append("The return must be 1 to 90 nights after departure. What return date should I use?")
    return updated, errors


def booking_context(lead):
    return "peak" if lead <= 7 else "normal" if lead <= 90 else "advance"


def create_booking(trip_id, draft, today):
    if missing(draft):
        raise ValueError("Your itinerary still needs details.")
    if not draft.get("fare"):
        raise ValueError("An airline quote is required before confirming.")
    lead = (date.fromisoformat(draft["departure"]) - today).days
    if lead < 1:
        raise ValueError("Please update your departure date before confirming.")
    price = total(draft)
    return {**{key: value for key, value in draft.items() if key != "_awaiting"},
            "booking_id": trip_id, "kind":"booked",
            "package": f"{draft['origin']} to {draft['destination']}", "context": booking_context(lead),
            "as_of": today.isoformat(), "slots": inventory_slots(draft["departure"]), "policy":"binding_hold_v1",
            "version":1, "supplier_version":1, "supplier_mock":"held", "currency":"SGD",
            "upgrade_prices_cents":[0, 5000, 7500], "upgraded":False, "paid_cents":price,
            "list_cents":price, "self_service":True}


def flight_summary(draft, *, confirmed=False):
    fare = draft.get("fare") or {}
    stale = draft.get("flight_details_stale")
    lines = []
    if stale:
        lines.append("The date changed. The airline and seats for the revised dates need a fresh check.")
        fare = {}
    if fare.get("airlines"):
        if fare.get("outbound_airlines_only"):
            label = "Outbound airlines in fare search"
        elif fare.get("estimate"):
            label = "Advertised airline"
        else:
            label = "Operating airline" if fare.get("live") else "Airline in test data"
        lines.append(label + ": " + ", ".join(fare["airlines"]))
    elif draft.get("route_airlines") and not stale:
        routes = [route["name"] + " (" + route["service"] + ")" for route in draft["route_airlines"]]
        lines.append("Published route service: " + " · ".join(routes))
    preference = SEAT_PREFERENCES.get(draft.get("seat_preference"))
    if preference:
        lines.append("Seat preference: " + preference + ".")
    if confirmed:
        lines.append("Seats are unassigned.")
    elif fare.get("seat_maps"):
        for seat_map in fare["seat_maps"]:
            options = ", ".join(seat_map["available"]) or "no matching seats shown"
            lines.append(f"{seat_map['flight']} {seat_map['from']}–{seat_map['to']}: {options}")
        lines.append("Seat-map availability is a snapshot. Seats are unassigned.")
    else:
        lines.append("Seat availability is unverified. You can request a window, aisle or seats together.")
    return "\n".join(lines)


def quote_text(draft):
    fare = draft.get("fare")
    if not fare:
        return "This itinerary needs a current airline quote. Say ‘Refresh my quote’ to search before confirming."
    cabin = draft["cabin"].replace("_", " ").lower()
    itinerary = (
        f"{draft['origin']} to {draft['destination']}, {human_date(draft['departure'])} – "
        f"{human_date(draft['return_date'])}, {draft['travellers']} traveller{'s' if draft['travellers'] != 1 else ''}, {cabin}, under {draft['lead_name']}."
    )
    if fare.get("demo"):
        price = (
            f"Simulated demo fare: {money(fare['per_person_cents'])} per adult, "
            f"{money(total(draft))} for your group. "
            "Use “Compare current fares” to see what airlines are advertising."
        )
    elif fare.get("estimate"):
        price = (
            f"Advertised return-fare estimate: {money(fare['per_person_cents'])} per adult, "
            f"{money(total(draft))} for your group.\n"
            f"Source: {fare['source_title']}\n{fare['source_url']}\n{fare['assumptions']}"
        )
    else:
        source = "live airline quote" if fare.get("live") else "test data"
        price = (
            f"Return flight total including taxes: {money(total(draft))}. Source: Amadeus {source}. "
            "The quote is valid for review for five minutes and is subject to airline availability."
        )
    return "\n\n".join((
        itinerary, price, flight_summary(draft),
        "Reply “Confirm booking” to save a demo reservation. No airline ticket or seat hold is created.",
    ))
