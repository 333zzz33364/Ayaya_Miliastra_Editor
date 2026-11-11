from __future__ import annotations

"""增量构建缓存：指纹稳定、命中/失效、坏文件自愈。

不依赖任何真实项目存档，只测缓存层本身。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

EDITOR_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDITOR_ROOT / "private_extensions"))

from ugc_file_tools.incremental_cache import (
    CACHE_SCHEMA_VERSION,
    compute_file_md5,
    load_cached_graph_model_export,
    save_cached_graph_model_export,
)


class IncrementalCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="incremental-cache-")
        self.addCleanup(self._temp.cleanup)
        self.workspace = Path(self._temp.name)

    def _payload(self, name: str = "图") -> dict:
        return {"graph_id": "g1", "name": name, "data": {"nodes": [{"id": "n1"}], "edges": []}}

    def test_roundtrip_hit(self) -> None:
        save_cached_graph_model_export(
            workspace_root=self.workspace,
            fingerprint="fp1",
            output_payload=self._payload(),
        )
        got = load_cached_graph_model_export(workspace_root=self.workspace, fingerprint="fp1")
        self.assertIsInstance(got, dict)
        self.assertEqual(got["name"], "图")

    def test_miss_returns_none(self) -> None:
        self.assertIsNone(load_cached_graph_model_export(workspace_root=self.workspace, fingerprint="nope"))

    def test_corrupted_file_is_removed_and_missed(self) -> None:
        save_cached_graph_model_export(
            workspace_root=self.workspace,
            fingerprint="fp2",
            output_payload=self._payload(),
        )
        cache_file = next(
            (self.workspace / "app/runtime/cache/ugc_file_tools/incremental_graph_export").glob("fp2.json")
        )
        cache_file.write_text("{not json", encoding="utf-8")
        self.assertIsNone(load_cached_graph_model_export(workspace_root=self.workspace, fingerprint="fp2"))
        self.assertFalse(cache_file.exists())

    def test_schema_mismatch_is_removed(self) -> None:
        save_cached_graph_model_export(
            workspace_root=self.workspace,
            fingerprint="fp3",
            output_payload=self._payload(),
        )
        cache_dir = self.workspace / "app/runtime/cache/ugc_file_tools/incremental_graph_export"
        cache_file = next(cache_dir.glob("fp3.json"))
        doc = json.loads(cache_file.read_text(encoding="utf-8"))
        doc["schema"] = int(CACHE_SCHEMA_VERSION) + 1
        cache_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(load_cached_graph_model_export(workspace_root=self.workspace, fingerprint="fp3"))
        self.assertFalse(cache_file.exists())

    def test_overwrite_same_fingerprint(self) -> None:
        save_cached_graph_model_export(workspace_root=self.workspace, fingerprint="fp4", output_payload=self._payload("旧"))
        save_cached_graph_model_export(workspace_root=self.workspace, fingerprint="fp4", output_payload=self._payload("新"))
        got = load_cached_graph_model_export(workspace_root=self.workspace, fingerprint="fp4")
        self.assertEqual(got["name"], "新")

    def test_file_md5_changes_with_content(self) -> None:
        path = self.workspace / "a.py"
        path.write_text("print(1)\n", encoding="utf-8")
        first = compute_file_md5(path)
        path.write_text("print(2)\n", encoding="utf-8")
        second = compute_file_md5(path)
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
