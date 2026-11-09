"""Long-lived worker for the Node server: one JSON request per stdin line, one
JSON reply per stdout line. Keeps Qt and pyvista loaded between builds.

    {"id": 1, "cmd": "build", "spec": {...}, "out_dir": "...", "render3d": true}
    -> {"id": 1, "ok": true, "result": {...}}  or  {"id": 1, "ok": false, "error": "..."}
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def main() -> None:
    out = sys.stdout
    sys.stdout = sys.stderr  # library prints must not corrupt the protocol
    from . import pipeline  # slow imports (Qt, optolayout) after the redirect
    out.write(json.dumps({"ready": True}) + "\n")
    out.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            if req.get("cmd") == "build":
                result = pipeline.run(req["spec"], Path(req["out_dir"]), bool(req.get("render3d", True)))
                reply = {"id": rid, "ok": True, "result": result}
            elif req.get("cmd") == "ping":
                reply = {"id": rid, "ok": True, "result": "pong"}
            else:
                reply = {"id": rid, "ok": False, "error": f"unknown cmd {req.get('cmd')!r}"}
        except Exception as e:  # report and keep serving
            traceback.print_exc()
            reply = {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"}
        out.write(json.dumps(reply, ensure_ascii=False) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
