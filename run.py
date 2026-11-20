import argparse
import os
from app.store import ROOT,Store

def main():
    parser=argparse.ArgumentParser(description="AgentGuard local synthetic travel sandbox")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--key",choices=("founder","audit","risk","knowledge"))
    args=parser.parse_args()
    store=Store(os.environ.get("AG_DB",str(ROOT/"instance"/"agentguard.sqlite3")))
    if args.key:
        print(store.role_key(args.key))
        return
    import uvicorn
    from app.api import make_app
    print(f"Customer desk  http://127.0.0.1:{args.port}/\nFounder console http://127.0.0.1:{args.port}/founder\nShow your private local founder key with: python run.py --key founder")
    uvicorn.run(make_app(store),host="127.0.0.1",port=args.port)
if __name__=="__main__":main()
