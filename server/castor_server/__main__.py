import uvicorn

from .config import settings


def main() -> None:
    uvicorn.run(
        "castor_server.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
