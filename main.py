import argparse
import asyncio
import logging
import sys

from bot import TradingBot
from config import Config


def parse_args():
    parser = argparse.ArgumentParser(description="Solana Mover Trading Bot")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Simulate trades without signing transactions",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live trading (overrides DRY_RUN env)",
    )
    parser.add_argument(
        "--mainnet",
        action="store_true",
        help="Use mainnet-beta (informational; set SOLANA_NETWORK in .env)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    log_level = args.log_level or Config.LOG_LEVEL
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.live:
        dry_run = False
    elif args.dry_run is True:
        dry_run = True
    else:
        dry_run = Config.DRY_RUN

    if args.mainnet:
        logging.getLogger(__name__).info("Ensure SOLANA_NETWORK=mainnet-beta in .env")

    bot = TradingBot(dry_run=dry_run)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
