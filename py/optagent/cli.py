"""Build one description by hand:  python -m optagent.cli spec.json out_dir [--no-3d]"""

import json
import sys
from pathlib import Path

from . import pipeline


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    res = pipeline.run(json.loads(Path(args[0]).read_text(encoding="utf-8")), Path(args[1]),
                       render3d="--no-3d" not in sys.argv)
    print(res["summary"])
    for e in res["errors"]:
        print("ERROR:", e)
    for w in res["warnings"]:
        print("WARN:", w)
    print("files:", json.dumps(res["files"], ensure_ascii=False))
    print("timings:", res["timings"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
