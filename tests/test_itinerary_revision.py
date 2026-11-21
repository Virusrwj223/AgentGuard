"""Booking edits through the website and independent checks on amendment economics."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import make_app
from app.fare_estimates import simulated_demo_fare
from app.flight_details import NEW_YORK, SINGAPORE, published_route_airlines
from app.pat import Pat
from app.store import Store
from tests.support import ModelReader


class LocalFares:
    places = {"Singapore": SINGAPORE, "Montreal, Canada": {
        "name": "Montreal, Canada", "city":"Montreal", "country":"CA", "iata":"YUL",
        "latitude":45.5, "longitude":-73.6}, "New York City":NEW_YORK}

    def enrich(self, draft):
        updated = dict(draft)
        for field in ("origin","destination"):
            if field in updated:
                updated[field+"_location"] = self.places[updated[field]]
        if all(key in updated for key in ("origin_location","destination_location","cabin","travellers","return_date")):
            updated.update(fare=simulated_demo_fare(updated), route_airlines=published_route_airlines(updated))
        return updated, []


class RevisionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name)/"db")
        self.app = make_app(self.store, ModelReader())
        self.app.state.chat.travel = LocalFares()
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.client.post("/api/chat/session")
        self.send("Plan a trip to Montreal, Canada from Singapore for 1 person departing in 1 week for 14 days of travel in economy")
        self.send("Raj")
        self.booked = self.send("Confirm booking")["itinerary"]
        self.assertEqual(self.booked["price_cents"],140000)

    def send(self, text):
        revision = self.client.get("/api/chat").json()["revision"]
        result = self.client.post("/api/chat/messages",json={"message":text,"revision":revision,"nonce":uuid4().hex})
        self.assertEqual(result.status_code,200,result.text)
        return self.client.get("/api/chat").json()

    def decision(self):
        with self.store.tx(False) as db:
            return json.loads(db.execute("SELECT payload FROM decisions ORDER BY rowid DESC LIMIT 1").fetchone()[0])

    def test_compound_cabin_departure(self):
        offer = self.send("Change my class of cabin to business and we will depart 1 day later")
        self.assertTrue(offer["itinerary"]["has_offer"], offer["messages"][-1]["body"])
        self.assertEqual(offer["itinerary"]["cabin"],"ECONOMY")
        self.assertIn("4,500.00",offer["messages"][-1]["body"])
        d = self.decision()
        self.assertEqual({m["scenario"] for m in d["models"]}, {"adverse","reference"})
        self.assertTrue(all(m["runner"]["status"]=="SUCCESS" for m in d["models"]))
        after = self.send("Accept offer")["itinerary"]
        self.assertEqual((after["cabin"],after["price_cents"],after["return_date"]),
                         ("BUSINESS",590000,self.booked["return_date"]))
        self.assertEqual(after["departure"],str(date.today()+timedelta(days=8)))

    def test_numeric_reproduction_and_explicit_reverse(self):
        self.send("Move trip by 2 days")
        d = self.decision()
        scores = {m["strategy"]:Fraction(m["worst_oracle"])/100 for m in d["comparison"]}
        availability = Fraction(55,100)
        conditional = -80*availability - 150*(1-availability)
        guarantee = -80*availability - (1-availability)*(150+Fraction(1,100)*1000000)
        self.assertEqual(scores, {"baseline":-150, "conditional":conditional, "guarantee":guarantee})
        self.assertIn("+38.50", self.send("Explain this decision")["messages"][-1]["body"])
        self.send("Accept offer")
        clarify = self.send("move it back by 2 days")
        self.assertFalse(clarify["itinerary"]["has_offer"])
        self.assertIn("earlier",clarify["messages"][-1]["body"])
        self.assertTrue(self.send("earlier")["itinerary"]["has_offer"])
        after = self.send("Accept offer")["itinerary"]
        self.assertEqual(after["departure"],self.booked["departure"])

    def test_local_and_inventory_fields(self):
        edits = [
            ("change the name to Mei Lin and change the airline to SWISS", {"lead_name":"Mei Lin","airline_preference":"SWISS"}),
            ("change the number of travellers to 2", {"travellers":2,"price_cents":280000}),
            ("return two days later and choose aisle seats", {"return_date":str(date.today()+timedelta(days=23)),"seat_preference":"Aisle"}),
        ]
        for message, expected in edits:
            offer = self.send(message)
            self.assertTrue(offer["itinerary"]["has_offer"],offer["messages"][-1]["body"])
            after = self.send("Accept offer")["itinerary"]
            self.assertEqual({key:after[key] for key in expected},expected)

    def test_compound_financial_terms(self):
        self.send("Change to business class and give me SGD 10 off")
        self.assertEqual(self.decision()["status"], "APPROVED")
        revised = self.send("Accept offer")["itinerary"]
        self.assertEqual((revised["price_cents"],revised["credit_cents"]),(589000,1000))
        self.send("Change my cabin to first class at no extra charge")
        self.assertEqual(self.decision()["status"], "COUNTEROFFER")
        self.assertEqual(self.send("Decline offer")["itinerary"]["price_cents"],589000)

    def test_route_edit_reprices_and_replaces_carriers(self):
        self.send("change the destination to New York City")
        after = self.send("Accept offer")["itinerary"]
        self.assertEqual(after["destination"],"New York City")
        self.assertEqual([x["name"] for x in after["route_airlines"]],["Cathay Pacific"])
        self.send("change origin to Montreal, Canada")
        self.assertEqual(self.send("Accept offer")["itinerary"]["origin"],"Montreal, Canada")

    def test_failed_hold_keeps_every_field_and_price(self):
        with self.store.tx() as db:
            row = db.execute("SELECT sid,booking FROM accounts").fetchone()
            state = json.loads(row["booking"])
            state["supplier_mock"]="unavailable"
            db.execute("UPDATE accounts SET booking=? WHERE sid=?", (json.dumps(state),row["sid"]))
        self.send("change to business class and depart one day later")
        after = self.send("Accept offer")
        self.assertIn("unavailable", after["messages"][-1]["body"])
        for key in ("cabin","departure","return_date","price_cents"):
            self.assertEqual(after["itinerary"][key], self.booked[key])

    def test_no_solver_no_offer_and_policy_can_reject(self):
        with self.store.tx() as db:
            k = json.loads(db.execute("SELECT body FROM packs ORDER BY version DESC LIMIT 1").fetchone()[0])
            k["version"] += 1
            k["profile"]["floor_cents"] = 0
            db.execute("INSERT INTO packs VALUES(?,?)",(k["version"],json.dumps(k)))
        self.assertFalse(self.send("we will depart 1 day later")["itinerary"]["has_offer"])
        self.assertEqual(self.decision()["status"],"REJECTED")
        self.app.state.chat.service.pat = Pat({})
        self.assertFalse(self.send("Change my cabin to business")["itinerary"]["has_offer"])
        self.assertEqual(self.decision()["mode"],"Exact preview")
