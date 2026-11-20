"""Explicit QwenWork JSON handoff when a custom connector is unavailable."""
import argparse
import getpass
import json
import os
import sys
import urllib.request
import urllib.error
def call(path,method="GET",body=None):
    base=os.environ.get("AG_URL","http://127.0.0.1:8765").rstrip("/")
    token=os.environ.get("AG_ROLE_KEY") or getpass.getpass("Scoped AgentGuard credential: ")
    req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=100) as resp:return json.load(resp)
    except urllib.error.HTTPError as err:
        raise ValueError(f"HTTP {err.code}: "+err.read(2048).decode()) from err
if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=["health","context","catalog","evaluate","response","draft","audit","gap","pack","summary"])
    parser.add_argument("file_or_id",nargs="?")
    parser.add_argument("--decision")
    a=parser.parse_args()
    routes={"health":"/health","context":"/api/context","catalog":"/api/catalog","evaluate":"/api/evaluate","response":"/api/latest","draft":"/api/drafts","gap":"/api/gaps","pack":"/api/pack"}
    if a.action in ("evaluate","draft","gap","summary"):
        with open(a.file_or_id,encoding="utf-8") as file:body=json.load(file)
        result=call(routes[a.action] if a.action!="summary" else "/api/audits/"+a.decision+"/summary","POST",body)
    elif a.action=="audit":result=call("/api/audits/"+a.file_or_id)
    else:result=call(routes[a.action])
    print(json.dumps(result,indent=2))
