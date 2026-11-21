import json
import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
import httpx

from fastapi.testclient import TestClient
from app.api import make_app
from app.pat import Pat
from app.language_models import Planners
from app.store import Store
from tests.support import ModelReader,TravelFixture


class RoundedModelReader(ModelReader):
    """Emulate a console that prints six decimal places for a valid probability."""
    def run(self,model):
        result=super().run(model)
        from fractions import Fraction
        result["probability"]=f'{float(Fraction(result["probability"])):.6f}'
        return result


class TravelDesk(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store=Store(Path(tmp.name)/"db.sqlite")
        self.app=make_app(self.store,ModelReader())
        self.app.state.chat.travel=TravelFixture()
        self.client=TestClient(self.app)
        self.client.post("/api/chat/session")
        self.start=date.today()+timedelta(days=30)
        self.end=self.start+timedelta(days=4)
        self.prompt=(f"Book from Singapore to Luang Prabang, departing {self.start} and returning "
                     f"{self.end} for two travellers under Mei Tan in economy class.")
    def state(self):
        return self.client.get("/api/chat").json()
    def send(self,text):
        body={"message":text,"nonce":uuid4().hex,"revision":self.state()["revision"]}
        resp=self.client.post("/api/chat/messages",json=body)
        self.assertEqual(resp.status_code,200,resp.text)
        return self.state()
    def count(self,name):
        with self.store.tx(False) as db:
            return db.execute("SELECT COUNT(*) FROM "+name).fetchone()[0]

    def test_arbitrary_trip_and_deferred_credit(self):
        before=self.send(self.prompt+" Can I get SGD 10 off?")
        t=before["itinerary"]
        self.assertEqual((t["phase"],t["destination"],t["lead_name"],t["price_cents"],t["queued_requests"]),
                         ("review","Luang Prabang","Mei Tan",96000,1))
        self.assertEqual(self.count("accounts"),0)
        self.assertEqual(self.count("requests"),0)
        self.assertEqual(self.count("decisions"),0)
        booked=self.send("Confirm booking")["itinerary"]
        self.assertTrue(booked["has_offer"])
        self.assertEqual((booked["phase"],booked["price_cents"]),("booked",96000))
        explanation=self.send("Why was that offered?")["messages"][-1]["body"]
        self.assertIn("Give SGD 10.00 credit",explanation)
        audit=self.send("Show the assumptions")["messages"][-1]["body"]
        self.assertIn("SGD 25.00",audit);self.assertIn("#assert Business()",audit)
        accepted=self.send("Accept offer")["itinerary"]
        self.assertEqual((accepted["price_cents"],accepted["credit_cents"],accepted["has_offer"]),(95000,1000,False))
        self.assertEqual(self.send("Accept offer")["itinerary"]["price_cents"],95000)
        with self.store.tx(False) as db:
            cats=[r[0] for r in db.execute("SELECT kind FROM events ORDER BY id")]
            self.assertLess(cats.index("standard booking confirmed"),cats.index("model evaluated"))
            self.assertEqual(cats.count("mock transaction"),1)

    def test_conversational_open_world_and_recovery(self):
        self.send("Plan a trip to Český Krumlov")
        self.send("Kuala Lumpur")
        invalid=self.send("2020-02-30")
        self.assertIsNone(invalid["itinerary"]["departure"])
        self.send(str(self.start))
        self.send("four nights")
        self.send("2 people")
        self.send("Sam Lee")
        ready=self.send("Economy")["itinerary"]
        self.assertEqual((ready["destination"],ready["origin"],ready["return_date"],ready["phase"]),("Český Krumlov","Kuala Lumpur",str(self.end),"review"))
        original=self.send("Confirm booking")["itinerary"]["id"]
        reopened=TestClient(make_app(Store(self.store.path),Pat({})))
        reopened.cookies.update(self.client.cookies)
        self.assertEqual(reopened.get("/api/chat").json()["itinerary"]["id"],original)
        self.send("Book another trip to Lombok")
        self.assertIn(original,self.send("List my trips")["messages"][-1]["body"])
        again=self.send("Open "+original)["itinerary"]
        self.assertEqual(again["destination"],"Český Krumlov")

    def test_session_isolation_and_no_founder_login(self):
        self.send(self.prompt+" Can I get 10% off?")
        a=self.send("Confirm booking")["itinerary"]
        other=TestClient(self.app)
        self.assertEqual(other.get("/api/chat").status_code,401)
        self.assertEqual(other.post("/api/chat/session").json()["messages"],[])
        def send_other(text):
            doc=other.get("/api/chat").json()
            return other.post("/api/chat/messages",json={"nonce":uuid4().hex,"revision":doc["revision"],"message":text}).json()
        self.assertIsNone(send_other("Open "+a["id"])["itinerary"])
        self.assertNotIn(a["id"],send_other("Explain the decision")["messages"][-1]["body"])
        self.assertEqual(self.client.get("/founder",follow_redirects=False).headers["location"],"/")
        self.assertEqual(self.client.get("/api/history").status_code,403)

    def test_startup_erases_demo_activity_and_replaces_stale_browser_session(self):
        self.send(self.prompt + " Can I get SGD 10 off?")
        self.send("Confirm booking")
        self.send("Accept offer")
        role_key = self.store.role_key("web_worker")
        with self.store.tx() as db:
            db.execute("INSERT INTO drafts VALUES('prior-draft',1,'{}','pending')")
        tables = ("accounts", "requests", "decisions", "grants", "events", "drafts", "web_chats",
                  "web_trips", "web_messages", "web_turns", "web_sales")
        self.assertTrue(all(self.count(table) for table in tables))
        policy_count = self.count("packs")

        with patch.dict("os.environ", {"AG_DB": str(self.store.path)}):
            restarted = TestClient(make_app(solver=Pat({})))
        self.assertTrue(all(self.count(table) == 0 for table in tables))
        self.assertEqual(self.count("packs"), policy_count)
        self.assertEqual(self.store.role_key("web_worker"), role_key)
        restarted.cookies.update(self.client.cookies)
        self.assertEqual(restarted.get("/api/chat").status_code, 401)
        state = restarted.post("/api/chat/session").json()
        self.assertTrue(state["new_session"])
        self.assertEqual(state["trips"], [])
        self.assertEqual(state["messages"], [])
        self.assertFalse(restarted.post("/api/chat/session").json()["new_session"])
        with self.store.tx() as db:
            db.execute("INSERT INTO events(kind) VALUES('new event')")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable event"):
                db.execute("DELETE FROM events")

    def test_review_without_solver_preserves_confirmed_booking(self):
        app=make_app(self.store,Pat({}));app.state.chat.travel=TravelFixture()
        self.client=TestClient(app)
        self.client.post("/api/chat/session")
        self.send(self.prompt+" Can we move my booking 14 days later?")
        t=self.send("Confirm booking")["itinerary"]
        self.assertTrue(t["has_decision"]); self.assertFalse(t["has_offer"])
        self.assertEqual(t["departure"],str(self.start))
        self.assertIn("preview",self.send("Why?")["messages"][-1]["body"].lower())
        self.assertEqual(self.count("grants"),0)

    def test_amendment_updates_both_dates_and_requires_consent(self):
        self.send(self.prompt);self.send("Confirm booking")
        self.send("Move my trip 14 days later at no extra charge")
        self.assertEqual(self.state()["itinerary"]["return_date"],str(self.end))
        t=self.send("Accept offer")["itinerary"]
        self.assertEqual((t["departure"],t["return_date"]),(str(self.start+timedelta(days=14)),str(self.end+timedelta(days=14))))
        self.assertIn("hold verified",self.send("Explain my decision")["messages"][-1]["body"])
        self.send("Ask for a free room upgrade")
        self.assertTrue(self.state()["itinerary"]["has_offer"])
        self.send("Decline offer")
        self.assertFalse(self.state()["itinerary"]["upgraded"])

    def test_advance_booking_amendments_in_both_directions(self):
        far=date.today()+timedelta(days=180)
        back=far+timedelta(days=7)
        self.send(f"Book from Singapore to Toronto, departing {far} and returning {back} "
                  "for two travellers under Mei Tan in economy class.")
        booked=self.send("Confirm booking")["itinerary"]
        self.assertEqual(booked["departure"],str(far))
        later=self.send("Move my trip 14 days later")
        self.assertTrue(later["itinerary"]["has_offer"],later["messages"][-1]["body"])
        self.assertIn("AG-03",self.send("Show the assumptions")["messages"][-1]["body"])
        accepted=self.send("Accept offer")["itinerary"]
        self.assertEqual((accepted["departure"],accepted["return_date"]),
                         (str(far+timedelta(days=14)),str(back+timedelta(days=14))))
        earlier=self.send("Move my trip 14 days earlier")
        self.assertTrue(earlier["itinerary"]["has_offer"],earlier["messages"][-1]["body"])
        accepted=self.send("Accept offer")["itinerary"]
        self.assertEqual((accepted["departure"],accepted["return_date"]),(str(far),str(back)))

    def test_reopens_legacy_far_ahead_booking_with_advance_context(self):
        self.start=date.today()+timedelta(days=180)
        self.end=self.start+timedelta(days=4)
        self.send(f"Book from Singapore to Toronto, departing {self.start} and returning {self.end} "
                  "for two travellers under Mei Tan in economy class.")
        self.send("Confirm booking")
        with self.store.tx() as db:
            row=db.execute("SELECT sid,booking FROM accounts").fetchone()
            legacy=json.loads(row["booking"])
            legacy["context"]="normal"
            legacy["slots"]={str(n): str(self.start+timedelta(days=n)) for n in range(1,91)}
            db.execute("UPDATE accounts SET booking=? WHERE sid=?",(json.dumps(legacy),row["sid"]))
        Store(self.store.path)
        with self.store.tx(False) as db:
            migrated=json.loads(db.execute("SELECT booking FROM accounts").fetchone()[0])
        self.assertEqual(migrated["context"],"advance")
        self.assertIn("-14",migrated["slots"])
        self.assertEqual(migrated["version"],legacy["version"]+1)
        Store(self.store.path)
        with self.store.tx(False) as db:
            self.assertEqual(json.loads(db.execute("SELECT booking FROM accounts").fetchone()[0])["version"],migrated["version"])

    def test_wide_liability_range_uses_independent_outcome_checks(self):
        app=make_app(self.store,RoundedModelReader());app.state.chat.travel=TravelFixture()
        self.client=TestClient(app)
        self.client.post("/api/chat/session")
        far=date.today()+timedelta(days=180)
        self.send(f"Book from Singapore to Toronto, departing {far} and returning "
                  f"{far+timedelta(days=7)} for two travellers under Mei Tan in economy class.")
        self.send("Confirm booking")
        result=self.send("Move my trip 14 days earlier")
        self.assertTrue(result["itinerary"]["has_offer"],result["messages"][-1]["body"])
        with self.store.tx(False) as db:
            decision=json.loads(db.execute("SELECT payload FROM decisions ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertEqual(decision["strategy"],"conditional")
        guarantees=[m for m in decision["models"] if m["strategy"]=="guarantee"]
        self.assertEqual(len(guarantees),2)
        for model in guarantees:
            self.assertEqual(model["runner"]["verification"],"terminal outcome probabilities")
            self.assertEqual(model["runner"]["status"],"SUCCESS")
            self.assertTrue(model["runner"]["probes"])
        self.assertIn("6 of them",self.send("Show the assumptions")["messages"][-1]["body"])

    def test_retries_stale_tabs_and_failed_exact_terms(self):
        self.send(self.prompt)
        body={"nonce":uuid4().hex,"revision":self.state()["revision"],"message":"Confirm booking"}
        self.assertTrue(self.client.post("/api/chat/messages",json=body).is_success)
        self.assertTrue(self.client.post("/api/chat/messages",json=body).is_success)
        self.assertEqual(self.count("accounts"),1)
        self.assertEqual(self.client.post("/api/chat/messages",json={**body,"nonce":uuid4().hex}).status_code,409)
        self.send("Request SGD 10 off")
        with self.store.tx() as db:db.execute("UPDATE grants SET expires=0")
        self.assertIn("expired",self.send("Accept offer")["messages"][-1]["body"])
        self.assertEqual(self.state()["itinerary"]["price_cents"],96000)

    def test_standing_worker_receives_sales_job_and_cannot_execute(self):
        self.send(self.prompt);self.send("Confirm booking")
        from app.sales_desk import make_proposal
        from app.contracts import Goal
        w=TestClient(self.app)
        headers={"Authorization":"Bearer "+self.store.role_key("web_worker")}
        self.assertEqual(w.get("/api/history",headers=headers).status_code,403)
        with ThreadPoolExecutor(1) as ex:
            pending=ex.submit(w.post,"/api/web-worker/next",json={"wait_seconds":5},headers=headers)
            # Mark the listener available without blocking. A race is harmless: either poll can lease a job.
            self.app.state.chat.broker.next(0)
            from threading import Thread
            result=[]
            browser=Thread(target=lambda:result.append(self.send("Can I get SGD 10 off?"))); browser.start()
            job=pending.result().json()
            self.assertEqual(job["kind"],"sales")
            payload=job["input"]; ctx=payload["booking"]
            self.assertNotIn("lead_name",ctx);self.assertNotIn("sales_key",ctx)
            answer=make_proposal(Goal.model_validate(payload["goal"]),ctx,payload["request_id"]).model_dump()
            token={"lease":job["lease"],"job_id":job["job_id"],"answer":answer}
            self.assertTrue(w.post("/api/web-worker/submit",headers=headers,json=token).is_success)
            browser.join(timeout=8);self.assertFalse(browser.is_alive());self.assertTrue(result[0]["itinerary"]["has_offer"])
        with self.store.tx(False) as db:
            d=json.loads(db.execute("SELECT payload FROM decisions").fetchone()[0])
            self.assertEqual(d["source"],"QwenWork background worker")
        self.assertEqual(w.post("/api/web-worker/submit",headers=headers,json=token).status_code,409)

    def test_hosted_planner_is_used_in_chat_and_invalid_output_falls_back(self):
        from app.contracts import Goal
        from app.sales_desk import make_proposal
        calls=[]
        def completion(request):
            self.assertEqual(str(request.url),"https://model.example/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"],"Bearer example")
            body=json.loads(request.content)
            data=json.loads(body["messages"][1]["content"])
            if "fields" in data:
                answer={"undeclared":"ignored"}; calls.append("intake")
            elif "booking" in data:
                answer=make_proposal(Goal.model_validate(data["goal"]),data["booking"],data["request_id"]).model_dump()
                calls.append("sales")
            else:
                answer={"goal":{"type":"credit","discount_cents":1000}}; calls.append("interpret")
            return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(answer)}}]})
        with httpx.Client(transport=httpx.MockTransport(completion)) as client:
            self.app.state.chat.planners=Planners(env={"AG_QWEN_BASE_URL":"https://model.example/v1","DASHSCOPE_API_KEY":"example"},client=client)
            self.send(self.prompt)
            self.send("Confirm booking")
            self.send("Please shave SGD 10 from this fare")
            self.assertIn("Qwen Model Studio",self.send("Explain this offer")["messages"][-1]["body"])
            self.assertEqual(self.send("Accept offer")["itinerary"]["price_cents"],95000)
        self.assertEqual(calls,["intake","interpret","sales"])

    def test_expired_offer_releases_waiting_request_on_reload(self):
        self.send(self.prompt);self.send("Confirm booking")
        self.send("Can I get ten dollars off?")
        self.assertTrue(self.state()["itinerary"]["has_offer"])
        self.send("Move my trip 14 days later")
        self.assertEqual(self.state()["itinerary"]["queued_requests"],1)
        with self.store.tx() as db:
            db.execute("UPDATE grants SET expires=0")
        self.client.get("/api/chat")
        self.assertEqual(self.count("decisions"),2)
        second=self.state()["itinerary"]
        self.assertTrue(second["has_offer"]);self.assertEqual(second["queued_requests"],0)
        self.send("Decline offer")
        with self.store.tx(False) as db:
            key=db.execute("SELECT customer FROM accounts").fetchone()[0]
        latest=self.client.get("/api/latest",headers={"Authorization":"Bearer "+key}).json()
        self.assertEqual(latest["execution"],"DECLINED");self.assertNotIn("terms_digest",latest)

    def test_new_itinerary_with_negotiation_after_an_existing_booking(self):
        self.send(self.prompt)
        first=self.send("Confirm booking")["itinerary"]["id"]
        new=self.send(f"Book a trip from Jakarta to Dili, {self.start} for two nights for one traveller under Lin Wu in economy class. Can I get ten dollars off?")
        self.assertEqual((new["itinerary"]["destination"],new["itinerary"]["phase"],new["itinerary"]["price_cents"],
                          new["itinerary"]["queued_requests"]),("Dili","review",24000,1))
        self.assertEqual(self.count("requests"),0)
        self.assertTrue(self.send("Confirm booking")["itinerary"]["has_offer"])
        new=self.send("Accept offer")["itinerary"]
        self.assertEqual(new["price_cents"],23000)
        self.assertEqual(self.send("Open "+first)["itinerary"]["price_cents"],96000)

    def test_repeat_credit_tracks_cumulative_cost_and_rejects_excess(self):
        self.send(self.prompt);self.send("Confirm booking")
        for amount in (10,5):
            self.assertTrue(self.send(f"Give me SGD {amount} off")["itinerary"]["has_offer"])
            self.send("Accept offer")
        trip=self.state()["itinerary"]
        self.assertEqual(trip["credit_cents"],1500)
        self.assertEqual(trip["price_cents"],94500)
        self.send("Give me SGD 10 off");self.send("Accept offer")
        trip=self.send("Give me SGD 1 off")["itinerary"]
        self.assertEqual(trip["credit_cents"],2500)
        self.assertFalse(trip["has_offer"])
        self.assertIn("below",self.send("Explain this decision")["messages"][-1]["body"])

    def test_credit_counteroffer_uses_remaining_modelled_contribution(self):
        self.send(self.prompt);self.send("Confirm booking")
        self.send("Give me SGD 20 off");self.send("Accept offer")
        self.assertTrue(self.send("Give me SGD 10 off")["itinerary"]["has_offer"])
        accepted=self.send("Accept offer")["itinerary"]
        self.assertEqual(accepted["credit_cents"],2500)

    def test_free_booking_counteroffer_and_followup_price_reduction(self):
        self.send(self.prompt)
        self.send("Confirm booking")
        with self.store.tx() as db:
            row = db.execute("SELECT sid,booking FROM accounts").fetchone()
            booked = json.loads(row["booking"])
            booked.update(paid_cents=280000, list_cents=280000)
            db.execute("UPDATE accounts SET booking=? WHERE sid=?", (json.dumps(booked),row["sid"]))
        free = self.send("make the total cost free")
        self.assertTrue(free["itinerary"]["has_offer"])
        self.assertEqual(free["itinerary"]["price_cents"],280000)
        reply = free["messages"][-1]["body"]
        self.assertIn("can’t waive the full fare", reply)
        self.assertIn("SGD 25.00", reply)
        self.assertIn("SGD 2,775.00", reply)
        self.assertEqual(self.send("Accept offer")["itinerary"]["price_cents"],277500)

        followup = self.send("lower the price")
        self.assertFalse(followup["itinerary"]["has_offer"])
        self.assertEqual(followup["itinerary"]["price_cents"],277500)
        self.assertIn("can’t offer a further credit", followup["messages"][-1]["body"])
        self.assertIn("SGD 25.00", followup["messages"][-1]["body"])
        self.assertIn("below", self.send("Explain this decision")["messages"][-1]["body"])
        self.assertIn("SGD 25.00", self.send("Show the assumptions")["messages"][-1]["body"])
        with self.store.tx(False) as db:
            decision = json.loads(db.execute("SELECT payload FROM decisions ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.assertEqual(decision["status"], "REJECTED")
        self.assertEqual(decision["proposal"]["goal"]["type"], "credit")
        self.assertTrue(decision["models"])
        self.assertTrue(all(m["runner"]["status"] == "SUCCESS" for m in decision["models"]))

    def test_unspecified_price_reduction_checks_remaining_credit(self):
        self.send(self.prompt)
        self.send("Confirm booking")
        self.send("Give me SGD 10 off")
        self.send("Accept offer")
        followup = self.send("Can you lower the price?")
        self.assertTrue(followup["itinerary"]["has_offer"])
        self.assertIn("SGD 15.00", followup["messages"][-1]["body"])
        self.assertEqual(self.send("Accept offer")["itinerary"]["credit_cents"],2500)

    def test_switch_and_delete_erases_selected_booking_and_history(self):
        self.send(self.prompt);first=self.send("Confirm booking")["itinerary"]["id"]
        self.send("Give me SGD 10 off");self.send("Accept offer")
        with self.store.tx(False) as db:
            sid=db.execute("SELECT sid FROM web_trips WHERE id=?",(first,)).fetchone()[0]
            decision=db.execute("SELECT id FROM decisions WHERE sid=?",(sid,)).fetchone()[0]
        second=self.send("Book another trip to Tokyo")["itinerary"]["id"]
        self.assertNotIn("Mei Tan"," ".join(m["body"] for m in self.state()["messages"]))
        selected=self.client.post(f"/api/chat/trips/{first}/select",json={}).json()
        self.assertEqual(selected["itinerary"]["id"],first)
        self.assertNotIn("Tokyo"," ".join(m["body"] for m in selected["messages"]))
        other=TestClient(self.app);other.post("/api/chat/session")
        self.assertEqual(other.delete(f"/api/chat/trips/{first}").status_code,409)
        result=self.client.delete(f"/api/chat/trips/{first}")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["itinerary"]["id"],second)
        with self.store.tx(False) as db:
            for table,column,value in (("accounts","sid",sid),("decisions","sid",sid),("grants","sid",sid),("requests","sid",sid),("web_messages","trip",first),("web_turns","trip",first),("web_sales","trip",first),("events","decision",decision),("events","decision",first)):
                self.assertEqual(db.execute(f"SELECT count(*) FROM {table} WHERE {column}=?",(value,)).fetchone()[0],0,table)
        self.assertEqual(self.client.post(f"/api/chat/trips/{first}/select",json={}).status_code,409)

    def test_name_is_not_a_destination_and_cabin_is_requested(self):
        trip=self.send("Under Raj")["itinerary"]
        self.assertEqual(trip["lead_name"],"Raj")
        self.assertIsNone(trip["destination"])
        self.send(f"From Singapore to Toronto departing {self.start} returning {self.end} for two people")
        self.assertIn("cabin",self.state()["messages"][-1]["body"])
        self.assertEqual(self.send("Business class")["itinerary"]["cabin"],"BUSINESS")
        self.assertEqual(self.state()["itinerary"]["queued_requests"],0)

    def test_bare_new_booking_never_searches_for_another(self):
        class NoSearch:
            def enrich(self,draft):raise AssertionError('A blank new-trip command must not search')
        self.app.state.chat.travel=NoSearch()
        result=self.send('Book another trip')
        self.assertIsNone(result['itinerary']['destination'])
        self.assertIsNone(result['itinerary']['origin'])
        self.assertEqual(result['messages'][-1]['body'],'Which city are you leaving from?')

    def test_singapore_montreal_country_context_in_full_chat(self):
        from app.web_travel_search import WebTravelSearch
        def respond(request):
            if request.url.host=='en.wikipedia.org':return httpx.Response(200,json={'query':{'pages':{}}})
            name=request.url.params['name']
            base=dict(latitude=1,longitude=2,feature_code='PPL')
            rows=([dict(base,id=1,name='Singapore',country='Singapore',country_code='SG',feature_code='PPLC'),dict(base,id=2,name='Singapore',country='South Africa',country_code='ZA')] if name=='Singapore' else [dict(base,id=3,name='Montreal',country='Canada',country_code='CA',admin1='Quebec'),dict(base,id=4,name='Montréal',country='France',country_code='FR')])
            return httpx.Response(200,json={'results':rows})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            self.app.state.chat.travel=WebTravelSearch({},client)
            self.send('Book another trip')
            result=self.send('Leaving from Singapore. Destination: Montreal, Canada')
            self.assertEqual(result['itinerary']['origin'],'Singapore')
            self.assertEqual(result['itinerary']['destination'],'Montreal, Canada')
            self.assertEqual(result['messages'][-1]['body'],'What date would you like to leave? You can say “12 March 2027”.')
