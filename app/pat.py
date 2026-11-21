"""Isolated PAT console adapter. Unavailable and uncertain results have no authority."""
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from fractions import Fraction as F
from .compiler import QUERY, Review, check, Graph, emit

SLOTS=threading.BoundedSemaphore(2)

def parse(text):
    check(len(text)<=262144,"PAT result too large")
    check(not re.search(r"(?im)\b(error|exception|invalid|timeout|unknown)\b",text),"PAT reported an error")
    hits=re.findall(r"The\s+Assertion\s*\((.*?)\)\s+is\s+Valid\s+with\s+(?:Min(?:imum)?\s+)?Probability\s+([^;\s]+)\s*;",text,re.I|re.S)
    check(len(hits)==1 and len(re.findall(r"The\s+Assertion\s*\(",text,re.I))==1,"Missing or ambiguous probability verdict")
    expr,number=hits[0]
    normalized=" ".join(expr.strip().replace("()","").split())
    check(normalized=="Business reaches economicSuccess with pmin","Unexpected PAT assertion")
    check(re.fullmatch(r"(?:0|1|0?\.\d{1,24}|1\.0+)(?:[eE][+-]?\d{1,2})?",number),"Invalid probability field")
    p=F(number)
    check(0<=p<=1,"Probability outside [0,1]")
    return p

class Pat:
    def __init__(self, config=None):
        cfg=os.environ if config is None else config
        self.exe=cfg.get("AG_PAT_EXE","")
        self.runtime=cfg.get("AG_PAT_RUNTIME","")
        self.version=cfg.get("AG_PAT_VERSION","")
        self.sha=cfg.get("AG_PAT_SHA256","")
        self.ready=False
        self.calibration=[]
        self.detail="PAT console is not configured"
        if self.exe:
            try:
                path=Path(self.exe)
                check(path.is_absolute() and path.is_file() and self.version,"PAT executable path or version missing")
                check(hashlib.sha256(path.read_bytes()).hexdigest()==self.sha,"PAT executable fingerprint mismatch")
                for p in (F(1,4),F(1,1000)):
                    g=Graph()
                    g.branch(0,"good",p,delta=1)
                    g.branch(0,"bad",1-p)
                    run=self.run(emit(g,1,0,1))
                    self.calibration.append(run)
                    check(run["status"]=="SUCCESS" and abs(F(run["probability"])-p)<=F(1,100000),"PAT calibration failed")
                fixed=Graph()
                fixed.branch(0,"constant",F(1),delta=1)
                run=self.run(emit(fixed,1,1,1))
                self.calibration.append(run)
                check(run["status"]=="SUCCESS" and abs(F(run["probability"])-F(1,2))<=F(1,100000),
                      "PAT constant-utility calibration failed")
                self.ready=True
                self.detail="PAT console calibrated"
            except (Review,OSError) as err:
                self.detail=str(err)
    def run(self,model):
        start=time.monotonic()
        result=dict(status="TOOL_ERROR",version=self.version,sha256=self.sha,module="PCSP",assertion=QUERY)
        if not SLOTS.acquire(timeout=1):
            return result | {"diagnostic":"Runner busy"}
        try:
            with tempfile.TemporaryDirectory(prefix="agentguard-") as tmp:
                root=Path(tmp)
                (root/"model.pcsp").write_text(model,encoding="utf-8")
                cmd=[self.exe,"-pcsp","-engine","0","-behavior","0",str(root/"model.pcsp"),str(root/"result.txt")]
                if self.runtime:
                    cmd.insert(0,self.runtime)
                # The helper applies limits before replacing itself or launching PAT.
                argv=[sys.executable,str(Path(__file__).with_name("limited_run.py")),*cmd]
                with (root/"console.log").open("wb") as log:
                    code=subprocess.run(argv,cwd=tmp,stdout=log,stderr=subprocess.STDOUT,timeout=8,check=False).returncode
                with (root/"console.log").open("rb") as log:
                    result["console"]=log.read(8192).decode(errors="replace")
                path=root/"result.txt"
                if code or not path.is_file() or not 0<path.stat().st_size<=262144:
                    result.update(diagnostic="Nonzero exit, missing or invalid result file",exit_code=code)
                    return result
                data=path.read_bytes()
                output=data.decode("utf-16" if data.startswith((b'\xff\xfe',b'\xfe\xff')) else "utf-8-sig")
                result["output"]=output
                result.update(status="SUCCESS",probability=str(parse(output)))
        except subprocess.TimeoutExpired:
            result.update(status="TIMEOUT",diagnostic="PAT exceeded eight seconds")
        except (UnicodeError,Review) as err:
            result.update(status="PARSE_ERROR",diagnostic=str(err))
        except OSError as err:
            result["diagnostic"]=str(err)
        finally:
            result["duration_ms"]=round(1000*(time.monotonic()-start),2)
            SLOTS.release()
        return result

def interval(run,lo,hi,exact):
    """A posteriori bound for THIS finite graph from independent exact DP.
    Also refuse discrepancy greater than 0.05 cents regardless of score margin.
    """
    if run["status"]!="SUCCESS":
        raise Review("PAT "+run["status"])
    p=F(run["probability"])
    expected=F(1,2) if hi==lo else (exact-lo)/(hi-lo)
    decoded=F(lo) if hi==lo else lo+(hi-lo)*p
    check(abs(p-expected)<=F(1,100000) and abs(decoded-exact)<=F(1,20),"Insufficient PAT precision or oracle discrepancy")
    return min(decoded,exact),max(decoded,exact)
