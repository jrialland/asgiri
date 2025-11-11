"""
This it a test ASGI application used for testing purposes.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket

lifespan_records = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    lifespan_records.append("startup")
    yield
    lifespan_records.append("shutdown")


app = FastAPI(lifespan=lifespan)


@app.get("/helloworld")
def get_helloworld():
    return {"Hello": "World"}


@app.post("/echo")
def post_echo(message: dict):
    return message


@app.get("/read_params")
def get_read_params(name: str, age: int, active: bool):
    return {"Name": name, "Age": age, "Active": active}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")
