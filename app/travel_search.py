"""Provider searches supply locations and fares; language models cannot invent them."""
import os
import time
import re
import unicodedata
from decimal import Decimal
from urllib.parse import quote
import httpx

from .flight_details import (
    available_seat_maps, known_photo, known_place, operating_airlines, published_route_airlines,
)


class TravelSearch:
    def __init__(self, env=None, client=None):
        self.env = os.environ if env is None else env
        self.client = client or httpx.Client(timeout=12, trust_env=False, headers={"User-Agent":"NorthlineTravelDemo/1.0"})
        self.token = None
        self.until = 0
        self.cache = {}

    def provider_error(self, exc):
        """Expose actionable categories without leaking credentials or provider bodies."""
        status=exc.response.status_code
        if status in (401,403):
            return "Amadeus rejected the credentials or API access (HTTP %s). Check that the API key and secret match AMADEUS_ENV in .env, then restart. Test and production keys are different." % status
        if status == 429:
            return "Amadeus search quota or rate limit was reached (HTTP 429). Wait before retrying or check the account quota."
        if status >= 500:
            return "Amadeus returned a service error (HTTP %s). Retry later; this does not mean the city is invalid." % status
        return "Amadeus rejected the search (HTTP %s). Check the city spelling or enter an airport IATA code; review the server's provider diagnostic if it persists." % status

    @staticmethod
    def normalized(value):
        return ''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c)).upper().strip()

    def spelling_hint(self, value):
        try:
            response=self.client.get("https://en.wikipedia.org/w/api.php",params={"action":"query","format":"json","list":"search","srsearch":value,"srinfo":"suggestion","srlimit":1})
            response.raise_for_status()
            suggestion=response.json().get('query',{}).get('searchinfo',{}).get('suggestion')
            if isinstance(suggestion,str) and 1<len(suggestion)<=100:
                suggestion=re.sub('<[^>]*>','',suggestion)
                return f' Did you mean “{suggestion}”? Please specify the city or airport, rather than a province or region.'
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            pass
        return ''

    def api(self, method, path, **kwargs):
        production = self.env.get("AMADEUS_ENV") == "production"
        base = "https://api.amadeus.com" if production else "https://test.api.amadeus.com"
        if not self.env.get("AMADEUS_CLIENT_ID") or not self.env.get("AMADEUS_CLIENT_SECRET"):
            raise ValueError("City/airport search and airline quotes need Amadeus API credentials in .env. Add AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET, then restart. Production credentials are needed for live fares.")
        if time.time() >= self.until:
            response = self.client.post(base+"/v1/security/oauth2/token", data={"grant_type":"client_credentials", "client_id":self.env["AMADEUS_CLIENT_ID"], "client_secret":self.env["AMADEUS_CLIENT_SECRET"]})
            response.raise_for_status()
            token = response.json()
            self.token, self.until = token["access_token"], time.time()+int(token["expires_in"])-30
        response = self.client.request(method, base+path, headers={"Authorization":"Bearer "+self.token}, **kwargs)
        response.raise_for_status()
        return response.json()

    def location(self, value):
        if place := known_place(value):
            return place
        key = value.casefold()
        if key in self.cache:
            return self.cache[key]
        keyword=self.normalized(value)
        data = self.api("GET", "/v1/reference-data/locations", params={"subType":"CITY,AIRPORT", "keyword":keyword, "page[limit]":10})
        rows = [r for r in data.get("data",[]) if r.get("subType") in ("CITY","AIRPORT") and r.get("iataCode")]
        exact = [r for r in rows if keyword in tuple(self.normalized(v) for v in (r["iataCode"],r.get("name",""),r.get("address",{}).get("cityName","")))]
        # Prefer a city code only when it is an exact city match. Never choose the first fuzzy hit.
        cities = [r for r in exact if r["subType"] == "CITY"]
        matches = cities or exact
        if len(matches) != 1:
            choices = "; ".join(f'{r["name"]} ({r["iataCode"]}, {r.get("address",{}).get("countryCode","")})' for r in rows[:6])
            if choices:
                raise ValueError(f'Please identify the city or airport for “{value}”. Search results: '+choices)
            hint=self.spelling_hint(value)
            coverage=(" Amadeus test city search covers the US, Spain, UK, Germany and India; Singapore and Canada are not included. A supported test route or production city-search access is needed." if self.env.get('AMADEUS_ENV')!='production' else '')
            raise ValueError(f'No city/airport search match for “{value}”.'+hint+coverage+" This is not proof that the place does not exist.")
        r = matches[0]
        result = {"name":r["name"].title(), "iata":r["iataCode"], "city":r.get("address",{}).get("cityName",r["name"]).title(), "country":r.get("address",{}).get("countryCode"), "source":"Amadeus Airport & City Search"}
        self.cache[key] = result
        return result

    def photo(self, city):
        key = "photo:"+city.casefold()
        if key in self.cache:
            return self.cache[key]
        try:
            r = self.client.get("https://en.wikipedia.org/w/api.php", params={"action":"query", "format":"json", "redirects":1,"titles":city,"prop":"pageimages|info","inprop":"url","piprop":"thumbnail|name","pithumbsize":900})
            r.raise_for_status()
            pages = list(r.json().get("query",{}).get("pages",{}).values())
            page = next((p for p in pages if p.get("thumbnail")),None)
            if page:
                url = page["thumbnail"]["source"]
                if url.startswith("https://upload.wikimedia.org/"):
                    result = {"url":url,"title":page["title"],"source_url":page["fullurl"],"credit_url":"https://commons.wikimedia.org/wiki/File:"+quote(page.get("pageimage",""))}
                    self.cache[key] = result
                    return result
        except (httpx.HTTPError,ValueError,KeyError):
            pass
        return None

    def location_error(self, exc):
        if isinstance(exc, httpx.HTTPStatusError):
            return self.provider_error(exc)
        if isinstance(exc, ValueError):
            return str(exc)
        return "Amadeus city search could not connect or returned an unexpected response. Check the network and provider configuration."

    def fare_error(self, exc):
        if isinstance(exc, httpx.HTTPStatusError):
            return "Airline fare search: " + self.provider_error(exc)
        if isinstance(exc, ValueError):
            return str(exc)
        return "The airline fare search or price confirmation failed. Please retry; no price has been invented."

    def destination_photo(self, place):
        return known_photo(place) or self.photo(place["city"])

    def fare(self, draft):
        parameters = {
            "originLocationCode": draft["origin_location"]["iata"],
            "destinationLocationCode": draft["destination_location"]["iata"],
            "departureDate": draft["departure"], "returnDate": draft["return_date"],
            "adults": draft["travellers"], "travelClass": draft["cabin"],
            "currencyCode": "SGD", "max": 5,
        }
        data = self.api("GET", "/v2/shopping/flight-offers", params=parameters)
        offers = [offer for offer in data.get("data", []) if offer.get("price", {}).get("currency") == "SGD"]
        if not offers:
            raise ValueError("No airline fare was returned for those dates and cabin. Please change the dates, route or cabin.")
        offer = min(offers, key=lambda candidate: Decimal(candidate["price"]["grandTotal"]))
        pricing = self.api(
            "POST", "/v1/shopping/flight-offers/pricing",
            json={"data": {"type": "flight-offers-pricing", "flightOffers": [offer]}},
        )
        priced = pricing["data"]["flightOffers"][0]
        if priced["price"]["currency"] != "SGD":
            raise ValueError("The airline returned a different currency. Please retry the fare search.")
        amount = Decimal(priced["price"]["grandTotal"]) * 100
        if not amount.is_finite() or amount != amount.to_integral_value() or amount <= 0:
            raise ValueError("The airline returned an invalid fare.")
        carriers = data.get("dictionaries", {}).get("carriers", {}) | pricing.get("dictionaries", {}).get("carriers", {})
        now = time.time()
        return {
            "amount_cents": int(amount), "airlines": operating_airlines(priced, carriers),
            "retrieved": now, "expires": now + 300, "source": "Amadeus",
            "seat_maps": self.seat_maps(priced, draft.get("seat_preference")),
            "live": self.env.get("AMADEUS_ENV") == "production",
        }

    def seat_maps(self, offer, preference):
        if not offer.get("travelerPricings"):
            return []
        try:
            response = self.api("POST", "/v1/shopping/seatmaps", json={"data": [offer]})
            return available_seat_maps(response, offer, preference)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return []

    def enrich(self, draft):
        updated = dict(draft)
        errors = []
        for field in ("origin", "destination"):
            value = updated.get(field)
            if not value:
                updated.pop(field + "_location", None)
                continue
            record = updated.get(field + "_location")
            if record and value.casefold() == record["name"].casefold():
                continue
            try:
                record = self.location(value)
                updated[field] = record["name"]
                updated[field + "_location"] = record
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                updated.pop(field + "_location", None)
                errors.append(field.title() + ": " + self.location_error(exc))

        destination = updated.get("destination_location")
        if destination:
            updated["photo"] = self.destination_photo(destination)
        else:
            updated.pop("photo", None)
        updated["route_airlines"] = published_route_airlines(updated)

        origin = updated.get("origin_location")
        if origin and destination and (origin["city"].casefold(), origin.get("country")) == (
            destination["city"].casefold(), destination.get("country")
        ):
            errors.append("Please choose a destination different from your starting city.")
        required = ("origin_location", "destination_location", "departure", "return_date", "travellers", "cabin")
        if errors or not all(updated.get(field) for field in required):
            updated.pop("fare", None)
            return updated, errors
        signature = [updated.get(field) for field in (*required, "seat_preference")]
        old = updated.get("fare") or {}
        if old.get("signature") != signature or old.get("expires", 0) <= time.time():
            updated.pop("fare", None)
            try:
                updated["fare"] = dict(self.fare(updated), signature=signature)
            except (httpx.HTTPError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
                errors.append(self.fare_error(exc))
        return updated, errors
