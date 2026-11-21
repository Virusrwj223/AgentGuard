"""Conversation and provider regressions for the Singapore–New York flow."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.api import make_app
from app.flight_details import NYC_PHOTO
from app.language_models import Completion
from app.pat import Pat
from app.sales_desk import make_proposal
from app.store import Store
from app.travel_search import TravelSearch
from app.web_travel_search import WebTravelSearch
from tests.support import ModelReader


CATHAY_HTML = """
<a class="result__a"
   href="https://flights.cathaypacific.com/destinations/en_SG/flights-from-singapore-to-new-york">
    Cathay Pacific flights from Singapore to New York (JFK)
</a>
<p class="result__snippet">Singapore to New York. Round-trip/Economy from SGD1,471 per adult.</p>
"""


class IntakeAgentFixture:
    def __init__(self):
        self.intake = []

    def answers(self, kind, payload):
        if kind != "intake":
            return
        self.intake.append(payload)
        if payload["message"] == "Singapore":
            # Deliberately misclassify the reply. The application knows what it asked.
            yield Completion({"destination": "Singapore"}, "test intake")
        elif payload["message"] == "two weeks from today i leave":
            yield Completion({"origin": "today i", "return_date": payload["today"]}, "test intake")


class NorthlineBookingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / "db.sqlite")
        self.app = make_app(self.store, ModelReader())
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.post("/api/chat/session")

        def search(request):
            self.assertEqual(request.url.host, "html.duckduckgo.com")
            return httpx.Response(200, text=CATHAY_HTML)

        self.travel_client = httpx.Client(transport=httpx.MockTransport(search))
        self.addCleanup(self.travel_client.close)
        self.app.state.chat.travel.client.close()
        self.app.state.chat.travel.client = self.travel_client
        self.app.state.chat.travel.env = {}

    def send(self, text):
        state = self.client.get("/api/chat").json()
        response = self.client.post(
            "/api/chat/messages", json={"message": text, "nonce": uuid4().hex, "revision": state["revision"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return self.client.get("/api/chat").json()

    def complete_trip(self):
        self.send("Book another trip")
        self.send("I am leaving from Singapore, going to New York City")
        self.send("Two weeks from today I leave")
        self.send("Seven nights")
        self.send("Two travellers under Mei Tan in economy, window seats please")
        return self.send("Confirm booking")

    def test_reported_conversation_books_with_airline_seat_preference_and_photo(self):
        agent = IntakeAgentFixture()
        self.app.state.chat.planners = agent
        started = self.send("Book another trip")
        self.assertEqual(started["messages"][-1]["body"], "Which city are you leaving from?")
        origin = self.send("Singapore")
        self.assertEqual(origin["itinerary"]["origin"], "Singapore")
        self.assertIsNone(origin["itinerary"]["destination"])
        self.assertEqual(origin["messages"][-1]["body"], "Where would you like to go?")
        route = self.send("I am leaving from Singapore, Going to New York City")
        self.assertEqual(route["itinerary"]["destination"], "New York City")
        self.assertEqual({row["name"] for row in route["itinerary"]["route_airlines"]},
                         {"Cathay Pacific", "Singapore Airlines"})
        self.send("Origin Singapore")
        dates = self.send("two weeks from today i leave")
        today = date.fromisoformat(agent.intake[-1]["today"])
        self.assertEqual(dates["itinerary"]["departure"], str(today + timedelta(days=14)))
        self.assertEqual(dates["itinerary"]["origin"], "Singapore")
        self.assertIsNone(dates["itinerary"]["return_date"])
        self.assertIn("When would you like to return?", dates["messages"][-1]["body"])
        self.assertEqual(agent.intake[0]["awaiting"], "origin")
        self.assertEqual(agent.intake[-1]["awaiting"], "departure")
        self.send("Seven nights")
        quote = self.send("Two travellers under Mei Tan in economy, window seats please")
        self.assertEqual(quote["itinerary"]["phase"], "review")
        self.assertEqual(quote["itinerary"]["airlines"], ["Cathay Pacific"])
        self.assertEqual(quote["itinerary"]["route_airlines"][0]["name"], "Cathay Pacific")
        self.assertEqual(quote["itinerary"]["seat_preference"], "Window")
        self.assertEqual(quote["itinerary"]["seat_maps"], [])
        self.assertIn("Seat availability is unverified", quote["messages"][-1]["body"])
        booked = self.send("Confirm booking")
        self.assertEqual(booked["itinerary"]["phase"], "booked")
        self.assertEqual(booked["messages"][-1]["photo"], NYC_PHOTO)
        self.assertIn("Cathay Pacific", booked["messages"][-1]["body"])
        self.assertIn("Seats are unassigned", booked["messages"][-1]["body"])
        with self.store.tx(False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], 0)

    def test_uses_official_published_fare_when_search_is_blocked(self):
        def respond(request):
            if request.url.host == "html.duckduckgo.com":
                return httpx.Response(202, text="anomaly-form")
            self.assertEqual(request.url.host, "flights.cathaypacific.com")
            return httpx.Response(200, text="""
                <table><tr><td>Singapore (SIN)</td><td>New York (JFK)</td>
                <td>Round-trip/Economy</td><td>From SGD1,471 per adult</td></tr></table>
            """)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            self.app.state.chat.travel = WebTravelSearch({}, client)
            result = self.complete_trip()["itinerary"]
        self.assertEqual(result["phase"], "booked")
        self.assertEqual(result["airlines"], ["Cathay Pacific"])
        self.assertEqual(result["price_cents"], 294200)

    def test_montreal_route_survives_edits_and_itinerary_reports_unavailable_fare(self):
        class IncorrectWorker:
            messages = []

            def answers(self, kind, payload):
                self.messages.append(payload["message"])
                yield Completion({"destination": "aisle", "origin": "change my seat preference"}, "test worker")

        fare_searches = []

        def respond(request):
            if request.url.host == "geocoding-api.open-meteo.com":
                self.assertEqual(request.url.params["name"].casefold(), "montreal")
                return httpx.Response(200, json={"results": [{
                    "name": "Montreal", "id": 6077243, "feature_code": "PPLA",
                    "country": "Canada", "country_code": "CA", "latitude": 45.5, "longitude": -73.57,
                }]})
            if request.url.host == "en.wikipedia.org":
                return httpx.Response(200, json={"query": {"pages": {}}})
            if request.url.host == "html.duckduckgo.com":
                fare_searches.append(request.url.params["q"])
                return httpx.Response(202, text="anomaly-form")
            self.assertIn(request.url.host, ("www.swiss.com", "www.qatarairways.com", "www.aircanada.com"))
            return httpx.Response(200, text="<tr><td>Singapore Montréal Round-trip Economy SGD 1,500</td></tr>")

        worker = IncorrectWorker()
        self.app.state.chat.planners = worker
        start = date.today() + timedelta(days=30)
        end = start + timedelta(days=14)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            self.app.state.chat.travel = WebTravelSearch({}, client)
            self.send("from singapore to montreal")
            self.assertEqual(worker.messages, [])
            incomplete = self.send("Show my itinerary")
            self.assertIn("What date would you like to leave?", incomplete["messages"][-1]["body"])
            self.send(f"Departing {start}, returning {end}, one traveller under Mei Tan in business class")
            self.send("Change my seat preference to aisle")
            state = self.send("Show my itinerary")
            trip = state["itinerary"]
            self.assertEqual((trip["origin"], trip["destination"], trip["seat_preference"]),
                             ("Singapore", "Montreal", "Aisle"))
            self.assertEqual((trip["departure"], trip["return_date"]), (str(start), str(end)))
            self.assertIsNone(trip["price_cents"])
            self.assertIn("curr=SGD", trip["fare_search_url"])
            reply = state["messages"][-1]["body"]
            for text in ("Singapore to Montreal", "Business class", "Seat preference: Aisle",
                         "I couldn’t verify an SGD return fare", "Compare current fares"):
                self.assertIn(text, reply)
            self.assertEqual(len(fare_searches), 2)
            self.send("Refresh my quote")
            self.assertEqual(len(fare_searches), 3)
            self.assertEqual(len(worker.messages), 2)

    def test_asking_about_airlines_and_seats_does_not_request_a_change(self):
        self.complete_trip()
        state = self.send("Which airline am I flying and what seats do I have?")
        self.assertIn("Cathay Pacific", state["messages"][-1]["body"])
        self.assertIn("Window", state["messages"][-1]["body"])
        self.assertIn("unassigned", state["messages"][-1]["body"])
        self.assertFalse(state["itinerary"]["has_decision"])

    def test_blocked_providers_still_allow_demo_booking_and_verified_credit(self):
        def respond(request):
            if request.url.host == "serpapi.com":
                return httpx.Response(429)
            return httpx.Response(202, text="anomaly-form")

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            travel = self.app.state.chat.travel
            travel.client = client
            travel.env = {"SERPAPI_API_KEY": "demo-key"}
            booked = self.complete_trip()
        itinerary = booked["itinerary"]
        self.assertEqual(itinerary["phase"], "booked")
        self.assertTrue(itinerary["fare_demo"])
        self.assertEqual(itinerary["price_cents"], 280000)
        self.assertEqual(itinerary["airlines"], [])
        self.assertEqual(itinerary["route_airlines"][0]["name"], "Cathay Pacific")
        self.assertEqual(booked["messages"][-1]["photo"], NYC_PHOTO)
        self.assertIn("simulated total", booked["messages"][-1]["body"])
        self.assertIn("Simulated demo fare", booked["messages"][-3]["body"])
        self.assertIn("Simulated demo total", self.send("Show my itinerary")["messages"][-1]["body"])
        offer = self.send("Can I get SGD 10 off?")["itinerary"]
        self.assertTrue(offer["has_offer"])
        applied = self.send("Accept offer")["itinerary"]
        self.assertEqual(applied["price_cents"], 279000)
        with self.store.tx(False) as db:
            event = db.execute("SELECT data FROM events WHERE kind='standard booking confirmed'").fetchone()
            self.assertEqual(json.loads(event["data"])["fare_source"], "Simulated demo fare")

    def test_preference_change_is_modeled_and_requires_exact_consent(self):
        self.complete_trip()
        offer = self.send("Change my seat preference to aisle")
        self.assertTrue(offer["itinerary"]["has_offer"])
        self.assertEqual(offer["itinerary"]["seat_preference"], "Window")
        with self.store.tx(False) as db:
            decision = json.loads(db.execute("SELECT payload FROM decisions").fetchone()[0])
        self.assertEqual(decision["proposal"]["goal"]["seat_preference"], "AISLE")
        self.assertEqual(decision["terms"]["seat_preference"], "AISLE")
        models = [model for model in decision["models"] if model["strategy"] == "record_seat_preference"]
        self.assertEqual(len(models), 2)
        self.assertTrue(all(model["runner"]["status"] == "SUCCESS" for model in models))
        self.assertTrue(all("seat_preference_cost" in model["assumptions"] for model in models))
        applied = self.send("Accept offer")["itinerary"]
        self.assertEqual(applied["seat_preference"], "Aisle")
        self.assertEqual(applied["price_cents"], offer["itinerary"]["price_cents"])

    def test_unavailable_pat_keeps_the_original_preference(self):
        self.app.state.chat.service.pat = Pat({})
        self.complete_trip()
        result = self.send("Aisle seat please")
        self.assertFalse(result["itinerary"]["has_offer"])
        self.assertEqual(result["itinerary"]["seat_preference"], "Window")
        self.assertIn("verifier is unavailable", result["messages"][-1]["body"])
        with self.store.tx(False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM grants").fetchone()[0], 0)

    def test_date_change_invalidates_the_old_flight_details_only_after_acceptance(self):
        before = self.complete_trip()["itinerary"]
        proposal = self.send("Move my trip 14 days later")["itinerary"]
        self.assertTrue(proposal["has_offer"])
        self.assertEqual(proposal["airlines"], before["airlines"])
        self.assertEqual(proposal["departure"], before["departure"])
        after = self.send("Accept offer")["itinerary"]
        self.assertTrue(after["flight_details_stale"])
        self.assertEqual(after["airlines"], [])
        self.assertEqual(after["seat_preference"], "Window")
        self.assertEqual(after["photo"], NYC_PHOTO)

    def test_resumes_older_blank_draft_using_last_question(self):
        self.send("Book another trip")
        with self.store.tx() as db:
            db.execute("UPDATE web_trips SET draft='{}'")
        self.assertEqual(self.send("Singapore")["itinerary"]["origin"], "Singapore")


class AirlineSeatMapTests(unittest.TestCase):
    def test_only_matching_available_seats_and_operating_carrier_are_reported(self):
        sent = []
        segment = {"id": "1", "carrierCode": "XX", "operating": {"carrierCode": "SQ"}, "number": "24",
                   "departure": {"iataCode": "SIN"}, "arrival": {"iataCode": "JFK"}}
        offer = {
            "price": {"currency": "SGD", "grandTotal": "1234.56"},
            "itineraries": [{"segments": [segment]}],
            "travelerPricings": [
                {"travelerId": traveler, "fareDetailsBySegment": [{"segmentId": "1", "cabin": "PREMIUM_ECONOMY"}]}
                for traveler in ("1", "2")
            ],
        }

        def seat(number, code="W", status="AVAILABLE", cabin="PREMIUM_ECONOMY"):
            return {
                "number": number, "cabin": cabin, "characteristicsCodes": [code],
                "travelerPricing": [
                    {"travelerId": "1", "seatAvailabilityStatus": "AVAILABLE"},
                    {"travelerId": "2", "seatAvailabilityStatus": status},
                ],
            }

        def respond(request):
            sent.append(request)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "fixture", "expires_in": 1800})
            if request.url.path.endswith("/pricing"):
                return httpx.Response(200, json={"data": {"flightOffers": [offer]}})
            if request.url.path.endswith("/seatmaps"):
                self.assertEqual(request.method, "POST")
                self.assertEqual(json.loads(request.content), {"data": [offer]})
                rows = [seat("31A"), seat("32A", status="BLOCKED"), seat("33C", code="A"),
                        seat("4A", cabin="BUSINESS")]
                return httpx.Response(200, json={"data": [{"segmentId": "1", "decks": [{"seats": rows}]}]})
            return httpx.Response(200, json={"data": [offer], "dictionaries": {"carriers": {"SQ": "Singapore Airlines"}}})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            travel = TravelSearch({"AMADEUS_CLIENT_ID": "fixture", "AMADEUS_CLIENT_SECRET": "fixture"}, client)
            start = date.today() + timedelta(days=14)
            draft, errors = travel.enrich({
                "origin": "Singapore", "destination": "JFK", "departure": str(start),
                "return_date": str(start + timedelta(days=7)), "travellers": 2,
                "cabin": "PREMIUM_ECONOMY", "seat_preference": "WINDOW",
            })
        self.assertEqual(errors, [])
        self.assertEqual(draft["fare"]["airlines"], ["Singapore Airlines"])
        self.assertEqual(draft["fare"]["seat_maps"][0]["available"], ["31A"])
        self.assertFalse(draft["fare"]["live"])
