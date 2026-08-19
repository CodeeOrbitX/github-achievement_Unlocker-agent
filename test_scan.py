import asyncio
import app

async def main():
    try:
        res = await app.scan_achievements(1)
        print("Success:", res)
    except Exception as e:
        print("Error:", repr(e))

asyncio.run(main())
