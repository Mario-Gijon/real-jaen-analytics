"""Run the development HTTP server using environment configuration."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "video_service.main:app",
        host=os.environ.get("VIDEO_SERVICE_HOST", "127.0.0.1"),
        port=int(os.environ.get("VIDEO_SERVICE_PORT", "8010")),
    )


if __name__ == "__main__":
    main()
