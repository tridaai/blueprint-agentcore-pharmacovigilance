"""Serve the AgentCore Runtime app: ``pv-agent-serve``.

This is the container entrypoint in the Dockerfile. Locally it binds
the same 8080 contract AgentCore Runtime expects in production."""

from __future__ import annotations

from pv_cases.config import Settings
from pv_cases.logging_config import configure_logging
from pv_cases.runtime_app import app


def main() -> None:
    configure_logging()
    settings = Settings.from_env()
    app.run(port=settings.runtime_port, host="0.0.0.0")


if __name__ == "__main__":
    main()
