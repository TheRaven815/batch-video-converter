from __future__ import annotations

import json
import sys
from pathlib import Path

from video_converter.api.main import app


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: export_openapi.py OUTPUT_PATH")
    output_path = Path(sys.argv[1])
    output_path.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
