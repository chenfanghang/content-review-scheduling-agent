"""从本地 JSON 文件读取合成素材和排期。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class LocalCatalog:
    """读取本地素材和排期数据。"""

    def materials(self):
        return json.loads((ROOT / "data/materials.json").read_text())

    def schedule(self):
        return json.loads((ROOT / "data/schedule.json").read_text())
