import asyncio
from solana_client import SolanaClient


async def main():
    client = SolanaClient()
    print(client.public_key)
    print(await client.get_balance())
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
