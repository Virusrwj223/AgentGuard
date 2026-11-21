import argparse
import json
import os
import sys
import webbrowser

from app.settings import configure, configured_solver, remember_pat
from app.store import ROOT, Store


def main():
    configure()
    parser=argparse.ArgumentParser(description="Northline Travel · AgentGuard demo")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--key",choices=("founder","audit","risk","knowledge"))
    parser.add_argument("--qwenwork-config",action="store_true",help="Print private, one-time worker connector JSON")
    parser.add_argument("--configure-pat",nargs=2,metavar=("EXE","VERSION"),help="Calibrate and remember a local PAT installation")
    parser.add_argument("--open-browser",action="store_true")
    args=parser.parse_args()
    if args.configure_pat:
        remember_pat(*args.configure_pat)
        return
    url=f"http://127.0.0.1:{args.port}"
    if args.key or args.qwenwork_config:
        store=Store(os.environ.get("AG_DB",str(ROOT/"instance"/"agentguard.sqlite3")))
        if args.key:
            print(store.role_key(args.key))
        else:
            print(json.dumps({"mcpServers":{"northline-web":{"type":"stdio","command":sys.executable,
                "args":[str(ROOT/"integrations"/"mcp_server.py")],
                "env":{"AG_URL":url,"AG_ROLE":"web_worker","AG_ROLE_KEY":store.role_key("web_worker")}}}},indent=2))
        return
    import uvicorn
    from app.api import make_app
    solver=configured_solver()
    app=make_app(solver=solver)
    print(f"Verifier: {solver.detail}",flush=True)
    print(f"Open your travel desk at {url}/",flush=True)
    if args.open_browser:
        import threading
        threading.Timer(1.1,webbrowser.open,args=(url+"/",)).start()
    uvicorn.run(app,host="127.0.0.1",port=args.port)


if __name__=="__main__":
    main()
