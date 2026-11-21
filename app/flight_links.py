"""Build customer-facing fare comparison links from validated trip details."""
import base64
from urllib.parse import urlencode

from .flight_details import route_airport_code


GOOGLE_CABINS = {"ECONOMY": 1, "PREMIUM_ECONOMY": 2, "BUSINESS": 3, "FIRST": 4}


def _flight_leg(date, origin, destination):
    # Google Flights search URLs encode each leg as a date and two IATA airports.
    airport = b"\x07\x08\x01\x12\x03"
    payload = (
        b"\x12\x0a" + date.encode("ascii")
        + b"\x6a" + airport + origin.encode("ascii")
        + b"\x72" + airport + destination.encode("ascii")
    )
    return b"\x1a" + bytes([len(payload)]) + payload


def fare_search_url(draft):
    if not all(draft.get(key) for key in (
        "origin_location", "destination_location", "departure", "return_date", "cabin", "travellers",
    )):
        return None
    origin, destination = draft["origin_location"], draft["destination_location"]
    if (origin["city"].casefold(), origin.get("country")) == (
        destination["city"].casefold(), destination.get("country"),
    ):
        return None
    departure, returning = draft["departure"], draft["return_date"]
    origin_code, destination_code = route_airport_code(origin), route_airport_code(destination)
    cabin = draft["cabin"]
    adults = draft["travellers"]
    if not origin_code or not destination_code:
        cabin_label = cabin.replace("_", " ").lower()
        query = (f"{origin['name']} to {destination['name']} round-trip flights "
                 f"{departure} to {returning} {cabin_label} {adults} adults SGD")
        return "https://www.google.com/search?" + urlencode({"q": query})

    search = (
        b"\x08\x1c\x10\x02"  # Round-trip flight search.
        + _flight_leg(departure, origin_code, destination_code)
        + _flight_leg(returning, destination_code, origin_code)
        + b"\x40\x01" * adults  # One adult passenger per field.
        + b"\x48" + bytes([GOOGLE_CABINS[cabin]])
        + b"\x70\x01\x82\x01\x0b\x08" + b"\xff" * 9 + b"\x01\x98\x01\x01"
    )
    encoded = base64.urlsafe_b64encode(search).decode("ascii").rstrip("=")
    return "https://www.google.com/travel/flights/search?" + urlencode({"tfs": encoded, "curr": "SGD"})
