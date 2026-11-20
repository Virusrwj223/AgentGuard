"""Bounded plan interpretation, provenance and deterministic lowering to PCSP."""
import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import date
from fractions import Fraction as F
from .contracts import Conditional, Sequence, Request, Price, Upgrade

QUERY = "#assert Business() reaches economicSuccess with pmin;"
INT_MAX = 2**31 - 1
FAMILIES = {"cost","availability","payment","fulfilment","liability","retention","opportunity"}
class Review(ValueError):
    pass

def check(test, reason):
    if not test:
        raise Review(reason)

def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)

def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()

def bounded(n):
    check(type(n) is int and -INT_MAX <= n <= INT_MAX, "Integer range exceeded")
    return n

@dataclass
class Edge:
    label: str
    p: F
    target: int

@dataclass
class Node:
    state: dict
    value: int
    edges: list[Edge] = field(default_factory=list)

class Graph:
    def __init__(self, initial=None):
        self.nodes = [Node(initial or {},0)]
    def branch(self, parent, label, probability, changes=None, delta=0):
        check(len(self.nodes)<512,"Reachable state limit exceeded")
        prev = self.nodes[parent]
        idx = len(self.nodes)
        self.nodes.append(Node(prev.state | (changes or {}),bounded(prev.value + delta)))
        prev.edges.append(Edge(label,probability,idx))
        return idx
    def paths(self,k):
        result=[]
        def walk(i,p,labels):
            node=self.nodes[i]
            if not node.edges:
                x=bounded(node.value if node.value>=0 else k*node.value)
                result.append(dict(node=i,events=labels,p=str(p),value=node.value,utility=x,contribution=str(p*x)))
                return
            check(sum(e.p for e in node.edges)==1,"Incomplete probability mass")
            for e in node.edges:
                check(0<e.p<=1 and i<e.target<len(self.nodes),"Invalid graph edge")
                walk(e.target,p*e.p,labels+[e.label])
        walk(0,F(1),[])
        return result
    def oracle(self,k):
        scores={}
        for i in reversed(range(len(self.nodes))):
            n=self.nodes[i]
            scores[i]=sum((e.p*scores[e.target] for e in n.edges),F(0)) if n.edges else F(bounded(n.value if n.value>=0 else k*n.value))
        return scores[0]
    def export(self):
        return [dict(id=i,state=n.state,value=n.value,edges=[dict(event=e.label,p=str(e.p),to=e.target) for e in n.edges]) for i,n in enumerate(self.nodes)]

