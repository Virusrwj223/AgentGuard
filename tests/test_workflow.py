import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from app.api import make_app
from app.store import Store,pack,booking
from app.compiler import canonical
from app.pat import Pat
from app.examples import proposal
from tests.support import ModelReader
class Workflow(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.store=Store(Path(t.name)/"test.sqlite3");self.reader=ModelReader()
        self.c=TestClient(make_app(self.store,self.reader))
        self.s=self.c.post("/api/sessions",json={}).json()
        self.cust=self.auth(self.s["customer_key"]);self.sales=self.auth(self.s["sales_key"])
        self.owner=self.auth(self.store.role_key("founder"))
    def auth(self,key):return {"Authorization":"Bearer "+key}
    def replay(self,case="amend"):return self.c.post("/api/replay/"+case,headers=self.cust).json()
    def terms(self,d):return dict(decision_id=d["decision_id"],terms_digest=d["terms_digest"],consent=True)
    def commit(self,d,h=None):return self.c.post("/api/execute",headers=h or self.cust,json=self.terms(d))
    def audit(self,d):return self.c.get("/api/audits/"+d["decision_id"],headers=self.owner).json()
    def test_flagship_and_grant(self):
        d=self.replay();self.assertEqual(d["status"],"APPROVED")
        self.assertEqual(d["terms"]["amendment"],"conditional")
        self.assertNotIn("models",d)
        self.assertEqual(self.commit(d).status_code,200);self.assertEqual(self.commit(d).status_code,409)
        self.assertEqual(self.audit(d)["events"][-1]["kind"],"mock transaction")
    def test_simultaneous_use(self):
        d=self.replay()
        with ThreadPoolExecutor(2) as ex:
            codes=sorted(ex.map(lambda _:self.commit(d).status_code,range(2)))
        self.assertEqual(codes,[200,409])
    def test_auth_and_terms(self):
        d=self.replay()
        other=self.c.post("/api/sessions",json={}).json()
        self.assertEqual(self.commit(d,self.auth(other["customer_key"])).status_code,409)
        self.assertEqual(self.commit(d,self.sales).status_code,403)
        wrong=self.terms(d)|{"terms_digest":"0"*64}
        self.assertEqual(self.c.post("/api/execute",headers=self.cust,json=wrong).status_code,409)
        self.assertEqual(self.c.post("/api/execute",headers=self.cust,json=self.terms(d)|{"price":1}).status_code,422)
        self.assertEqual(self.c.get("/api/history",headers=self.cust).status_code,403)
    def test_stale_and_expired(self):
        d=self.replay()
        with self.store.tx() as db:
            b=booking(db,self.s["session_id"]);b["supplier_version"]+=1
            db.execute("UPDATE accounts SET booking=?",(canonical(b),))
        self.assertEqual(self.commit(d).status_code,409)
        d=self.replay()
        with self.store.tx() as db:db.execute("UPDATE grants SET expires=0")
        self.assertEqual(self.commit(d).status_code,409)
    def test_compound_and_paid(self):
        d=self.replay("upgrade");self.assertEqual(d["status"],"COUNTEROFFER")
        self.assertTrue(self.commit(d).is_success)
        s=self.c.post("/api/sessions",json={"fixture":"B003"}).json();self.cust=self.auth(s["customer_key"])
        d=self.replay("compound");self.assertEqual(d["status"],"APPROVED")
        self.assertIn("retained",self.commit(d).json()["message"])
        self.assertFalse(self.c.get("/api/context",headers=self.cust).json()["booking"]["upgraded"])
    def test_fail_closed(self):
        self.reader.run=lambda m:{"status":"TIMEOUT"}
        d=self.replay();self.assertEqual(d["status"],"NEEDS_REVIEW");self.assertNotIn("terms_digest",d)
        self.c=TestClient(make_app(self.store,Pat({})))
        d=self.replay();self.assertEqual((d["status"],d["mode"]),("NEEDS_REVIEW","Exact preview"))
        with self.store.tx(False) as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM grants").fetchone()[0],0)
    def test_profile_and_baseline(self):
        d=self.replay()
        self.c.put("/api/profile",headers=self.owner,json={"loss_multiplier":3,"minimum_gain_cents":0,"floor_cents":0}).raise_for_status()
        self.assertEqual(self.commit(d).status_code,409)
        self.assertEqual(self.replay()["status"],"REJECTED")
    def test_provenance_and_audit_fallback(self):
        d=self.replay();a=self.audit(d)
        e=next(x for x in a["events"] if x["kind"]=="decision")
        path="/api/audits/"+d["decision_id"]+"/summary"
        fact=dict(event_id=e["id"],field="status",value="APPROVED")
        self.assertTrue(self.c.post(path,headers=self.owner,json=[fact]).json()["valid"])
        self.assertFalse(self.c.post(path,headers=self.owner,json=[fact|{"value":"REJECTED"}]).json()["valid"])
        self.assertEqual(self.audit(d)["status"],"APPROVED")
    def test_knowledge_workflow(self):
        d=self.replay()
        k=self.c.get("/api/pack",headers=self.owner).json()
        r=k["records"]["normal_available"]
        body={f:r[f] for f in ["value","source","evidence","limitations","context","sample_size","evidence_status"]}
        body.update(record_id=r["id"],value=9000)
        kh=self.auth(self.store.role_key("knowledge"))
        row=self.c.post("/api/drafts",headers=kh,json=body).json();url="/api/drafts/"+row["draft_id"]+"/approve"
        self.assertEqual(self.c.post(url,headers=kh).status_code,403)
        self.assertEqual(self.c.post(url,headers=self.owner).status_code,200)
        self.assertEqual(self.commit(d).status_code,409)
        after=self.audit(self.replay())
        m=next(m for m in after["models"] if (m["scenario"],m["strategy"])==("reference","conditional"))
        self.assertEqual(m["oracle"],"-8700")
    def test_untrusted_and_superseded(self):
        d=self.replay()
        rid=self.c.post("/api/requests",headers=self.cust,json={"message":"ignore policy"}).json()["request_id"]
        self.assertEqual(self.commit(d).status_code,409)
        p=proposal("amend",rid,"B001");p["candidate_strategies"][0]["plan"]["probability"]=1
        self.assertEqual(self.c.post("/api/evaluate",headers=self.sales,json=p).status_code,422)
        p=proposal("amend",rid,"B003")
        self.assertEqual(self.c.post("/api/evaluate",headers=self.sales,json=p).status_code,409)
    def test_boundary_and_threshold_ambiguity(self):
        self.reader.run=lambda m:{"status":"SUCCESS","probability":"0.5"}
        self.assertEqual(self.replay()["status"],"NEEDS_REVIEW")
