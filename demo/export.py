"""Regenerate a redacted flagship preview and PCSP files using the real local application."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.compiler import QUERY
from app.examples import proposal
from app.contracts import Proposal,CustomerResult
from app.governance import Service
from app.pat import Pat
from app.store import ROOT,Store,lookup
def main():
    dest=ROOT/"demo"/"generated";dest.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        st=Store(Path(tmp)/"demo.db");svc=Service(st,Pat({}))
        sid=st.session("B001")["session_id"]
        rid=svc.inquiry(sid,"Move my existing booking 30 days later at no extra cost.")
        p=proposal("amend",rid,"B001")
        d=svc.evaluate(sid,Proposal.model_validate(p),"Scripted regression fixture")
        with st.tx(False) as db:report=lookup(db,d["decision_id"])
        for m in report["models"]:
            (dest/(m["strategy"]+"_"+m["scenario"]+".pcsp")).write_text(m["pcsp"],encoding="utf-8")
        (dest/"flagship-audit.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        p["request_id"]="REPLACE_WITH_ACTIVE_REQUEST"
        (ROOT/"demo"/"amend-proposal.json").write_text(json.dumps(p,indent=2),encoding="utf-8")
        (ROOT/"shared"/"customer-result.schema.json").write_text(json.dumps(CustomerResult.model_json_schema(),indent=2),encoding="utf-8")
if __name__=="__main__":main()
