import asyncio

import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from skeleton.notifications import notifications
from skeleton.ui import demo

app = FastAPI(title="TransitFlow")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/", demo.app)


@app.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket):
    await notifications.websocket_endpoint(websocket)


@app.on_event("startup")
async def startup_event():
    notifications.set_loop(asyncio.get_running_loop())


def run(host: str = "0.0.0.0", port: int = 7860) -> None:
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
