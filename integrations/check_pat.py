"""Release check using only the operator's actual, fingerprinted PAT executable."""
import json
import sys
import tempfile
from datetime import date, timedelta
from fractions import Fraction as F
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.compiler import Graph, canonical, emit
from app.pat import interval
from app.contracts import Consent, Goal
from app.governance import Service
from app.sales_desk import make_proposal
from app.store import ROOT, Store, booking, lookup
from app.trip_intake import create_booking


def seat_change_check(solver):
    """Run the real solver through the current policy and consent transaction."""
    today = date.today()
    departure = today + timedelta(days=30)
    draft = {
        "origin": "Singapore", "destination": "New York City", "departure": departure.isoformat(),
        "return_date": (departure + timedelta(days=7)).isoformat(), "travellers": 1,
        "lead_name": "Demo traveller", "cabin": "ECONOMY", "seat_preference": "WINDOW",
        "fare": {"amount_cents": 100000},
    }
    with tempfile.TemporaryDirectory() as temporary:
        store = Store(Path(temporary) / "test.sqlite")
        service = Service(store, solver)
        sid = store.session("B001")["session_id"]
        reservation = create_booking("SeatAcceptance", draft, today)
        with store.tx() as db:
            db.execute("UPDATE accounts SET booking=? WHERE sid=?", (canonical(reservation), sid))
        request = service.inquiry(sid, "Change my seat preference to aisle")
        proposal = make_proposal(Goal(type="seat_preference", seat_preference="AISLE"), reservation, request)
        result = service.evaluate(sid, proposal, "PAT acceptance check")
        with store.tx(False) as db:
            decision = lookup(db, result["decision_id"], sid)
            prior = booking(db, sid)
        report = {
            "case": "seat preference change from proposal to consent", "passed": False,
            "status": result["status"], "models": decision["models"],
        }
        if result["status"] != "APPROVED" or result["mode"] != "PAT" or prior["seat_preference"] != "WINDOW":
            return report
        service.execute(sid, Consent(
            decision_id=result["decision_id"], terms_digest=result["terms_digest"], consent=True,
        ))
        with store.tx(False) as db:
            updated = booking(db, sid)
        report["passed"] = updated["seat_preference"] == "AISLE" and updated["paid_cents"] == prior["paid_cents"]
        return report


def peak_amendment_check(solver):
    """Compare the emitted adverse-scenario models with the independently priced paths."""
    today = date.today()
    departure = today + timedelta(days=7)
    draft = dict(origin="Singapore", destination="Montreal", lead_name="Raj", cabin="ECONOMY",
                 travellers=1, departure=str(departure), return_date=str(departure+timedelta(days=14)),
                 fare={"amount_cents":140000})
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp)/"peak.sqlite")
        service = Service(store,solver)
        sid = store.session("B001")["session_id"]
        reservation = create_booking("Montreal",draft,today)
        with store.tx() as db:
            db.execute("UPDATE accounts SET booking=? WHERE sid=?", (canonical(reservation),sid))
        rid = service.inquiry(sid,"Move trip by 2 days")
        result = service.evaluate(sid,make_proposal(Goal(type="amend",days=2),reservation,rid))
        with store.tx(False) as db:
            decision = lookup(db,result["decision_id"],sid)
    expected = {"baseline":-15000,"conditional":-11150,"guarantee":-461150}
    actual = {m["strategy"]:m for m in decision["models"] if m["scenario"]=="adverse"}
    passed = result["status"]=="APPROVED" and decision["strategy"]=="conditional" and all(
        key in actual and actual[key]["interval"] and
        F(actual[key]["interval"][0]) <= target <= F(actual[key]["interval"][1])
        for key,target in expected.items())
    return {"case":"Singapore–Montreal peak amendment", "expected_cents":expected,
            "passed":bool(passed), "models":decision["models"]}


def main():
    from app.settings import configure, configured_solver
    configure()
    solver=configured_solver()
    if not solver.ready:
        print(json.dumps({"status":"UNAVAILABLE","reason":solver.detail,"calibration":solver.calibration},indent=2))
        return 2
    out=[]
    def check_case(name,model,lo,hi,expected):
        result=solver.run(model)
        record=dict(case=name,model=model,raw_result=result,bounds=[lo,hi],expected=str(expected))
        try:
            a,b=interval(result,lo,hi,F(expected))
            record.update(passed=True,interval=[str(a),str(b)])
        except ValueError as err:record.update(passed=False,error=str(err))
        out.append(record)
    for name,values,k in [
        ("rare loss",[(".9",900),(".099",750),(".001",-999250)],1),
        ("loss aversion",[(".9",900),(".099",750),(".001",-999250)],3),
        ("likely profit",[(".99",100),(".01",-100000)],1),
        ("constant",[(1,-7)],1),
    ]:
        graph=Graph()
        for p,v in values:graph.branch(0,"outcome",F(p),delta=v)
        xs=[p["utility"] for p in graph.paths(k)];lo,hi=min(xs),max(xs)
        check_case(name,emit(graph,k,lo,hi),lo,hi,graph.oracle(k))
    sample=json.loads((ROOT/"demo/generated/flagship-audit.json").read_text())
    for m in sample["models"]:check_case(m["strategy"]+"/"+m["scenario"],m["pcsp"],*m["bounds"],m["oracle"])
    out.append(seat_change_check(solver))
    out.append(peak_amendment_check(solver))
    dest=ROOT/"instance"/"pat-acceptance.json";dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(dict(version=solver.version,sha256=solver.sha,calibration=solver.calibration,checks=out),indent=2),encoding="utf-8")
    for r in out:print("PASS" if r["passed"] else "REVIEW",r["case"])
    print(dest)
    return 0 if all(r["passed"] for r in out) else 1
if __name__=="__main__":sys.exit(main())
