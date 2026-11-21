"""Decision comparison and the only booking writer."""
import json
import time
from copy import deepcopy
from contextlib import nullcontext
from datetime import date, timedelta
from fractions import Fraction as F
from .compiler import Compiler, QUERY, Review, canonical, check, digest, emit
from .contracts import Keep, Strategy
from .pat import interval
from .itinerary_revision import describe_revision, prepare_revision
from .trip_intake import SEAT_PREFERENCES, booking_context, inventory_slots, money
from .store import booking, event, lookup, now, pack, uid


def verify_distribution(pat, graph, multiplier, paths, exact):
    """Check each non-dominant terminal outcome when one wide-range probability rounds too much."""
    masses = {}
    for path in paths:
        value = path["utility"]
        masses[value] = masses.get(value, F(0)) + F(path["p"])
    check(1 < len(masses) <= 8, "Too many distinct outcomes for distribution verification")
    base = max(masses, key=lambda value: (masses[value], value))
    decoded = F(base)
    probes = []
    for value in sorted(masses):
        if value == base:
            continue
        probe = deepcopy(graph)
        for path in paths:
            probe.nodes[path["node"]].value = int(path["utility"] == value)
        model = emit(probe, 1, 0, 1)
        result = pat.run(model)
        probes.append(dict(value=value, model_hash=digest(model), expected_probability=str(masses[value]), runner=result))
        check(result["status"] == "SUCCESS", "Outcome probability verification failed")
        measured = F(result["probability"])
        check(abs(measured - masses[value]) <= F(1, 100000), "Outcome probability disagrees with exact oracle")
        decoded += (value - base) * measured
    check(abs(decoded - exact) <= F(1, 20), "Insufficient PAT precision after outcome verification")
    return (min(decoded, exact), max(decoded, exact)), probes

def terms_for(c):
    t=dict(c.terms)
    ops=c.ops
    t.update(currency="SGD",booking_id=c.booking["booking_id"],package=c.booking["package"])
    if "revision" in t and c.goal.revision_charge_limit_cents is not None:
        t["requested_limit"] = c.goal.revision_charge_limit_cents
    if "days" in t:
        t["requested_date"]=c.booking["slots"][str(t["days"])]
        if c.booking.get("return_date"):
            t["revised_return"] = str(date.fromisoformat(c.booking["return_date"]) + timedelta(days=t["days"]))
        t["amendment"]="conditional" if "REQUEST_AMENDMENT" in ops else "guaranteed"
    if "discount_cents" in t:
        t["price_cents"]=c.number("sale_price","SGD cents")-t["discount_cents"]
    if "credit_cents" in t:
        t["revised_total_cents"] = c.booking["paid_cents"] - t["credit_cents"]
    return t

def wording(t):
    parts=[]
    if "revision" in t:
        revision = t["revision"]
        parts.append(describe_revision(revision) + ".")
        delta = revision["customer_delta"]
        if revision["inventory"]:
            parts.append("All changes depend on one simulated supplier hold.")
            fare = revision["fields"]["fare"]
            parts.append(f"Fare source: {fare.get('source','travel provider')}.")
        if revision.get("credit_cents"):
            parts.append(f"Includes an additional credit of {money(revision['credit_cents'])}.")
        if revision.get("dates"):
            parts.append(f"Trip dates: {revision['dates'][0]} to {revision['dates'][1]}.")
        if delta:
            parts.append(f"{'Additional charge' if delta>0 else 'Price reduction'}: {money(abs(delta))}.")
        if t.get("requested_limit") is not None and delta > t["requested_limit"]:
            parts.append(f"This exceeds your requested limit of {money(t['requested_limit'])}.")
        parts.append(f"Revised total: {money(revision['total_cents'])}.")
    if "seat_preference" in t:
        preference = SEAT_PREFERENCES[t["seat_preference"]]
        parts.append(f"Record this seat preference on the demo booking: {preference}. Seats remain unassigned.")
    if "amendment" in t:
        if t["amendment"]=="conditional":
            parts.append(f"Request {t['requested_date']}. Change the date only if the supplier confirms a binding hold. Otherwise keep the original booking.")
        else:parts.append(f"Guarantee the change to {t['requested_date']}.")
    if "revised_return" in t:
        parts.append(f"The revised return is {t['revised_return']}.")
    if "credit_cents" in t:
        parts.append(f"I can offer a {money(t['credit_cents'])} credit, bringing your total to {money(t['revised_total_cents'])}.")
    if "price_cents" in t:parts.append(f"Sale price SGD {t['price_cents']/100:.2f}, including the discount.")
    if "charge_cents" in t:
        prefix="If the date changes, add" if "amendment" in t else "Add"
        parts.append(f"{prefix} the upgrade for SGD {t['charge_cents']/100:.2f}.")
    elif "amendment" in t:parts.append("No extra charge.")
    return " ".join(parts)

