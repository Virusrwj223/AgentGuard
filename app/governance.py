"""Decision comparison and the only booking writer."""
import json
import time
from datetime import date
from fractions import Fraction as F
from .compiler import Compiler, QUERY, Review, canonical, check, digest, emit
from .contracts import Keep, Strategy
from .pat import interval
from .store import booking, event, lookup, now, pack, uid

def terms_for(c):
    t=dict(c.terms)
    ops=c.ops
    t.update(currency="SGD",booking_id=c.booking["booking_id"],package=c.booking["package"])
    if "days" in t:
        t["requested_date"]=c.booking["slots"][str(t["days"])]
        t["amendment"]="conditional" if "REQUEST_AMENDMENT" in ops else "guaranteed"
    if "discount_cents" in t:
        t["price_cents"]=c.number("sale_price","SGD cents")-t["discount_cents"]
    return t

def wording(t):
    parts=[]
    if "amendment" in t:
        if t["amendment"]=="conditional":
            parts.append(f"Request {t['requested_date']}. Change the date only if the supplier confirms a binding hold. Otherwise keep the original booking.")
        else:parts.append(f"Guarantee the change to {t['requested_date']}.")
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
    def evaluate(self,sid,p,source="QwenWork structured handoff"):
        with self.store.tx(False) as db:
            b,k=booking(db,sid),pack(db)
            r=db.execute("SELECT message FROM requests WHERE id=? AND sid=?",(p.request_id,sid)).fetchone()
            check(r and b["booking_id"]==p.booking_id,"Session, request or booking mismatch")
            current=db.execute("SELECT id FROM requests WHERE sid=? ORDER BY rowid DESC LIMIT 1",(sid,)).fetchone()
            check(current and current[0]==p.request_id,"Request superseded; reload current context")
            msg_fingerprint=digest(r[0])
        d=dict(decision_id=uid("D"),request_id=p.request_id,mode="PAT" if self.pat.ready else "Exact preview",
               status="NEEDS_REVIEW",strategy=None,terms=None,models=[],comparison=[],proposal=p.model_dump(),
               source=source,snapshot=b,pack_version=k["version"],profile_version=k["profile_version"],profile=k["profile"],
               reason="",message="Your request is with the agency for review. Your booking is unchanged.")
        if p.needs_clarification:
            d.update(status="CLARIFY",reason="Agent flagged ambiguous terms",message="Please confirm your requested change, exact date or amount, and maximum extra charge.")
        elif p.goal.type=="unknown":
            d["reason"]="Unsupported request"
        else:
            try:
                self.score(d,p,b,k)
            except (Review,KeyError,TypeError,OverflowError) as err:
                d["reason"]=str(err)
        with self.store.tx() as db:
            current=db.execute("SELECT id FROM requests WHERE sid=? ORDER BY rowid DESC LIMIT 1",(sid,)).fetchone()
            if current[0]!=p.request_id or b!=booking(db,sid) or k["version"]!=pack(db)["version"]:
                d.update(status="NEEDS_REVIEW",terms=None,reason="State changed during evaluation",message="The booking or policy changed. Please request a fresh review.")
            ident=d["decision_id"]
            db.execute("INSERT INTO decisions VALUES(?,?,?,?)",(ident,sid,p.request_id,canonical(d)))
            event(db,ident,"sales","proposal received",dict(request_id=p.request_id,message_digest=msg_fingerprint,source=source,proposal=p.model_dump()))
            event(db,ident,"compiler","trusted inputs",dict(snapshot=b,pack=k["version"],profile=k["profile_version"],horizon=k["horizon_days"]))
            for m in d["models"]:
                event(db,ident,"verifier","model evaluated",{f:m[f] for f in ("strategy","scenario","model_hash","oracle","runner","assumptions","closure")})
            event(db,ident,"governance","decision",{f:d[f] for f in ("status","strategy","reason","comparison","terms","message")})
            if d["status"] in ("APPROVED","COUNTEROFFER"):
                details=dict(terms=d["terms"],model_hashes=[m["model_hash"] for m in d["models"] if m["strategy"]==d["strategy"]],snapshot=digest(b),pack=k["version"],profile=k["profile_version"],strategy=d["strategy"],request_id=p.request_id)
                db.execute("INSERT INTO grants VALUES(?,?,?,?,0)",(ident,sid,canonical(details),int(time.time())+300))
                event(db,ident,"executor","authorization issued",dict(strategy=d["strategy"],ttl_seconds=300,terms_digest=digest(d["terms"])))
        return public(d)
    def score(self,d,p,b,k):
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
                c=Compiler(k,b,p.goal,scenario,s.strategy_id=="baseline")
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
            d.update(status="REJECTED",reason="All candidate scores fall below the required utility",message="We cannot offer the requested terms. Your current arrangement is unchanged.")
            return
        # Prefer fulfilling the request to soliciting different terms. Rank within each group.
        best=sorted(choices,key=lambda r:(r[4],-r[1],r[0]))[0]
        for other in ranked[1:]:
            if other[0]!=best[0] and other[4]<=best[4] and other[2]>=threshold:
                check(best[1]>other[2] or (best[1]==best[2]==other[1]==other[2] and best[0]<other[0]) or (not best[4] and other[4]),"Alternative ranking unresolved")
        status="COUNTEROFFER" if best[4] else "APPROVED"
        d.update(status=status,strategy=best[0],terms=best[3],reason=f"Conservative utility {best[1]} clears {threshold} utility cents; compared within consent class.",message=wording(best[3]))
    def execute(self,sid,confirm):
        with self.store.tx() as db:
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
            if "amendment" in t:
                observed=b["supplier_mock"]
                check(observed in ("held","unavailable"),"Supplier response needs re-evaluation")
                event(db,d["decision_id"],"supplier mock","supplier observed",dict(outcome=observed,supplier_version=b["supplier_version"],slot=t["requested_date"]))
                changed=observed=="held"
                if changed:
                    check(b["slots"].get(str(t["days"]))==t["requested_date"],"Slot changed")
                    b["departure"]=t["requested_date"]
                    # New slots require a refreshed trusted inventory import.
                    b["slots"]={}
                    result.append("Supplier hold verified. Date changed to "+b["departure"]+".")
                elif t["amendment"]=="conditional":result.append("Supplier unavailable. Original booking retained.")
                else:
                    b["recovery_open"]=True
                    result.append("Guaranteed service unavailable. Recovery case opened.")
            if "price_cents" in t:
                b.update(kind="booked",paid_cents=t["price_cents"])
                result.append(f"Simulated sale at SGD {t['price_cents']/100:.2f}.")
            if "charge_cents" in t and changed:
                b.update(upgraded=True,upgrade_charge_cents=t["charge_cents"])
                result.append(f"Upgrade recorded at SGD {t['charge_cents']/100:.2f}.")
            b["version"]+=1
            db.execute("UPDATE accounts SET booking=? WHERE sid=?",(canonical(b),sid))
            db.execute("UPDATE grants SET used=1 WHERE decision=?",(d["decision_id"],))
            output=dict(message=" ".join(result),booking_version=b["version"])
            event(db,d["decision_id"],"executor","mock transaction",output)
            return output
