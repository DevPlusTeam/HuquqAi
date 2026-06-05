import os

import uvicorn


HOST = os.getenv("ROBOT_HOST", "127.0.0.1")
PORT = int(os.getenv("ROBOT_PORT", os.getenv("PORT", "8000")))
RELOAD = os.getenv("ROBOT_RELOAD", "0").lower() in {"1", "true", "yes"}


if __name__ == "__main__":
    uvicorn.run("app:app", host=HOST, port=PORT, reload=RELOAD)
