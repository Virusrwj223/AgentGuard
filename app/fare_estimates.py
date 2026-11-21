"""Provider-backed fare estimates and local prices for simulated bookings."""
import math
import time

import httpx

from .flight_details import route_airport_code
from .flight_links import GOOGLE_CABINS, fare_search_url


def google_flights_estimate(client, key, draft):
    if not key:
        return None
    origin = route_airport_code(draft["origin_location"])
    destination = route_airport_code(draft["destination_location"])
    if not origin or not destination:
        return None
    cabin = draft["cabin"]
    try:
        response = client.get("https://serpapi.com/search.json", timeout=6, params={
            "engine": "google_flights", "api_key": key, "type": 1,
            "departure_id": origin, "arrival_id": destination,
            "outbound_date": draft["departure"], "return_date": draft["return_date"],
            "travel_class": GOOGLE_CABINS[cabin], "currency": "SGD", "gl": "sg", "hl": "en", "adults": 1,
        })
        response.raise_for_status()
        data = response.json()
        if data.get("error") or data.get("search_parameters", {}).get("currency") != "SGD":
            return None
        expected_cabin = cabin.replace("_", " ").casefold()
        candidates = [row for group in ("best_flights", "other_flights") for row in data.get(group, [])
                      if row.get("type") == "Round trip" and type(row.get("price")) is int and row["price"] > 0
                      and row.get("flights") and all(
                          leg.get("travel_class", "").casefold() == expected_cabin for leg in row["flights"]
                      )]
        if not candidates:
            return None
        offer = min(candidates, key=lambda row: row["price"])
        airlines = list(dict.fromkeys(leg["airline"] for leg in offer["flights"] if leg.get("airline")))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return None

    now = time.time()
    price = offer["price"] * 100
    return {
        "amount_cents": price * draft["travellers"], "per_person_cents": price,
        "expires": now + 1800, "retrieved": now, "live": False, "estimate": True,
        "source": "Google Flights via SerpApi", "source_title": "Google Flights via SerpApi",
        "source_url": fare_search_url(draft), "airlines": airlines, "outbound_airlines_only": True,
        "assumptions": "The displayed return-fare estimate for one adult is multiplied by the group size. "
                       "Airlines shown are for the outbound leg. Check current availability and price before buying.",
    }


def _distance_km(origin, destination):
    try:
        lat1, lon1 = (math.radians(origin[field]) for field in ("latitude", "longitude"))
        lat2, lon2 = (math.radians(destination[field]) for field in ("latitude", "longitude"))
    except (KeyError, TypeError):
        return None
    half_chord = math.hypot(math.sin((lat2 - lat1) / 2),
                            math.sqrt(math.cos(lat1) * math.cos(lat2)) * math.sin((lon2 - lon1) / 2))
    return 2 * 6371 * math.asin(min(1, half_chord))


def simulated_demo_fare(draft):
    """Use fixed per-adult SGD rates by distance band and cabin."""
    distance = _distance_km(draft["origin_location"], draft["destination_location"])
    # Economy, premium economy, business, first. Rates are chosen for the demo.
    if distance is None:
        rates = (1200, 1900, 4500, 8000)
    elif distance <= 2000:
        rates = (250, 450, 850, 1400)
    elif distance <= 6000:
        rates = (550, 850, 1750, 3200)
    else:
        rates = (1400, 2250, 5900, 9900)
    cabins = ("ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST")
    price = rates[cabins.index(draft["cabin"])] * 100
    now = time.time()
    return {
        "amount_cents": price * draft["travellers"], "per_person_cents": price,
        "expires": now + 1800, "retrieved": now, "live": False, "demo": True,
        "source": "Simulated demo fare",
    }
