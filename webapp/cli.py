import argparse

import uvicorn


def main() -> None:
    """Run the webapp server via Uvicorn.

    Parses CLI arguments and starts Uvicorn with the FastAPI application.

    Returns:
        None
    """
    parser = argparse.ArgumentParser(description="Run the markdownify webapp server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level")
    args = parser.parse_args()

    # Import here so package resolution happens after install
    # and to avoid side-effects during CLI discovery.
    app_path = "webapp.main:app"

    uvicorn.run(
        app_path,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )
