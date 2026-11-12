"""Run the API server."""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("LAB_HOST", "127.0.0.1"),
        port=int(os.environ.get("LAB_PORT", "8000")),
        reload=False,
    )
