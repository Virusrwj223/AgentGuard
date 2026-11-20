"""Run genuine MCP stdio tool calls against a disposable local HTTP server."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request,urlopen
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.examples import proposal

async def main():
    with tempfile.TemporaryDirectory() as tmp, socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1];sock.close()
        url=f"http://127.0.0.1:{port}"
        env=dict(os.environ,AG_DB=str(Path(tmp)/"demo.sqlite"),AG_PAT_EXE="")
        proc=subprocess.Popen([sys.executable,"run.py","--port",str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(80):
                try:
                    with urlopen(url+"/health",timeout=.3):break
                except OSError:time.sleep(.1)
            def post(path,body,key=""):
                req=Request(url+path,json.dumps(body).encode(),headers={"Content-Type":"application/json","Authorization":"Bearer "+key})
                with urlopen(req,timeout=5) as response:return json.load(response)
            account=post("/api/sessions",{})
            rid=post("/api/requests",{"message":"Move my booking 30 days later at no extra cost."},account["customer_key"])["request_id"]
            conf=StdioServerParameters(command=sys.executable,args=[str(ROOT/"integrations/mcp_server.py")],env=dict(env,AG_ROLE="sales",AG_ROLE_KEY=account["sales_key"],AG_URL=url))
            async with stdio_client(conf) as (reader,writer),ClientSession(reader,writer) as client:
                await client.initialize()
                names={t.name for t in (await client.list_tools()).tools}
                assert names=={"health","get_booking_context","get_operation_catalog","evaluate_strategies","get_authorized_response"}
                ctx=await client.call_tool("get_booking_context",{})
                assert not ctx.isError and rid in ctx.content[0].text
                result=await client.call_tool("evaluate_strategies",{"proposal":proposal("amend",rid,"B001")})
                assert not result.isError and "NEEDS_REVIEW" in result.content[0].text
                print("MCP handshake, scoped catalog, booking lookup and structured evaluation passed (oracle-only mode).")
        finally:
            proc.terminate();proc.wait(timeout=8)
if __name__=="__main__":asyncio.run(main())
