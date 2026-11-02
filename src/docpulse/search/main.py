"""``python -m docpulse.search`` — run the API with uvicorn."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DocPulse search API")
    parser.add_argument("--host", default=None, help="default: SEARCH_HOST or 0.0.0.0")
    parser.add_argument("--port", type=int, default=None, help="default: SEARCH_PORT or 8080")
    parser.add_argument("--log-level", default="info")
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="skip loading the model at startup (first query pays ~10 s instead)",
    )
    args = parser.parse_args(argv)

    from .api import create_app
    from .config import SearchSettings
    from .query import warmup

    settings = SearchSettings.from_env()
    app = create_app(settings=settings)

    if not args.no_warmup:
        try:
            warmup(app.state.query_encoder, ("warmup",))
        except Exception as exc:  # keep serving; /health will show the truth
            print(f"warmup failed ({type(exc).__name__}: {exc}) — continuing", file=sys.stderr)

    import uvicorn

    uvicorn.run(
        app,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
