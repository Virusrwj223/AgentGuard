import copy
import unittest
from fractions import Fraction as F
from app.compiler import Graph,Compiler,QUERY,Review,emit
from app.contracts import Proposal
from app.examples import proposal
from app.store import seed
from app.pat import parse,interval
from tests.support import ModelReader

class Mathematics(unittest.TestCase):
    def reduce(self,outcomes,k):
        g=Graph()
        for p,v in outcomes:g.branch(0,"terminal",F(p),delta=v)
        paths=g.paths(k)
        lo=min(x["utility"] for x in paths);hi=max(x["utility"] for x in paths)
        model=emit(g,k,lo,hi)
        self.assertEqual(model.count(QUERY),1)
        run=ModelReader().run(model)
        x=g.oracle(k)
        self.assertEqual(interval(run,lo,hi,x),(x,x))
        return x,model,run
    def test_rare(self):
        xs=[(".9",900),(".099",750),(".001",-999250)]
        score,model,run=self.reduce(xs,1)
        self.assertEqual(score,-115)
        self.assertEqual(F(run['probability']),F(999135,1000150))
        self.assertIn("20000 : Good() 3 : Bad()",model)
        self.assertEqual(self.reduce(xs,3)[0],F("-2113.50"))
    def test_high_probability_of_profit(self):
        self.assertEqual(self.reduce([(".99",100),(".01",-100000)],1)[0],-901)
    def test_degenerate(self):
        for x in [-300,0,400]:self.assertEqual(self.reduce([(1,x)],1)[0],x)
        self.assertEqual(self.reduce([(".5",-100),(".5",-20)],1)[0],-60)
    def test_overflow(self):
        with self.assertRaises(Review):self.reduce([(1,-2**30)],3)
    def test_precision(self):
        with self.assertRaises(Review):interval({"status":"SUCCESS","probability":"0.99999"},0,10**8,F(99999001))
        self.assertEqual(interval({"status":"SUCCESS","probability":"0.500001"},0,100,F(50)),(F(50),F("50.0001")))
    def test_verdict_parser(self):
        text="The Assertion (Business() reaches economicSuccess with pmin) is Valid with Min Probability 0.25;"
        self.assertEqual(parse(text),F(1,4))
        for x in [text*2,text.replace("pmin","pmax"),text.replace("Min","Max"),text.replace("0.25","NaN"),text.replace("0.25","1.01"),"Precision 0.25","",text+"\nParsing error"]:
            with self.assertRaises(Review):parse(x)
class Composition(unittest.TestCase):
    def setUp(self):
        self.k=seed("pack.json");self.b=seed("bookings.json")["B001"]
        self.p=Proposal.model_validate(proposal("amend","R1","B001"))
    def build(self,index=0):
        c=Compiler(self.k,self.b,self.p.goal,"reference")
        return c,c.compile(self.p.candidate_strategies[index].plan)
    def test_conditional_joint(self):
        c,g=self.build()
        self.assertEqual({x['value']:F(x['p']) for x in g.paths(1)},{-8000:F('.8'),-15000:F('.198'),-100015000:F('.002')})
        self.assertEqual(g.oracle(1),-209400)
        self.assertEqual(self.build(1)[1].oracle(1),-9400)
    def test_dependency_mutations(self):
        original=copy.deepcopy(self.k)
        for mutate in [lambda k:k["events"].pop("failure"),lambda k:k["events"]["failure"]["tree"].update(events=[]),lambda k:k["operations"]["GUARANTEE_AMENDMENT"]["coverage"].pop("payment"),lambda k:k["records"]["fee"].update(value=None),lambda k:k["records"]["fee"].update(expires="2025-09-01"),lambda k:k["records"]["normal_available"].update(value=10001),lambda k:k["records"]["fee"].update(approval_status="pending")]:
            self.k=copy.deepcopy(original);mutate(self.k)
            with self.assertRaises(Review):self.build()
    def test_extension_and_once(self):
        self.k["events"]["failure"]["tree"]["events"]+=["dispute"]
        self.assertEqual(self.build()[1].oracle(1),-209400)
        self.k["events"]["failure"]["tree"]["events"]+=["extra"]
        self.k["events"]["extra"]={"when":{"failed":True},"tree":{"label":"rebooking overhead","set":{},"delta":["-fee"],"events":[]}}
        g=self.build()[1]
        self.assertEqual(g.oracle(1),-211000)
        self.assertEqual(emit(g,1,-200000000,0).count(QUERY),1)
    def test_repeatable(self):
        c,g=self.build(1);d,h=self.build(1)
        self.assertEqual(g.export(),h.export());self.assertEqual(emit(g,1,-15000,0),emit(h,1,-15000,0))
