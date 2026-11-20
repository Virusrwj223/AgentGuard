"""Release check using only the operator's actual, fingerprinted PAT executable."""
import json
import sys
from fractions import Fraction as F
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.compiler import Graph,emit
from app.pat import Pat,interval
from app.store import ROOT

def main():
    solver=Pat()
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
    dest=ROOT/"instance"/"pat-acceptance.json";dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(dict(version=solver.version,sha256=solver.sha,calibration=solver.calibration,checks=out),indent=2),encoding="utf-8")
    for r in out:print("PASS" if r["passed"] else "REVIEW",r["case"])
    print(dest)
    return 0 if all(r["passed"] for r in out) else 1
if __name__=="__main__":sys.exit(main())
