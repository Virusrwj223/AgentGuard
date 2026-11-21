"""Key-free place lookup and sourced advertised fare estimates for the demo."""
import csv
import io
import math
import re
import time
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlparse

import httpx

from .fare_estimates import google_flights_estimate, simulated_demo_fare
from .flight_details import advertised_airline, known_photo, known_place
from .travel_search import TravelSearch


class SearchResults(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current = None
        self.capture = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = attributes.get("class", "").split()
        if tag == "a" and "result__a" in classes:
            self.current = {"url": attributes.get("href", ""), "title": "", "snippet": ""}
            self.rows.append(self.current)
            self.capture = "title"
            self.depth = 1
        elif self.capture and tag not in {"br", "hr", "img", "input", "source", "wbr"}:
            self.depth += 1
        elif self.current and not self.capture and "result__snippet" in classes:
            self.capture = "snippet"
            self.depth = 1

    def handle_endtag(self, tag):
        if self.capture:
            self.depth -= 1
            if self.depth <= 0:
                self.capture = None

    def handle_data(self, data):
        if self.current and self.capture:
            self.current[self.capture] += data


class AirlineFareTable(HTMLParser):
    """Read fare evidence from individual published rows."""
    def __init__(self):
        super().__init__()
        self.row = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []

    def handle_endtag(self, tag):
        if tag == "tr" and self.row is not None:
            self.rows.append(" ".join(self.row))
            self.row = None

    def handle_data(self, data):
        if self.row is not None and data.strip():
            self.row.append(data.strip())


class WebTravelSearch(TravelSearch):
    def __init__(self, env=None, client=None, *, allow_demo_fares=False):
        super().__init__(env, client)
        self.allow_demo_fares = allow_demo_fares

    def search(self, query):
        response = self.client.get("https://html.duckduckgo.com/html/", params={"q": query}, timeout=4)
        response.raise_for_status()
        parser = SearchResults()
        parser.feed(response.text)
        if not parser.rows and ("anomaly" in response.text.lower() or response.status_code == 202):
            raise ValueError("The internet search provider blocked automated requests.")
        for row in parser.rows:
            url = row["url"]
            if url.startswith("//"):
                url = "https:" + url
            parsed = urlparse(url)
            if parsed.hostname == "duckduckgo.com" or (parsed.hostname or "").endswith(".duckduckgo.com"):
                url = parse_qs(parsed.query).get("uddg", [""])[0]
            row["url"] = url
        return [row for row in parser.rows if row["url"].startswith("https://")]

    def airports(self,value):
        records=self.cache.get('airports')
        if records is None:
            response=self.client.get('https://raw.githubusercontent.com/datasets/airport-codes/master/data/airport-codes.csv')
            response.raise_for_status()
            records=list(csv.DictReader(io.StringIO(response.text)))
            self.cache['airports']=records
        wanted=self.normalized(value)
        exact=[r for r in records if r.get('iata_code') and (wanted==r['iata_code'] or wanted==self.normalized(r.get('name',''))) ]
        if len(exact)!=1:
            raise ValueError('Please give the full airport name or its three-letter IATA code.')
        r=exact[0]
        return {'name':r['name'],'city':r.get('municipality') or r['name'],'country':r.get('iso_country'),'iata':r['iata_code'],'source':'OurAirports / airport-codes dataset'}

    def location(self,value):
        if place := known_place(value):
            return place
        key='web:'+value.casefold()
        if key in self.cache:return self.cache[key]
        if re.fullmatch('[A-Za-z]{3}',value) or re.search(r'\bairport\b',value,re.I):
            try:
                result = self.airports(value)
            except ValueError:
                if re.search(r"\bairport\b", value, re.I):
                    raise
                result = self.geocode(value)
        else:
            result = self.geocode(value)
        self.cache[key] = result
        return result

    def geocode(self, value):
        parts=[part.strip() for part in value.split(',')]
        query=re.sub(r'\s+city$','',parts[0],flags=re.I)
        qualifiers=[self.normalized(part) for part in parts[1:]]
        response=self.client.get('https://geocoding-api.open-meteo.com/v1/search',params={'name':query,'count':10,'language':'en','format':'json'})
        response.raise_for_status()
        rows=[r for r in response.json().get('results',[]) if r.get('feature_code','').startswith('PPL')]
        if qualifiers:
            rows=[r for r in rows if all(q in {self.normalized(str(r.get(k,''))) for k in ('country','country_code','admin1','admin2','admin3','admin4')} for q in qualifiers)]
        wanted=self.normalized(query)
        exact=[r for r in rows if self.normalized(r['name'])==wanted]
        # GeoNames may list small settlements with the same name as a city.
        # Prefer a capital or a clearly dominant population only when the
        # returned records support that distinction; retain true ambiguity.
        if len(exact)>1 and not qualifiers:
            capitals=[r for r in exact if r.get('feature_code')=='PPLC']
            if len(capitals)==1:
                exact=capitals
            else:
                ranked=sorted(exact,key=lambda r:r.get('population',0),reverse=True)
                if ranked[0].get('population',0)>=100000 and ranked[0].get('population',0)>=20*max(1,ranked[1].get('population',0)):
                    exact=ranked[:1]
        if len(exact)!=1:
            suggestions='; '.join(f'{r["name"]}, {r.get("admin1","")}, {r.get("country","")}' for r in rows[:5])
            raise ValueError('Please confirm the city for “'+value+'”. '+('Did you mean '+suggestions+'? Reply with the city and country, such as “Montreal, Canada”.' if suggestions else 'No city match was found.'+self.spelling_hint(value)))
        r=exact[0]
        result={'name':r['name']+(', '+r.get('country',r.get('country_code','')) if qualifiers else ''),'city':r['name'],'country':r.get('country_code'),'country_name':r.get('country'),'region':r.get('admin1'),'geonames_id':r['id'],'latitude':r['latitude'],'longitude':r['longitude'],'source':'Open-Meteo / GeoNames city search'}
        return result

    def advertised_price(self, draft, evidence):
        normalized = self.normalized(evidence)
        route = [draft["origin_location"], draft["destination_location"]]
        for place in route:
            city = self.normalized(place["city"]).removesuffix(" CITY")
            if not re.search(r"\b" + re.escape(city) + r"\b", normalized):
                return None
            if "Airport" in place["name"] and place.get("iata"):
                if not re.search(r"\b" + re.escape(place["iata"]) + r"\b", normalized):
                    return None
        cabin = draft["cabin"].replace("_", " ").lower()
        cabin_pattern = r"(?<!premium )economy" if cabin == "economy" else re.escape(cabin)
        if not re.search(r"\b" + cabin_pattern + r"\b", evidence, re.I):
            return None
        currency = r"(?:S\$|SGD\s*\$?)\s*([\d,]+(?:\.\d{1,2})?)"
        round_trip = r"(?:round.?trip|return)"
        match = re.search(round_trip + r"[^.!?]{0,100}?" + currency, evidence, re.I)
        if not match:
            match = re.search(currency + r"[^.!?]{0,35}?" + round_trip, evidence, re.I)
        if match:
            cents = int(Decimal(match[1].replace(",", "")) * 100)
            return cents if cents > 0 else None
        return None

    def airline_fares(self, draft):
        # These maintained airline links advertise the outbound Singapore route.
        if draft["origin_location"].get("iata") != "SIN":
            return
        for route in draft.get("route_airlines", []):
            try:
                response = self.client.get(route["source_url"], follow_redirects=True, timeout=3)
                response.raise_for_status()
                if advertised_airline(str(response.url)) != route["name"]:
                    continue
                parser = AirlineFareTable()
                parser.feed(response.text)
                for evidence in parser.rows:
                    yield {"title": route["name"], "url": str(response.url), "snippet": evidence}
            except (httpx.HTTPError, ValueError):
                continue

    def fare(self, draft):
        if quote := google_flights_estimate(self.client, self.env.get("SERPAPI_API_KEY", "").strip(), draft):
            return quote
        origin = draft["origin_location"]["city"]
        destination = draft["destination_location"]["city"]
        cabin = draft["cabin"].replace("_", " ").lower()
        query = f"{origin} to {destination} round trip flights {draft['departure']} {draft['return_date']} {cabin} SGD per adult"
        try:
            rows = self.search(query)
        except (httpx.HTTPError, ValueError):
            rows = []
        for row in rows:
            if estimate := self.estimate(draft, row, query):
                return estimate
        for row in self.airline_fares(draft):
            if estimate := self.estimate(draft, row, query):
                return estimate
        if self.allow_demo_fares:
            return simulated_demo_fare(draft)
        raise ValueError(
            "I couldn’t verify an advertised SGD return fare for this route and cabin. "
            "Use “Compare current fares” in your itinerary, or say “Refresh my quote” to search again."
        )

    def estimate(self, draft, row, query):
        unit = self.advertised_price(draft, row["title"] + " " + row["snippet"])
        if unit is None:
            return None
        airline = advertised_airline(row["url"])
        now = time.time()
        return {
            "airlines": [airline] if airline else [], "amount_cents": unit * draft["travellers"],
            "per_person_cents": unit, "expires": now + 1800, "retrieved": now,
            "source": "Internet search — advertised fare estimate", "live": False, "estimate": True,
            "source_url": row["url"], "source_title": row["title"], "evidence": row["snippet"], "query": query,
            "assumptions": "Advertised return fare per adult multiplied by traveller count. "
                           "The estimate does not verify requested dates, baggage, availability or checkout price.",
        }

    def destination_photo(self,record):
        if photo := known_photo(record):
            return photo
        # Names such as Québec and New York can refer to a region. Require the
        # article coordinates to be near the city returned by the place search.
        if 'latitude' not in record:return self.photo(record['city'])
        key = 'located-photo:' + record['city'] + ':' + str(record.get('geonames_id'))
        if key in self.cache:return self.cache[key]
        try:
            titles=record['city']+'|'+record['city']+' City'
            response=self.client.get('https://en.wikipedia.org/w/api.php',params={'action':'query','format':'json','redirects':1,'titles':titles,'prop':'pageimages|info|coordinates','inprop':'url','piprop':'thumbnail|name','pithumbsize':900})
            response.raise_for_status()
            for page in response.json().get('query',{}).get('pages',{}).values():
                coords=page.get('coordinates') or []
                if not coords or not page.get('thumbnail'):continue
                lat,lon=coords[0]['lat'],coords[0]['lon']
                distance=math.hypot((lat-record['latitude'])*111,(lon-record['longitude'])*111*math.cos(math.radians(lat)))
                url=page['thumbnail']['source']
                if distance<60 and url.startswith('https://upload.wikimedia.org/'):
                    photo={'url':url,'title':page['title'],'source_url':page['fullurl'],'credit_url':'https://commons.wikimedia.org/wiki/File:'+quote(page.get('pageimage',''))}
                    self.cache[key]=photo
                    return photo
        except (httpx.HTTPError,KeyError,TypeError,ValueError):pass
        return None

    def location_error(self, exc):
        if isinstance(exc, ValueError):
            return str(exc)
        return "Internet place search could not be reached. Check the connection and retry."

    def fare_error(self, exc):
        if isinstance(exc, ValueError):
            return str(exc)
        return "Internet fare search was blocked or unavailable. Please retry later; no price has been invented."
