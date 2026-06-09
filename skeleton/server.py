# TASK 6 EXTENSION: New file. FastAPI/uvicorn server that hosts the Gradio UI and the /ws/notifications WebSocket endpoint.

import asyncio

import gradio as gr
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


@app.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket):
    await notifications.websocket_endpoint(websocket)


@app.on_event("startup")
async def startup_event():
    notifications.set_loop(asyncio.get_running_loop())


# Mount the Gradio UI at "/" using Gradio's helper. This must be done LAST so the
# explicit /ws/notifications route above is matched before Gradio's catch-all, and
# so Gradio's blocks/config are initialised (a raw app.mount("/", demo.app) leaves
# config unset, which makes the index template render fail with a 500).
app = gr.mount_gradio_app(app, demo, path="/")


def run(host: str = "0.0.0.0", port: int = 7860) -> None:
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