def weights(probabilities):
    check(sum(probabilities)==1 and all(p>0 for p in probabilities),"Invalid branch weights")
    common=math.lcm(*(p.denominator for p in probabilities))
    raw=[int(x*common) for x in probabilities]
    divisor=math.gcd(*raw)
    result=[v//divisor for v in raw]
    bounded(sum(result))
    return result

def choice(probs,targets):
    if len(probs)==1:
        return targets[0]
    return "pcase { " + " ".join(f"{w} : {t}" for w,t in zip(weights(probs),targets))+" }"

def emit(graph,k,low,high):
    # PAT reachability expects a named state proposition.
    lines=["var successFlag = false;","#define economicSuccess successFlag == true;",
           "Good() = success{successFlag = true;} -> Skip;","Bad() = no_success -> Skip;","Business() = N0();"]
    for i,n in enumerate(graph.nodes):
        if n.edges:
            rhs=choice([e.p for e in n.edges],[f"edge{i}_{j} -> N{e.target}()" for j,e in enumerate(n.edges)])
        else:
            x=bounded(n.value if n.value>=0 else n.value*k)
            check(low<=x<=high,"Invalid utility bounds")
            q=F(1) if high==low else F(x-low,high-low)
            rhs="Good()" if q==1 else "Bad()" if q==0 else choice([q,1-q],["Good()","Bad()"])
        lines.append(f"N{i}() = {rhs};")
    return "\n".join(lines+[QUERY,""])

class Compiler:
    def __init__(self,pack,booking,goal,scenario,baseline=False):
        self.pack,self.booking,self.goal,self.scenario=pack,booking,goal,scenario
        self.baseline=baseline
        self.used=set()
        self.closure=set()
        self.ops=set()
        self.terms={}
        check(booking["currency"]==pack["currency"]=="SGD" and booking["policy"]=="binding_hold_v1","Unsupported currency or supplier policy")
        lead=(date.fromisoformat(booking["departure"])-date.fromisoformat(booking["as_of"])).days
        check((booking["context"]=="normal" and 8<=lead<=90) or (booking["context"]=="peak" and 1<=lead<=7),"Unsupported seasonal lead-time context")
        check((goal.type=="sale")==(booking["kind"]=="quote"),"Goal incompatible with booking status")
        self.g=Graph(dict(hold="none",done=False,amended=False,failed=False,upgraded=False,upgrade_attempted=False,declined=False,sold=False,
                          seeking_amend=goal.type in ("amend","amend_upgrade"),occurred=[]))
    def number(self,ref,unit):
        rec=self.pack["records"].get(ref)
        check(rec and rec["approval_status"]=="approved_for_demo" and rec["units"]==unit,"Missing or unapproved parameter "+ref)
        check(rec["source"] and rec["evidence"] and rec["evidence_status"]!="unknown","Unknown evidence "+ref)
        for asof in (self.booking["as_of"],date.today().isoformat()):
            check(rec["effective"]<=asof<=rec["expires"],"Evidence outside effective period: "+ref)
        v=rec["value"]
        check(type(v) is int and rec["approved_range"][0]<=v<=rec["approved_range"][1],"Value outside approved range: "+ref)
        self.used.add(ref)
        return bounded(v)
    def prob(self,ref,args):
        if ref=="@availability":
            ref=self.pack["scenarios"][self.booking["context"]][self.scenario]
        if ref=="@consent":
            if args["charge_cents"]<=self.goal.max_extra_charge_cents:
                return F(1)
            ref="consent_upgrade"
        n=self.number(ref,"basis points")
        check(0<=n<=10000,"Invalid probability")
        return F(n,10000)
    def money(self,tree,args):
        v=0
        for expr in tree["delta"]:
            sign,name=expr[0],expr[1:]
            check(sign in "+-","Invalid financial expression")
            amount=args[name[1:]] if name.startswith("$") else self.number(name,"SGD cents")
            v=bounded(v+(amount if sign=="+" else -amount))
        return v
    def expand(self,tree,parents,args,stack=()):
        if "probability" in tree:
            p=self.prob(tree["probability"],args)
            out=[]
            for key,weight in (("yes",p),("no",1-p)):
                if weight:
                    starts=[self.g.branch(i,tree["probability"]+" "+key,weight) for i in parents]
                    out+=self.expand(tree[key],starts,args,stack)
            return out
        nodes=[self.g.branch(i,tree["label"],F(1),tree["set"],self.money(tree,args)) for i in parents]
        for ev in tree["events"]:
            check(ev not in stack,"Cyclic consequence definition")
            self.closure.add(ev)
            event=self.pack["events"].get(ev)
            check(event,"Missing consequence "+ev)
            out=[]
            for i in nodes:
                st=self.g.nodes[i].state
                if all(st.get(k)==v for k,v in event["when"].items()) and ev not in st["occurred"]:
                    start=self.g.branch(i,ev,F(1),{"occurred":st["occurred"]+[ev]})
                    out+=self.expand(event["tree"],[start],args,stack+(ev,))
                else:
                    out.append(i)
            nodes=out
        return nodes
    def dependencies(self,tree,args,seen=()):
        # Validate both outcomes, including currently zero-weight branches.
        if "probability" in tree:
            self.prob(tree["probability"],args)
            return self.dependencies(tree["yes"],args,seen) | self.dependencies(tree["no"],args,seen)
        self.money(tree,args)
        found=set()
        for ev in tree["events"]:
            check(ev not in seen and ev in self.pack["events"],"Missing or cyclic dependency "+ev)
            found.add(ev)
            found |= self.dependencies(self.pack["events"][ev]["tree"],args,seen+(ev,))
        return found
    def apply(self,p,nodes):
        if isinstance(p,Sequence):
            for step in p.steps:
                nodes=self.apply(step,nodes)
            return nodes
        if isinstance(p,Conditional):
            left,right=[],[]
            for i in nodes:
                hold=self.g.nodes[i].state["hold"]
                check(hold!="none","Supplier response not observed")
                (left if hold=="held" else right).append(i)
            return self.apply(p.then,left)+self.apply(p.otherwise,right)
        name=p.op
        args=p.model_dump(exclude={"op"})
        item=self.pack["operations"].get(name)
        check(item and set(item["coverage"])==FAMILIES and all(v=="covered" or v.startswith("N/A: ") and len(v)>8 for v in item["coverage"].values()),"Unresolved risk coverage: "+name)
        deps=self.dependencies(item["tree"],args)
        check(set(self.pack["required_events"].get(name,[]))<=deps,"Required consequence omitted")
        self.closure |= deps
        self.ops.add(name)
        if isinstance(p,Request):
            check(self.goal.type in ("amend","amend_upgrade") and p.days==self.goal.days and str(p.days) in self.booking["slots"],"Requested date outside approved slots")
        if isinstance(p,Price):
            check(self.goal.type=="sale" and self.goal.discount_cents<=p.discount_cents<=self.number("sale_price","SGD cents"),"Invalid sale adjustment")
        if isinstance(p,Upgrade):
            check(self.goal.type in ("upgrade","amend_upgrade") and p.charge_cents in self.booking["upgrade_prices_cents"] and not self.booking["upgraded"],"Upgrade unavailable")
        if not nodes:
            return []
        for i in nodes:
            st=self.g.nodes[i].state
            check(all(st.get(k)==v for k,v in item["pre"].items()),"Failed precondition: "+name)
            check(not isinstance(p,Upgrade) or self.goal.type!="amend_upgrade" or st["amended"],"Compound upgrade needs a confirmed amendment")
            check(name!="KEEP_BOOKING" or st["hold"]!="held","A binding hold requires explicit confirmation")
        for key,v in args.items():
            check(key not in self.terms or self.terms[key]==v,"Conflicting terms")
            self.terms[key]=v
        return self.expand(item["tree"],nodes,args)
    def compile(self,plan):
        leaves=self.apply(plan,[0])
        check(all(self.g.nodes[i].state["done"] for i in leaves),"Unsettled booking or supplier hold")
        if not self.baseline:
            # Require every successful branch to fulfil the whole goal. Only observed
            # refusal, supplier failure or a rejected paid offer can justify fallback.
            for i in leaves:
                s=self.g.nodes[i].state
                if self.goal.type.startswith("amend"):
                    check(s["amended"] or s["failed"] or s["hold"]=="failed","Unfulfilled amendment")
                if "upgrade" in self.goal.type and (self.goal.type=="upgrade" or s["amended"]):
                    check(s["upgraded"] or s["declined"],"Unfulfilled upgrade")
                if self.goal.type=="sale":
                    check(s["sold"],"Unfulfilled sale")
            check(self.ops!={"KEEP_BOOKING"},"Baseline cannot fulfil goal")
        return self.g
