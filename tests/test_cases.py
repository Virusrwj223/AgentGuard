"""Labelled downstream cases. Natural-language parsing is deliberately outside this test."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.api import make_app
from app.examples import proposal
from app.store import Store,ROOT,booking,pack
from app.compiler import canonical
from tests.support import ModelReader

def suite():
    rows=[]
    for msg,fixture,case,mutation,expected in json.loads((ROOT/"demo/cases.json").read_text()):
        with tempfile.TemporaryDirectory() as folder:
            st=Store(Path(folder)/"cases.db");solver=ModelReader()
            c=TestClient(make_app(st,solver))
            session=c.post("/api/sessions",json={"fixture":fixture}).json()
            h={"Authorization":"Bearer "+session["customer_key"]};s={"Authorization":"Bearer "+session["sales_key"]}
            rid=c.post("/api/requests",headers=h,json={"message":msg}).json()["request_id"]
            ir=proposal(case,rid,fixture)
            if mutation=="injected_field":ir["minimum_gain_cents"]=-5000
            if mutation=="wrong_booking":ir["booking_id"]="B003"
            if mutation=="negative_sale":
                ir["goal"]["discount_cents"]=4000;ir["candidate_strategies"][0]["plan"]["discount_cents"]=4000
            if mutation in ("unknown_policy","unsupported_slot","missing_probability"):
                with st.tx() as db:
                    if mutation=="missing_probability":
                        k=pack(db);k["version"]+=1;k["records"].pop("normal_available");db.execute("INSERT INTO packs VALUES(?,?)",(k["version"],canonical(k)))
                    else:
                        b=booking(db,session["session_id"])
                        if mutation=="unknown_policy":b["policy"]="unknown"
                        else:
                            ir["goal"]["days"]=60
                            for x in ir["candidate_strategies"]:
                                op=x["plan"]
                                (op["steps"][0] if op["op"]=="SEQUENCE" else op)["days"]=60
                        db.execute("UPDATE accounts SET booking=?",(canonical(b),))
            if mutation=="solver_timeout":solver.run=lambda _:dict(status="TIMEOUT")
            start=time.monotonic()
            resp=c.post("/api/evaluate",headers=s,json=ir)
            status={422:"INVALID",409:"BLOCKED"}.get(resp.status_code,resp.json().get("status"))
            if mutation=="stale_supplier":
                with st.tx() as db:
                    b=booking(db,session["session_id"]);b["supplier_version"]+=1
                    db.execute("UPDATE accounts SET booking=?",(canonical(b),))
                d=resp.json()
                status="STALE_BLOCKED" if c.post("/api/execute",headers=h,json=dict(decision_id=d["decision_id"],terms_digest=d["terms_digest"],consent=True)).status_code==409 else "FAILED"
            rows.append(dict(request=msg,expected=expected,actual=status,elapsed_ms=round((time.monotonic()-start)*1000,2)))
    return rows
class Cases(unittest.TestCase):
    def test_labelled_plans(self):
        for r in suite():
            with self.subTest(request=r["request"]):self.assertEqual(r["expected"],r["actual"])
