"""
This it a test ASGI application used for testing purposes.
"""

from contextlib import asynccontextmanager
from typing import cast

from asgiref.typing import ASGIApplication
from fastapi import FastAPI, WebSocket

lifespan_records = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    lifespan_records.append("startup")
    yield
    lifespan_records.append("shutdown")


_fastapi_app = FastAPI(lifespan=lifespan)

# Cast FastAPI to ASGIApplication for type checking compatibility
app: ASGIApplication = cast(ASGIApplication, _fastapi_app)


@_fastapi_app.get("/helloworld")
def get_helloworld():
    return {"Hello": "World"}


@_fastapi_app.post("/echo")
def post_echo(message: dict):
    return message


@_fastapi_app.get("/read_params")
def get_read_params(name: str, age: int, active: bool):
    return {"Name": name, "Age": age, "Active": active}


@_fastapi_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")
