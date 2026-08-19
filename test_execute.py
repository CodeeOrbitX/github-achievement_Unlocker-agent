import asyncio
import app
import json
from pydantic import BaseModel

class ExecuteReq(BaseModel):
    account_id: int = 1
    second_account_id: int | None = None
    dry_run: bool = True
    achievement_keys: list[str] | None = ["quickdraw"]

async def main():
    try:
        req = ExecuteReq()
        res = await app.execute(req)
        print("Success:")
        from fastapi.encoders import jsonable_encoder
        json.dumps(jsonable_encoder(res))
        print("JSON serialization passed!")
    except Exception as e:
        print("Error:", repr(e))

asyncio.run(main())
