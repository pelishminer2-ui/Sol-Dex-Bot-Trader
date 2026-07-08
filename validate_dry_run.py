"""Short dry-run validation script for the trading bot."""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from bot import TradingBot


async def main():
    bot = TradingBot(dry_run=True)
    await bot.initialize()

    # Run 3 scan cycles (~30 seconds)
    for _ in range(3):
        if not bot.running:
            break
        await bot._refresh_watchlist()
        await bot._try_entry()
        position = bot.strategy.get_open_position()
        if position:
            await bot._monitor_open_position(position)
        await asyncio.sleep(10)

    bot.running = False
    await bot.solana.close()
    print("VALIDATION_OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"VALIDATION_FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
