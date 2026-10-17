"""Screenshot the local Streamlit UI through headless Chrome (CDP). Dev helper for the README images.

  docker run -d --rm --name p4-shot -p 127.0.0.1:9222:9222 zenika/alpine-chrome:124 --no-sandbox \
      --remote-debugging-address=0.0.0.0 --remote-debugging-port=9222 about:blank
  python scripts/screenshot_ui.py http://host.docker.internal:8504/ results/ui_label.png demo_labeler
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import urllib.request

import websockets


async def main(url: str, out: str, labeler: str) -> None:
    tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
    ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
    async with websockets.connect(ws_url, max_size=50_000_000) as ws:
        n = 0

        async def cmd(method: str, **params: object) -> dict[str, object]:
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == n:
                    return dict(msg.get("result", {}))

        await cmd("Emulation.setDeviceMetricsOverride", width=1400, height=1300, deviceScaleFactor=1, mobile=False)
        await cmd("Page.enable")
        await cmd("Page.navigate", url=url)
        await asyncio.sleep(15)
        if labeler:
            await cmd("Runtime.evaluate", expression="document.querySelector('[data-testid=stSidebar] input').focus()")
            await cmd("Input.insertText", text=labeler)
            await cmd("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13)
            await cmd("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)
            await asyncio.sleep(10)
        shot = await cmd("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        with open(out, "wb") as fh:
            fh.write(base64.b64decode(str(shot["data"])))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
