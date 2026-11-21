"""Private browser sessions and the optional, standing QwenWork planning connector."""
from typing import Any

from fastapi import BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .chat import ChatDesk


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Turn(Body):
    nonce: str = Field(min_length=16, max_length=70, pattern=r"^[A-Za-z0-9_-]+$")
    revision: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=1800)


class ImportBooking(Body):
    customer_key: str = Field(min_length=20,max_length=160)


class Poll(Body):
    wait_seconds: int = Field(default=15,ge=0,le=18)


class Answer(Body):
    job_id: str = Field(min_length=10,max_length=64)
    lease: str = Field(min_length=10,max_length=64)
    answer: dict[str,Any]


def install_web(app, service, access):
    desk=ChatDesk(service)
    app.state.chat=desk
    cookie="northline_session"

    def thread(request:Request):
        who=desk.identify(request.cookies.get(cookie))
        if not who:
            raise HTTPException(401,"This browser session expired. Reload the page to start again.")
        return who

    def respond(who, tasks):
        state=desk.state(who)
        if state["working"]:
            tasks.add_task(desk.work,who)
        return state

    @app.post("/api/chat/session")
    def begin(request:Request,response:Response,tasks:BackgroundTasks):
        who=desk.identify(request.cookies.get(cookie))
        new_session = who is None
        if not who:
            who,key=desk.start()
            response.set_cookie(cookie,key,httponly=True,samesite="strict",secure=request.url.scheme=="https",
                                max_age=60*60*24*90,path="/api/chat")
        return respond(who,tasks) | {"new_session": new_session}

    @app.get("/api/chat")
    def state(tasks:BackgroundTasks,who=Depends(thread)):
        return respond(who,tasks)

    @app.post("/api/chat/messages")
    def message(body:Turn,tasks:BackgroundTasks,who=Depends(thread)):
        desk.turn(who,body)
        return respond(who,tasks)

    @app.post("/api/chat/import")
    def migrate(body:ImportBooking,tasks:BackgroundTasks,who=Depends(thread)):
        try:
            desk.migration(who,body.customer_key)
        except PermissionError:
            raise HTTPException(403,"This previous reservation could not be restored.")
        return respond(who,tasks)

    @app.post("/api/chat/trips/{trip_id}/select")
    def select_trip(trip_id:str,tasks:BackgroundTasks,who=Depends(thread)):
        desk.select(who,trip_id)
        return respond(who,tasks)

    @app.delete("/api/chat/trips/{trip_id}")
    def delete_trip(trip_id:str,tasks:BackgroundTasks,who=Depends(thread)):
        desk.delete(who,trip_id)
        return respond(who,tasks)

    @app.post("/api/web-worker/next")
    def next_task(body:Poll,who=access("web_worker")):
        return desk.broker.next(body.wait_seconds)

    @app.post("/api/web-worker/submit")
    def submit(body:Answer,who=access("web_worker")):
        if not desk.broker.submit(body.job_id,body.lease,body.answer):
            raise HTTPException(409,"Planning task has expired or was already returned.")
        return {"accepted":True}
