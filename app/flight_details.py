"""Source-backed route and seat information shared by the travel providers."""
import re
from urllib.parse import urlsplit

SINGAPORE_AIRLINES_NYC = (
    "https://www.singaporeair.com/sg/en/plan-travel/destinations/flights-from-singapore-to-new-york/"
)
CATHAY_NYC = "https://flights.cathaypacific.com/destinations/en_SG/flights-from-singapore-to-new-york"
MONTREAL_AIRLINES = (
    ("SWISS", "Via Zürich", "https://www.swiss.com/lhg/sg/en/o-d/cy-cy/singapore-montreal"),
    ("Qatar Airways", "Via Doha", "https://www.qatarairways.com/en/destinations/flights-to-montreal/from-singapore.html"),
    ("Air Canada", "Connecting service", "https://www.aircanada.com/en-sg/flights-from-singapore-to-montreal"),
)
NYC_PHOTO = {
    "url": "https://upload.wikimedia.org/wikipedia/commons/e/e9/LowerManhattanSkyline_1WTC.jpg",
    "title": "Lower Manhattan skyline",
    "credit_url": "https://commons.wikimedia.org/wiki/File:LowerManhattanSkyline_1WTC.jpg",
    "credit": "Irina S.B. · CC BY-SA 4.0",
}

# Exact city and airport names for the two cities in the main demo. Other
# locations go through the configured place provider.
SINGAPORE = {
    "name": "Singapore", "city": "Singapore", "country": "SG", "iata": "SIN",
    "latitude": 1.29, "longitude": 103.85, "source": SINGAPORE_AIRLINES_NYC,
}
NEW_YORK = {
    "name": "New York City", "city": "New York City", "country": "US", "iata": "NYC",
    "latitude": 40.71, "longitude": -74.01, "source": SINGAPORE_AIRLINES_NYC,
}
KNOWN_PLACES = {
    "singapore": SINGAPORE,
    "singapore, singapore": SINGAPORE,
    "sin": SINGAPORE | {"name": "Singapore Changi Airport"},
    "changi airport": SINGAPORE | {"name": "Singapore Changi Airport"},
    "singapore changi airport": SINGAPORE | {"name": "Singapore Changi Airport"},
    "new york": NEW_YORK,
    "new york city": NEW_YORK,
    "new york city, united states": NEW_YORK,
    "new york city, usa": NEW_YORK,
    "new york, united states": NEW_YORK,
    "new york, usa": NEW_YORK,
    "nyc": NEW_YORK,
    "jfk": NEW_YORK | {"name": "John F. Kennedy International Airport", "iata": "JFK"},
    "ewr": NEW_YORK | {"name": "Newark Liberty International Airport", "iata": "EWR"},
}


def known_place(value):
    place = KNOWN_PLACES.get(" ".join(value.casefold().split()))
    return dict(place) if place else None


def known_photo(place):
    if place.get("country") == "US" and place.get("city", "").casefold() in {"new york", "new york city"}:
        return dict(NYC_PHOTO)
    return None


def route_airport_code(place):
    code = place.get("iata") or ""
    if re.fullmatch(r"[A-Z]{3}", code):
        return code
    if place.get("country") == "CA" and place["city"].casefold() in {"montreal", "montréal"}:
        return "YUL"
    return None


def route_airlines(origin, destination, cabin):
    """Return published service examples, scoped to supported endpoint airports."""
    if route_airport_code(origin) == "SIN" and route_airport_code(destination) == "YUL":
        return [
            {"name": name, "service": service, "source_url": url}
            for name, service, url in MONTREAL_AIRLINES
        ]
    places = (origin, destination)
    singapore = next((place for place in places if place.get("iata") == "SIN"), None)
    new_york = next((place for place in places if place.get("iata") in {"NYC", "JFK", "EWR"}), None)
    if not singapore or not new_york:
        return []
    airlines = []
    airport = new_york["iata"]
    if cabin in (None, "PREMIUM_ECONOMY", "BUSINESS"):
        airports = "JFK / EWR" if airport == "NYC" else airport
        airlines.append({
            "name": "Singapore Airlines", "service": f"Nonstop to {airports} · Premium economy and business",
            "source_url": SINGAPORE_AIRLINES_NYC,
        })
    if airport in {"NYC", "JFK"} and cabin in (None, "ECONOMY"):
        airlines.append({
            "name": "Cathay Pacific", "service": "JFK via Hong Kong · Economy",
            "source_url": CATHAY_NYC,
        })
    return airlines


def published_route_airlines(draft):
    origin = draft.get("origin_location")
    destination = draft.get("destination_location")
    if origin and destination:
        return route_airlines(origin, destination, draft.get("cabin"))
    return []


# A direct airline fare page can name its own marketed carrier. Third-party
# search results and content from an agent cannot attach a carrier to a fare.
AIRLINE_DOMAINS = {
    "swiss.com": "SWISS",
    "cathaypacific.com": "Cathay Pacific",
    "singaporeair.com": "Singapore Airlines",
    "flyscoot.com": "Scoot",
    "emirates.com": "Emirates",
    "qatarairways.com": "Qatar Airways",
    "united.com": "United Airlines",
    "ana.co.jp": "All Nippon Airways",
    "jal.co.jp": "Japan Airlines",
    "aircanada.com": "Air Canada",
    "britishairways.com": "British Airways",
    "airfrance.com": "Air France",
    "klm.com": "KLM",
}


def advertised_airline(url):
    host = urlsplit(url).hostname or ""
    for domain, airline in AIRLINE_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return airline
    return None


def offer_segments(offer):
    return [
        segment for itinerary in offer.get("itineraries", [])
        for segment in itinerary.get("segments", [])
    ]


def operating_airlines(offer, carriers):
    codes = {
        segment.get("operating", {}).get("carrierCode") or segment.get("carrierCode")
        for segment in offer_segments(offer)
    }
    return sorted({carriers.get(code, code) for code in codes if code})


def available_seat_maps(response, offer, preference):
    """List seats reported available to every traveller in the quoted cabin."""
    segments = {segment["id"]: segment for segment in offer_segments(offer) if "id" in segment}
    travellers = {
        row.get("travelerId"): {
            fare["segmentId"]: fare.get("cabin") for fare in row.get("fareDetailsBySegment", [])
        }
        for row in offer.get("travelerPricings", [])
        if row.get("travelerId")
    }
    if not travellers:
        return []
    characteristics = {"WINDOW": "W", "AISLE": "A", "MIDDLE": "9"}
    characteristic = characteristics.get(preference)
    maps = []
    for seat_map in response.get("data", []):
        segment_id = seat_map.get("segmentId")
        segment = segments.get(segment_id)
        if not segment:
            continue
        available = []
        for deck in seat_map.get("decks", []):
            for seat in deck.get("seats", []):
                if characteristic and characteristic not in seat.get("characteristicsCodes", []):
                    continue
                eligible = {
                    row.get("travelerId") for row in seat.get("travelerPricing", [])
                    if row.get("seatAvailabilityStatus") == "AVAILABLE"
                }
                if travellers.keys() <= eligible and all(
                    row.get(segment_id) == seat.get("cabin") and seat.get("cabin")
                    for row in travellers.values()
                ):
                    if seat.get("number"):
                        available.append(seat["number"])
        maps.append({
            "flight": segment.get("carrierCode", "") + segment.get("number", ""),
            "from": segment.get("departure", {}).get("iataCode", ""),
            "to": segment.get("arrival", {}).get("iataCode", ""),
            "available": list(dict.fromkeys(available))[:12],
        })
    return maps