def public(d):
    obj={k:d[k] for k in ("decision_id","request_id","status","message","mode")}
    if d.get("terms"):
        obj.update(terms=d["terms"],terms_digest=digest(d["terms"]))
    return obj

class Service:
    def __init__(self,store,pat):
        self.store,self.pat=store,pat
    def inquiry(self,sid,msg):
        rid=uid("R")
        with self.store.tx() as db:
            db.execute("INSERT INTO requests VALUES(?,?,?,?)",(rid,sid,msg,now()))
        return rid
    def evaluate(self,sid,p,source="QwenWork structured handoff", *, decision_id=None, travel=None):
        with self.store.tx(False) as db:
            if decision_id and db.execute("SELECT id FROM decisions WHERE id=? AND sid=?", (decision_id,sid)).fetchone():
                return public(lookup(db,decision_id,sid))
            b,k=booking(db,sid),pack(db)
            r=db.execute("SELECT message FROM requests WHERE id=? AND sid=?",(p.request_id,sid)).fetchone()
            check(r and b["booking_id"]==p.booking_id,"Session, request or booking mismatch")
            current=db.execute("SELECT id FROM requests WHERE sid=? ORDER BY rowid DESC LIMIT 1",(sid,)).fetchone()
            check(current and current[0]==p.request_id,"Request superseded; reload current context")
            msg_fingerprint=digest(r[0])
        d=dict(decision_id=decision_id or uid("D"),request_id=p.request_id,mode="PAT" if self.pat.ready else "Exact preview",
               status="NEEDS_REVIEW",strategy=None,terms=None,models=[],comparison=[],proposal=p.model_dump(),
               source=source,snapshot=b,pack_version=k["version"],profile_version=k["profile_version"],profile=k["profile"],
               reason="",message="Your request is with the agency for review. Your booking is unchanged.")
        if p.needs_clarification:
            d.update(status="CLARIFY",reason="Agent flagged ambiguous terms",message="Please confirm your requested change, exact date or amount, and maximum extra charge.")
        elif p.goal.type=="unknown":
            d.update(reason="Unsupported request",
                     message="I’m not sure which part of the booking you’d like to change. Could you tell me what you’d like me to check?")
        else:
            try:
                revision = None
                if p.goal.type == "itinerary":
                    if travel is None:
                        import os
                        from .travel_search import TravelSearch
                        from .web_travel_search import WebTravelSearch
                        travel = TravelSearch() if os.getenv("AG_TRAVEL_MODE")=="amadeus" else WebTravelSearch(allow_demo_fares=True)
                    try:
                        revision = prepare_revision(b, p.goal, travel)
                    except ValueError as exc:
                        raise Review(str(exc)) from exc
                self.score(d,p,b,k,revision)
            except (Review,KeyError,TypeError,OverflowError) as err:
                d["reason"]=str(err)
        with self.store.tx() as db:
            current=db.execute("SELECT id FROM requests WHERE sid=? ORDER BY rowid DESC LIMIT 1",(sid,)).fetchone()
            if current[0]!=p.request_id or b!=booking(db,sid) or k["version"]!=pack(db)["version"]:
                d.update(status="NEEDS_REVIEW",terms=None,reason="State changed during evaluation",message="The booking or policy changed. Please request a fresh review.")
            ident=d["decision_id"]
            if decision_id and db.execute("SELECT id FROM decisions WHERE id=? AND sid=?", (ident,sid)).fetchone():
                return public(lookup(db,ident,sid))
            db.execute("INSERT INTO decisions VALUES(?,?,?,?)",(ident,sid,p.request_id,canonical(d)))
            event(db,ident,"sales","proposal received",dict(request_id=p.request_id,message_digest=msg_fingerprint,source=source,proposal=p.model_dump()))
            event(db,ident,"compiler","trusted inputs",dict(snapshot=b,pack=k["version"],profile=k["profile_version"],horizon=k["horizon_days"]))
            for m in d["models"]:
                event(db,ident,"verifier","model evaluated",{f:m[f] for f in ("strategy","scenario","model_hash","oracle","runner","assumptions","closure")})
            event(db,ident,"governance","decision",{f:d[f] for f in ("status","strategy","reason","comparison","terms","message")})
            if d["status"] in ("APPROVED","COUNTEROFFER"):
                details=dict(terms=d["terms"],model_hashes=[m["model_hash"] for m in d["models"] if m["strategy"]==d["strategy"]],snapshot=digest(b),pack=k["version"],profile=k["profile_version"],strategy=d["strategy"],request_id=p.request_id)
                expires = int(time.time()) + 300
                if d["terms"].get("revision", {}).get("inventory"):
                    expires = min(expires, int(d["terms"]["revision"]["fields"]["fare"]["expires"]))
                db.execute("INSERT INTO grants VALUES(?,?,?,?,0)",(ident,sid,canonical(details),expires))
                event(db,ident,"executor","authorization issued",dict(strategy=d["strategy"],ttl_seconds=max(0,expires-int(time.time())),terms_digest=digest(d["terms"])))
        return public(d)
    def score(self,d,p,b,k,revision=None):
        policy=k["profile"]
        strategies=[Strategy(strategy_id="baseline",plan=Keep(op="KEEP_BOOKING")),*p.candidate_strategies]
        seen=set()
        ranked=[]
        for s in strategies:
            sig=digest(s.plan.model_dump())
            if sig in seen:continue
            seen.add(sig)
            intervals=[]
            previews=[]
            terms=None
            for scenario in k["scenarios"][b["context"]]:
                c=Compiler(k,b,p.goal,scenario,s.strategy_id=="baseline",revision)
                g=c.compile(s.plan)
                terms=terms_for(c)
                mult=policy["loss_multiplier"]
                paths=g.paths(mult)
                lo=min(x["utility"] for x in paths)
                hi=max(x["utility"] for x in paths)
                exact=g.oracle(mult)
                check(sum(F(x["contribution"]) for x in paths)==exact,"Path/DP disagreement")
                model=emit(g,mult,lo,hi)
                run=self.pat.run(model) if self.pat.ready else dict(status="UNAVAILABLE",diagnostic=self.pat.detail)
                manifest=dict(compiler="agentguard-1",strategy=s.strategy_id,scenario=scenario,plan=s.plan.model_dump(),goal=p.goal.model_dump(),snapshot_hash=digest(b),pack_hash=digest(k),bounds=[lo,hi],
                              assumptions=sorted(c.used),closure=sorted(c.closure),operations=sorted(c.ops),query=QUERY,graph=g.export(),pcsp=model)
                m=manifest | dict(model_hash=digest(manifest),paths=paths,oracle=str(exact),expected_value=str(sum(F(x["p"])*x["value"] for x in paths)),runner=run,interval=None)
                d["models"].append(m)
                previews.append(exact)
                if self.pat.ready:
                    try:
                        low,high=interval(run,lo,hi,exact)
                        intervals.append((low,high))
                        m["interval"]=[str(low),str(high)]
                    except Review as err:
                        expected_probability=(exact-lo)/(hi-lo) if hi != lo else F(1,2)
                        if (run["status"] == "SUCCESS" and hi != lo
                            and abs(F(run["probability"])-expected_probability) <= F(1,100000)):
                            try:
                                bounds, probes = verify_distribution(self.pat,g,mult,paths,exact)
                                run["primary"] = dict(run)
                                run["probes"] = probes
                                run["status"] = "SUCCESS"
                                run["verification"] = "terminal outcome probabilities"
                                low,high=bounds
                                intervals.append(bounds)
                                m["interval"]=[str(low),str(high)]
                                continue
                            except Review as probe_error:
                                run["diagnostic"] = str(probe_error)
                        else:
                            run["diagnostic"]=str(err)
                        if run["status"]=="SUCCESS":run["status"]="NUMERIC_UNCERTAIN"
            check(previews,"No approved scenarios")
            row=dict(strategy=s.strategy_id,worst_oracle=str(min(previews)),interval=None)
            d["comparison"].append(row)
            if len(intervals)==len(previews):
                low=min(a for a,_ in intervals)
                high=min(b for _,b in intervals)
                row["interval"]=[str(low),str(high)]
                counter=terms.get("charge_cents",0)>p.goal.max_extra_charge_cents or (p.goal.type=="sale" and terms.get("discount_cents",0)<p.goal.discount_cents)
                if p.goal.type == "itinerary" and not c.baseline and p.goal.revision_charge_limit_cents is not None:
                    counter = revision["customer_delta"] > p.goal.revision_charge_limit_cents
                if p.goal.type == "credit" and not c.baseline:
                    counter = terms["credit_cents"] < p.goal.discount_cents
                ranked.append((s.strategy_id,low,high,terms,counter))
        if not self.pat.ready:
            raise Review(self.pat.detail+"; oracle preview only")
        check(len(ranked)==len(d["comparison"]),"Solver failure or precision uncertainty; inspect model diagnostics")
        baseline=ranked[0]
        threshold=baseline[2]+policy["minimum_gain_cents"]
        floor=policy["floor_cents"]
        if floor is not None:threshold=max(threshold,floor)
        choices=[r for r in ranked[1:] if r[1]>=threshold]
        if not choices:
            lower_threshold=baseline[1]+policy["minimum_gain_cents"]
            check(not any(r[2]>=max(lower_threshold,floor if floor is not None else lower_threshold) for r in ranked[1:]),"Threshold overlaps numerical uncertainty")
            message = "I can’t offer that change on those terms. If you have some flexibility, I can check a different option."
            if p.goal.type == "credit":
                previous = b.get("credit_cents", 0)
                message = ("I can’t offer a further credit on this booking."
                           if p.goal.discount_cents == 1 else "I can’t offer that credit on this booking.")
                if previous:
                    message += f" You’ve already received {money(previous)} in credit."
                if b.get("self_service"):
                    message += " If your travel plans are flexible, I can check a different itinerary for you."
            d.update(status="REJECTED", reason="All candidate scores fall below the required utility", message=message)
            return
        # Prefer fulfilling the request to soliciting different terms. Rank within each group.
        best=sorted(choices,key=lambda r:(r[4],-r[1],r[0]))[0]
        for other in ranked[1:]:
            if other[0]!=best[0] and other[4]<=best[4] and other[2]>=threshold:
                check(best[1]>other[2] or (best[1]==best[2]==other[1]==other[2] and best[0]<other[0]) or (not best[4] and other[4]),"Alternative ranking unresolved")
        status="COUNTEROFFER" if best[4] else "APPROVED"
        message = wording(best[3])
        if status == "COUNTEROFFER":
            if p.goal.type == "credit":
                requested = p.goal.discount_cents
                unavailable = ("I can’t waive the full fare." if requested == b["paid_cents"]
                               else f"I can’t offer the full {money(requested)} credit.")
                message = unavailable + " " + message
            else:
                message = "I can offer these alternative terms. " + message
        d.update(status=status,strategy=best[0],terms=best[3],reason=f"Conservative utility {best[1]} clears {threshold} utility cents; compared within consent class.",message=message)
    def execute(self,sid,confirm, *, transaction=None):
        with (self.store.tx() if transaction is None else nullcontext(transaction)) as db:
            d=lookup(db,confirm.decision_id,sid)
            row=db.execute("SELECT * FROM grants WHERE decision=? AND sid=?",(confirm.decision_id,sid)).fetchone()
            check(row and not row["used"] and row["expires"]>time.time(),"Authorization missing, expired or consumed")
            grant=json.loads(row["details"])
            t=grant["terms"]
            b,k=booking(db,sid),pack(db)
            latest=db.execute("SELECT id FROM requests WHERE sid=? ORDER BY rowid DESC LIMIT 1",(sid,)).fetchone()[0]
            latest_decision=db.execute("SELECT id FROM decisions WHERE sid=? AND request=? ORDER BY rowid DESC LIMIT 1",(sid,latest)).fetchone()
            check(latest_decision and latest_decision[0]==confirm.decision_id and latest==grant["request_id"],"Superseded authorization")
            check(grant["snapshot"]==digest(b) and grant["pack"]==k["version"] and grant["profile"]==k["profile_version"],"Stale snapshot or knowledge")
            check(confirm.terms_digest==digest(t) and d["terms"]==t and d["mode"]=="PAT","Terms do not match authorization")
            for m in d["models"]:
                if m["strategy"]==grant["strategy"]:
                    check(m["model_hash"] in grant["model_hashes"] and all(k["records"][i]["expires"]>=date.today().isoformat() for i in m["assumptions"]),"Stale model evidence")
            result=[]
            changed=True
            if "revision" in t:
                revision = t["revision"]
                fields = revision["fields"]
                if revision["inventory"]:
                    check(fields["fare"]["expires"] > time.time(), "Revised fare expired")
                    observed = b["supplier_mock"]
                    check(observed in ("held","unavailable"), "Unknown supplier outcome")
                    event(db,d["decision_id"],"supplier mock","supplier observed",{"outcome": observed})
                    changed = observed == "held"
                if changed:
                    b.update(fields)
                    if revision["credit_cents"]:
                        b["credit_cents"] = b.get("credit_cents",0) + revision["credit_cents"]
                    b.update(package=f"{b['origin']} to {b['destination']}", paid_cents=revision["total_cents"],
                             list_cents=revision["list_cents"])
                    if revision["inventory"]:
                        b.update(flight_details_stale=False, slots=inventory_slots(b["departure"]),
                                 context=booking_context((date.fromisoformat(b["departure"])-date.fromisoformat(b["as_of"])).days))
                    result.append("Reservation updated. " + describe_revision(revision) + f". Total {money(b['paid_cents'])}.")
                else:
                    result.append("Supplier unavailable. Your original itinerary and price were retained.")
            if "amendment" in t:
                observed=b["supplier_mock"]
                check(observed in ("held","unavailable"),"Supplier response needs re-evaluation")
                event(db,d["decision_id"],"supplier mock","supplier observed",dict(outcome=observed,supplier_version=b["supplier_version"],slot=t["requested_date"]))
                changed=observed=="held"
                if changed:
                    check(b["slots"].get(str(t["days"]))==t["requested_date"],"Slot changed")
                    b["departure"]=t["requested_date"]
                    if b.get("return_date"):
                        b["return_date"] = (date.fromisoformat(b["return_date"]) + timedelta(days=t["days"])).isoformat()
                    if b.get("fare"):
                        b["flight_details_stale"] = True
                    # New slots require a refreshed trusted inventory import.
                    b["slots"]={}
                    if b.get("self_service"):
                        b["slots"] = inventory_slots(b["departure"])
                        lead = (date.fromisoformat(b["departure"]) - date.fromisoformat(b["as_of"])).days
                        b["context"] = booking_context(lead)
                    result.append("Supplier hold verified. Date changed to "+b["departure"]+".")
                elif t["amendment"]=="conditional":result.append("Supplier unavailable. Original booking retained.")
                else:
                    b["recovery_open"]=True
                    result.append("Guaranteed service unavailable. Recovery case opened.")
            if "price_cents" in t:
                b.update(kind="booked",paid_cents=t["price_cents"])
                result.append(f"Simulated sale at SGD {t['price_cents']/100:.2f}.")
            if "credit_cents" in t:
                b.update(paid_cents=t["revised_total_cents"], credit_cents=b.get("credit_cents",0)+t["credit_cents"])
                result.append(f"Credit applied. Your new demo total is SGD {b['paid_cents']/100:.2f}.")
            if "seat_preference" in t:
                b["seat_preference"] = t["seat_preference"]
                result.append(f"Seat preference recorded: {SEAT_PREFERENCES[t['seat_preference']]}. Seats remain unassigned.")
            if "charge_cents" in t and changed:
                b.update(upgraded=True,upgrade_charge_cents=t["charge_cents"])
                if b.get("self_service"):
                    b["paid_cents"] += t["charge_cents"]
                result.append(f"Upgrade recorded at SGD {t['charge_cents']/100:.2f}.")
            b["version"]+=1
            db.execute("UPDATE accounts SET booking=? WHERE sid=?",(canonical(b),sid))
            db.execute("UPDATE grants SET used=1 WHERE decision=?",(d["decision_id"],))
            output=dict(message=" ".join(result),booking_version=b["version"])
            event(db,d["decision_id"],"executor","mock transaction",output)
            return output
