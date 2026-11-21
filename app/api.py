import json
import os
import time
from pathlib import Path
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from .contracts import Start,Inquiry,Proposal,Consent,Draft,Profile,Gap,Summary,CustomerResult
from .compiler import Review,check,canonical,digest,QUERY
from .store import ROOT,Store,booking,event,lookup,now,pack,uid
from .pat import Pat
from .governance import Service,public
from .examples import CASES,proposal

def make_app(store=None,solver=None):
    starting_demo = store is None
    if starting_demo:
        store=Store(os.environ.get("AG_DB",str(ROOT/"instance"/"agentguard.sqlite3")))
    solver=solver or Pat()
    svc=Service(store,solver)
    app=FastAPI(title="AgentGuard Travel Desk",version="2.0")
    app.state.service=svc
    @app.exception_handler(Review)
    async def denied(request,exc):
        return JSONResponse({"detail":str(exc)},status_code=409)
    @app.middleware("http")
    async def hygiene(request,call_next):
        if int(request.headers.get("content-length","0") or 0)>64000:
            return JSONResponse({"detail":"Payload too large"},status_code=413)
        response=await call_next(request)
        response.headers["Cache-Control"]="no-store"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Content-Security-Policy"]="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://upload.wikimedia.org; frame-ancestors 'none'"
        return response
    def access(*roles):
        def identify(authorization:str=Header(default="")):
            try:
                scheme,_,key=authorization.partition(" ")
                if scheme!="Bearer":raise PermissionError("Bearer credential required")
                return store.identity(key,roles)
            except PermissionError as err:
                raise HTTPException(403,str(err)) from err
        return Depends(identify)
    @app.get("/")
    def home():return FileResponse(ROOT/"frontend"/"index.html")
    @app.get("/founder")
    def founder():return RedirectResponse("/",status_code=303)
    @app.get("/health")
    def health():return {"service":"AgentGuard","solver":"PAT" if solver.ready else "Exact preview","can_authorize":solver.ready}
    @app.post("/api/sessions")
    def start(body:Start):return store.session(body.fixture)
    @app.get("/api/context")
    def context(who=access("customer","sales")):
        with store.tx(False) as db:
            b=booking(db,who[1])
            row=db.execute("SELECT id,message FROM requests WHERE sid=? ORDER BY rowid DESC LIMIT 1",(who[1],)).fetchone()
        return {"booking":{k:b[k] for k in ("booking_id","package","kind","departure","currency","slots","version","upgraded","upgrade_prices_cents","origin","destination","return_date","cabin","travellers","lead_name","seat_preference","airline_preference","paid_cents") if k in b},"request":dict(row) if row else None}
    @app.get("/api/catalog")
    def catalog(who=access("sales","founder","knowledge","risk")):
        return {"schema":Proposal.model_json_schema(),"capabilities":["itinerary","seat_preference","amend","upgrade","amend_upgrade","sale","credit"],"currency":"SGD","horizon_days":90,"clarify":"Ask for exact units, dates and consent limits when ambiguous."}
    @app.post("/api/requests")
    def inquiry(body:Inquiry,who=access("customer")):
        return {"request_id":svc.inquiry(who[1],body.message)}
    @app.post("/api/evaluate",response_model=CustomerResult,response_model_exclude_none=True)
    def evaluate(body:Proposal,who=access("sales")):
        return svc.evaluate(who[1],body,"External structured plan; platform provenance requires session evidence")
    @app.post("/api/replay/{case}")
    def replay(case:str,who=access("customer")):
        check(case in CASES,"Unknown replay")
        with store.tx(False) as db:b=booking(db,who[1])
        msg=CASES[case].replace("30 days","14 days") if b["booking_id"]=="B003" else CASES[case]
        rid=svc.inquiry(who[1],msg)
        p=Proposal.model_validate(proposal(case,rid,b["booking_id"]))
        return svc.evaluate(who[1],p,"Scripted regression fixture")
    @app.get("/api/latest")
    def latest(who=access("customer","sales")):
        with store.tx(False) as db:
            r=db.execute("SELECT d.payload FROM decisions d WHERE d.sid=? AND d.request=(SELECT id FROM requests WHERE sid=? ORDER BY rowid DESC LIMIT 1) ORDER BY d.rowid DESC LIMIT 1",(who[1],who[1])).fetchone()
            if not r:return None
            d=json.loads(r[0])
            response=public(d)
            if not d.get("terms"):return response
            grant=db.execute("SELECT * FROM grants WHERE decision=?",(d["decision_id"],)).fetchone()
            if grant and grant["used"]:
                execution=db.execute("SELECT data FROM events WHERE decision=? AND kind='mock transaction' ORDER BY id DESC LIMIT 1",(d["decision_id"],)).fetchone()
                if execution:
                    response.update(execution="COMPLETED",message=json.loads(execution[0])["message"])
                else:
                    response.update(execution="DECLINED",message="You declined this offer. Your booking keeps its current terms.")
            elif grant and grant["expires"]>time.time():
                bound=json.loads(grant["details"])
                if bound["snapshot"]==digest(booking(db,who[1])) and bound["pack"]==pack(db)["version"]:return response
                response.update(status="NEEDS_REVIEW",message="Booking or policy changed. Please request a fresh review.")
            else:
                response.update(status="NEEDS_REVIEW",message="This offer expired. Please request a fresh review.")
            response.pop("terms",None);response.pop("terms_digest",None)
            return response
    @app.post("/api/execute")
    def execute(body:Consent,who=access("customer")):return svc.execute(who[1],body)
    @app.get("/api/history")
    def history(who=access("founder","audit")):
        with store.tx(False) as db:
            return [dict(decision_id=r["id"],**{key:json.loads(r["payload"])[key] for key in ("status","mode","strategy","source","request_id")}) for r in db.execute("SELECT * FROM decisions ORDER BY rowid DESC LIMIT 100")]
    @app.get("/api/audits/{ident}")
    def audit(ident:str,who=access("founder","audit")):
        with store.tx(False) as db:
            d=lookup(db,ident)
            original=json.loads(db.execute("SELECT body FROM packs WHERE version=?",(d["pack_version"],)).fetchone()[0])
            d["evidence"]={i:original["records"][i] for m in d["models"] for i in m["assumptions"]}
            d["events"]=[dict(r)|{"data":json.loads(r["data"])} for r in db.execute("SELECT * FROM events WHERE decision=? ORDER BY id",(ident,))]
            return d
    @app.post("/api/audits/{ident}/summary")
    def verify_summary(ident:str,body:list[Summary],who=access("audit","founder")):
        with store.tx() as db:
            d=lookup(db,ident)
            facts=[]
            valid=0<len(body)<=12
            for item in body[:12]:
                ev=db.execute("SELECT data FROM events WHERE id=? AND decision=? AND kind IN ('decision','model evaluated')",(item.event_id,ident)).fetchone()
                valid &= bool(ev and json.loads(ev[0]).get(item.field)==item.value)
                facts.append(item.model_dump())
            result=dict(valid=bool(valid),facts=facts if valid else [],summary=d["reason"])
            event(db,ident,"audit","summary checked",result)
            return result
    @app.get("/api/pack")
    def get_pack(who=access("founder","knowledge","risk")):
        with store.tx(False) as db:return pack(db)
    @app.get("/api/drafts")
    def drafts(who=access("founder","knowledge","risk")):
        with store.tx(False) as db:
            return [dict(r)|{"body":json.loads(r["body"])} for r in db.execute("SELECT * FROM drafts ORDER BY rowid DESC LIMIT 100")]
    def add_draft(kind,body,actor):
        with store.tx() as db:
            i=uid("K")
            db.execute("INSERT INTO drafts VALUES(?,?,?,?)",(i,pack(db)["version"],canonical(dict(kind=kind,data=body.model_dump())),"pending"))
            event(db,i,actor,"draft submitted",{"kind":kind})
            return {"draft_id":i,"status":"pending"}
    @app.post("/api/drafts")
    def draft(body:Draft,who=access("knowledge","founder")):return add_draft("record",body,who[0])
    @app.post("/api/gaps")
    def gap(body:Gap,who=access("risk","founder")):return add_draft("gap",body,who[0])
    @app.put("/api/drafts/{ident}")
    def edit(ident:str,body:Draft,who=access("founder")):
        with store.tx() as db:
            row=db.execute("SELECT body FROM drafts WHERE id=? AND status='pending'",(ident,)).fetchone()
            check(row and json.loads(row[0])["kind"]=="record","Pending record required")
            db.execute("UPDATE drafts SET body=?,base=? WHERE id=?",(canonical(dict(kind="record",data=body.model_dump())),pack(db)["version"],ident))
            event(db,ident,"founder","draft edited",{"record":body.record_id})
        return {"status":"pending"}
    @app.post("/api/drafts/{ident}/{action}")
    def review(ident:str,action:str,who=access("founder")):
        check(action in ("approve","dismiss"),"Invalid review action")
        with store.tx() as db:
            r=db.execute("SELECT * FROM drafts WHERE id=? AND status='pending'",(ident,)).fetchone()
            check(r,"Draft is no longer pending")
            k=pack(db)
            if action=="approve":
                check(r["base"]==k["version"],"Stale draft; review against current pack")
                obj=json.loads(r["body"])
                v=obj["data"]
                if obj["kind"]=="gap":
                    check(v["operation"] in k["operations"] and v["severity"]=="material","Only a known material gap can block an operation")
                    k["operations"][v["operation"]]["coverage"]["fulfilment"]="unresolved: "+v["finding"]
                else:
                    rec=k["records"].get(v["record_id"])
                    check(rec and v["value"] is not None and v["evidence_status"]!="unknown" and rec["approved_range"][0]<=v["value"]<=rec["approved_range"][1],"Unknown record, evidence or out-of-range value")
                    check(v["context"]==rec["context"],"Context must match the registered parameter")
                    rec.update({f:v[f] for f in ("value","source","context","sample_size","limitations","evidence_status","evidence")},approver="founder",version=rec["version"]+1,approval_status="approved_for_demo")
                k["version"]+=1
                db.execute("INSERT INTO packs VALUES(?,?)",(k["version"],canonical(k)))
            db.execute("UPDATE drafts SET status=? WHERE id=?",(action,ident))
            event(db,ident,"founder","knowledge reviewed",{"action":action,"pack":k["version"]})
            return {"status":action,"pack_version":k["version"]}
    @app.put("/api/profile")
    def profile(body:Profile,who=access("founder")):
        with store.tx() as db:
            k=pack(db)
            k.update(profile=body.model_dump(),profile_version=k["profile_version"]+1,version=k["version"]+1)
            db.execute("INSERT INTO packs VALUES(?,?)",(k["version"],canonical(k)))
            event(db,"profile","founder","owner profile changed",{"version":k["version"],"profile":k["profile"]})
            return k["profile"]
    from .web_api import install_web
    install_web(app,svc,access)
    if starting_demo:
        store.reset_records()
    app.mount("/static",StaticFiles(directory=ROOT/"frontend"),name="static")
    return app
