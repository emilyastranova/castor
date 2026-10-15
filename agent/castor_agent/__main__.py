import asyncio
import logging
import sys

from .agent import Agent
from .config import AgentConfig


def main() -> None:
    config = AgentConfig.from_args()
    logging.basicConfig(
        level=config.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    agent = Agent(config)
    try:
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
