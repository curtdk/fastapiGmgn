"""测试 WS user_status 推送"""
import asyncio
import json
import sys
import websockets

sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")


async def listen(seconds=8):
    uri = "ws://localhost:8000/ws/trades/75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump"
    try:
        async with websockets.connect(uri) as ws:
            print("Connected to", uri)
            deadline = asyncio.get_event_loop().time() + seconds
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=2)
                    data = json.loads(msg)
                    t = data.get("type")
                    if t == "user_status":
                        d = data.get("data", {})
                        print(f"\n[user_status] address={d.get('address', '')[:8]}... status={d.get('status')}")
                        print(f"  holding_qty={d.get('holding_qty')} holding_cost={d.get('holding_cost')}")
                        print(f"  cluster_name={d.get('cluster_name')} cluster_type={d.get('cluster_type')}")
                    elif t:
                        print(f"[{t}]")
                except asyncio.TimeoutError:
                    continue
            print("\nDone listening")
    except Exception as e:
        print(f"WS error: {e}")


if __name__ == "__main__":
    asyncio.run(listen(10))