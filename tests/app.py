"""
This it a test ASGI application used for testing purposes.
"""

from contextlib import asynccontextmanager
from hashlib import sha256
from typing import cast

from asgiref.typing import ASGIApplication
from fastapi import FastAPI, File, Request, UploadFile, WebSocket

lifespan_records = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    lifespan_records.append("startup")
    yield
    lifespan_records.append("shutdown")


_fastapi_app = FastAPI(lifespan=lifespan)

# Cast FastAPI to ASGIApplication for type checking compatibility
app: ASGIApplication = cast(ASGIApplication, _fastapi_app)


@_fastapi_app.get("/")
def read_root():
    return {"message": "Welcome to the test ASGI application!"}


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


@_fastapi_app.get("/cause_error")
async def get_cause_error():
    raise ValueError("This is a test error for error handling.")


@_fastapi_app.get("/lifespan_records")
async def get_lifespan_records():
    return {"records": lifespan_records}


@_fastapi_app.post("/uploadfile/formpost")
async def upload_file_formpost(file: UploadFile = File(...)):
    """Endpoint to upload a file and return its SHA256 hash and size."""
    md = sha256()
    content_size = 0
    while chunk := await file.read(1024 * 1024):  # Read in 1 MB chunks
        md.update(chunk)
        content_size += len(chunk)
    return {
        "filename": file.filename,
        "content_size": content_size,
        "sha256": md.hexdigest(),
    }


@_fastapi_app.post("/uploadfile/rawpost")
async def upload_file_rawpost(request: Request):
    """Endpoint to upload a file via raw POST and return its SHA256 hash and size."""
    md = sha256()
    content_size = 0
    async for chunk in request.stream():
        md.update(chunk)
        content_size += len(chunk)
    return {"content_size": content_size, "sha256": md.hexdigest()}
