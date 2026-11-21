"""Test-only process evaluator for the emitted PCSP subset, independent of Graph.oracle."""
import re
from fractions import Fraction as F
from app.compiler import QUERY
class ModelReader:
    ready=True
    detail="Test fixture"
    def run(self,text):
        assert text.splitlines()[-1]==QUERY
        nodes={int(n):expr for n,expr in re.findall(r"^N(\d+)\(\) = (.*);$",text,re.M)}
        scores={}
        def value(s):
            if s=="Good()":return F(1)
            if s=="Bad()":return F(0)
            return scores[int(re.search(r"N(\d+)\(\)$",s)[1])]
        for n in sorted(nodes,reverse=True):
            expr=nodes[n]
            if expr.startswith("pcase"):
                pairs=re.findall(r"(\d+) : ((?:edge\d+_\d+ -> )?(?:N\d+|Good|Bad)\(\))",expr)
                assert len(pairs)>=2
                scores[n]=sum(int(w)*value(target) for w,target in pairs)/sum(int(w) for w,_ in pairs)
            else:scores[n]=value(expr)
        return dict(status="SUCCESS",probability=str(scores[0]),version="TEST DOUBLE")


class TravelFixture:
    """Deterministic fare service for tests only; never selected in application code."""
    def enrich(self,draft):
        import time
        from app.trip_intake import FIELDS
        updated=dict(draft)
        for field in ("origin","destination"):
            if updated.get(field):
                updated[field+"_location"]={"name":updated[field],"iata":"SIN" if field=="origin" else "LON"}
        if all(updated.get(f) for f in FIELDS):
            updated["fare"]={"amount_cents":updated["nights"]*updated["travellers"]*12000,"expires":time.time()+300,"live":False,"source":"test fixture"}
        return updated,[]
