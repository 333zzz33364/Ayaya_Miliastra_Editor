from __future__ import annotations

"""
ugc_file_tools.incremental_cache

增量构建用的「按输入指纹跳过」缓存。

只缓存 *确定性中间结果*：单图的 GraphModel 导出 JSON（已 enrich 端口类型）。
键由“会影响该结果的全部输入”组成，命中即跳过解析 + 反序列化 + 端口类型 enrich：
- 源码内容 md5（不是 mtime，避免 checkout/复制导致误失效）
- 节点库/图解析代码指纹（`compute_node_defs_fingerprint`）
- 节点坐标缩放、UI 导出记录、UI GUID 注册表快照、active_package_id
- 本模块自身版本号（schema 变更时整体失效）

不使用本缓存的情形：
- 调用方显式传 `use_cache=False`；
- 缓存文件损坏或字段缺失（当作未命中，并删除坏文件）。
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

CACHE_SCHEMA_VERSION = 1


def _cache_dir(workspace_root: Path) -> Path:
    from engine.utils.cache.cache_paths import get_runtime_cache_root

    return get_runtime_cache_root(Path(workspace_root)) / "ugc_file_tools" / "incremental_graph_export"


def compute_file_md5(path: Path) -> str:
    hasher = hashlib.md5()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_export_fingerprint(
    *,
    workspace_root: Path,
    graph_code_file: Path,
    node_pos_scale: float | None,
    ui_export_record_id: str | None,
    ui_guid_registry_snapshot_path: Path | None,
    active_package_id: str | None,
) -> str:
    """计算单图导出结果的输入指纹。"""
    from engine.utils.graph.node_defs_fingerprint import compute_node_defs_fingerprint

    payload: Dict[str, Any] = {
        "schema": int(CACHE_SCHEMA_VERSION),
        "workspace": str(Path(workspace_root).resolve()),
        "source_md5": compute_file_md5(Path(graph_code_file)),
        "node_defs_fp": compute_node_defs_fingerprint(Path(workspace_root)),
        "node_pos_scale": (None if node_pos_scale is None else float(node_pos_scale)),
        "ui_export_record_id": str(ui_export_record_id or ""),
        "ui_guid_registry_snapshot_md5": (
            compute_file_md5(Path(ui_guid_registry_snapshot_path))
            if ui_guid_registry_snapshot_path is not None and Path(ui_guid_registry_snapshot_path).is_file()
            else ""
        ),
        "active_package_id": str(active_package_id or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_path(*, workspace_root: Path, fingerprint: str) -> Path:
    return _cache_dir(workspace_root) / f"{fingerprint}.json"


def load_cached_graph_model_export(
    *,
    workspace_root: Path,
    fingerprint: str,
) -> Optional[Dict[str, Any]]:
    """读取缓存的 GraphModel 导出 payload；未命中/损坏返回 None。"""
    path = _cache_path(workspace_root=Path(workspace_root), fingerprint=str(fingerprint))
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _safe_unlink(path)
        return None
    if not isinstance(doc, dict):
        _safe_unlink(path)
        return None
    if int(doc.get("schema") or 0) != int(CACHE_SCHEMA_VERSION):
        _safe_unlink(path)
        return None
    payload = doc.get("output_payload")
    if not isinstance(payload, dict) or not payload:
        _safe_unlink(path)
        return None
    return payload


def save_cached_graph_model_export(
    *,
    workspace_root: Path,
    fingerprint: str,
    output_payload: Dict[str, Any],
) -> Path:
    """原子写入缓存（同进程并发安全由临时文件 + replace 保证）。"""
    path = _cache_path(workspace_root=Path(workspace_root), fingerprint=str(fingerprint))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    doc = {"schema": int(CACHE_SCHEMA_VERSION), "output_payload": dict(output_payload)}
    temp_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(temp_path, path)
    return path


def _safe_unlink(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


__all__ = [
    "CACHE_SCHEMA_VERSION",
    "compute_export_fingerprint",
    "compute_file_md5",
    "load_cached_graph_model_export",
    "save_cached_graph_model_export",
]
