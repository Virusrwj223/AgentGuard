"""Run genuine MCP stdio tool calls against a disposable local HTTP server."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4
from urllib.request import Request,urlopen
import httpx
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.examples import proposal
from app.contracts import Goal
from app.sales_desk import make_proposal
from app.store import Store


async def check_web_worker(url, env):
    store = Store(env["AG_DB"])
    config = StdioServerParameters(command=sys.executable,args=[str(ROOT/"integrations/mcp_server.py")],
        env=dict(env,AG_ROLE="web_worker",AG_ROLE_KEY=store.role_key("web_worker"),AG_URL=url))
    async with httpx.AsyncClient(base_url=url,timeout=15,trust_env=False) as browser:
        response = await browser.post("/api/chat/session",json={})
        response.raise_for_status()
        async def say(text):
            state = (await browser.get("/api/chat")).json()
            reply = await browser.post("/api/chat/messages",
                json={"revision":state["revision"],"message":text,"nonce":uuid4().hex})
            reply.raise_for_status()
            return reply.json()
        start = date.today()+timedelta(days=30)
        fixture=(await browser.post('/api/sessions',json={})).json()
        restored=await browser.post('/api/chat/import',json={"customer_key":fixture['customer_key']})
        restored.raise_for_status()
        assert restored.json()['itinerary']['phase']=='booked'
        async with stdio_client(config) as (reader,writer),ClientSession(reader,writer) as client:
            await client.initialize()
            assert {t.name for t in (await client.list_tools()).tools}=={"health","next_web_task","complete_web_task"}
            async def call(name, args):
                result = await client.call_tool(name,args)
                assert not result.isError, name
                return json.loads(result.content[0].text)
            assert (await call("next_web_task",{"wait_seconds":0}))["kind"]=="idle"
            response = asyncio.create_task(say("Move my trip 30 days later"))
            job = await call("next_web_task",{"wait_seconds":10})
            assert job["kind"]=="sales"
            data = job["input"]
            answer = make_proposal(Goal.model_validate(data["goal"]),data["booking"],data["request_id"])
            submitted = await call("complete_web_task",{"job_id":job["job_id"],"lease":job["lease"],"answer":answer.model_dump()})
            assert submitted["accepted"]
            await response
            for _ in range(60):
                if not (await browser.get("/api/chat")).json()["working"]:
                    break
                await asyncio.sleep(.1)
            explained = await say("Explain this decision")
            assert "QwenWork background worker" in explained["messages"][-1]["body"]
            assert not explained["itinerary"]["has_offer"]
            print("Restored booking, MCP worker handoff, independent preview and in-chat explanation passed (SDK client).")

async def main():
    with tempfile.TemporaryDirectory() as tmp, socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1];sock.close()
        url=f"http://127.0.0.1:{port}"
        env=dict(os.environ,AG_DB=str(Path(tmp)/"demo.sqlite"),AG_PAT_EXE="",DASHSCOPE_API_KEY="",AG_QWEN_BASE_URL="")
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
            await check_web_worker(url,env)
        finally:
            proc.terminate();proc.wait(timeout=8)
if __name__=="__main__":asyncio.run(main())
