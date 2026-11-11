from __future__ import annotations

"""
ugc_file_tools.writeback_preflight

写回端硬校验（导出前 fail-closed）：

- 只做“读 payload → 检查不变量 → 抛错”的纯校验，不负责修复、不写盘。
- 由 `node_graph_writeback` 与 `signal_writeback` 的写盘入口调用，避免同一份不合规
  payload 被写出后在官方编辑器/游戏侧静默失败。

校验项按“真实存档样本可证”取舍：
1. `section10['7']` 必须与 base 原值一致（编辑器保存的多图存档恒为 1，改写成 group 数会被静默拒绝）。
2. 节点图 `graph_id` 不得重复。
3. 客户端脚本信号定义的独立信号索引（node_def `4/5`）不得重复。
4. 图中不得残留通用 `300005` 客户端发送节点（必须使用信号专属 node_def）。
5. 同一挂载槽内不得重复挂载同一 `graph_id`。

对真实编辑器存档中确实存在的形态（同一槽多条定位记录、`field1 != 1`、以及种子模板里的
悬空图引用）只记 warning，不阻断写盘。
"""

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import binary_data_text_to_numeric_message
from ugc_file_tools.node_graph_writeback.writeback_feature_flags import is_writeback_feature_enabled

_PREFLIGHT_FEATURE_NAME = "preflight"

_SECTION_NODE_GRAPH = "10"
_SECTION_ENTITY_PLACEMENT = "5"

_CLIENT_SEND_NODE_TITLE = "发送客户端脚本信号"
_GENERIC_CLIENT_SEND_RUNTIME_ID = 300005

_MOUNT_COMPONENT_SLOT_ID = 3
_MOUNT_FIELD_KEY = "13"


class WritebackPreflightError(ValueError):
    """写回前硬校验失败：payload 不满足可导出不变量，拒绝写盘。"""

    def __init__(self, *, issues: Sequence[Mapping[str, Any]], report: Mapping[str, Any]) -> None:
        lines = ["写回前校验失败，已拒绝写盘："]
        for item in list(issues):
            lines.append(f"- [{item.get('code')}] {item.get('message')}")
        lines.append("排查方向见 ugc_file_tools/writeback_preflight.py 顶部说明。")
        super().__init__("\n".join(lines))
        self.issues: List[Dict[str, Any]] = [dict(x) for x in list(issues)]
        self.report: Dict[str, Any] = dict(report)


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return []


def _decode_message(value: Any) -> Any:
    """把 `<binary_data>` 文本解码为数值键 message；其余原样返回。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.startswith("<binary_data>"):
        try:
            return binary_data_text_to_numeric_message(value)
        except Exception:
            return None
    return None


def _first_dict(value: Any) -> Optional[Dict[str, Any]]:
    for item in _as_list(value):
        if isinstance(item, dict):
            return item
        decoded = _decode_message(item)
        if isinstance(decoded, dict):
            return decoded
    return None


def _extract_int_field(container: Any, key: str) -> Optional[int]:
    if not isinstance(container, Mapping):
        return None
    value = container.get(key)
    if isinstance(value, int) and type(value) is int:
        return int(value)
    if isinstance(value, Mapping) and isinstance(value.get("int"), int):
        return int(value["int"])
    return None


def _iter_graph_entries(section10: Mapping[str, Any]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for group in _as_list(section10.get("1")):
        group_obj = _decode_message(group)
        if not isinstance(group_obj, Mapping):
            continue
        for entry in _as_list(group_obj.get("1")):
            entry_obj = _decode_message(entry)
            if isinstance(entry_obj, Mapping):
                entries.append(dict(entry_obj))
    return entries


def _graph_id_from_entry(entry: Mapping[str, Any]) -> Optional[int]:
    header = _first_dict(entry.get("1"))
    return _extract_int_field(header, "5")


def _iter_graph_nodes(entry: Mapping[str, Any]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for node in _as_list(entry.get("3")):
        node_obj = _decode_message(node)
        if isinstance(node_obj, Mapping):
            nodes.append(dict(node_obj))
    return nodes


def _iter_node_defs(section10: Mapping[str, Any]) -> List[Dict[str, Any]]:
    defs: List[Dict[str, Any]] = []
    for wrapper in _as_list(section10.get("2")):
        wrapper_obj = _decode_message(wrapper)
        if not isinstance(wrapper_obj, Mapping):
            continue
        inner = _decode_message(wrapper_obj.get("1"))
        if isinstance(inner, Mapping):
            defs.append(dict(inner))
    return defs


def _iter_entity_entries(payload_root: Mapping[str, Any]) -> List[Dict[str, Any]]:
    section5 = payload_root.get(_SECTION_ENTITY_PLACEMENT)
    if not isinstance(section5, Mapping):
        return []
    entries: List[Dict[str, Any]] = []
    for entry in _as_list(section5.get("1")):
        entry_obj = _decode_message(entry)
        if isinstance(entry_obj, Mapping):
            entries.append(dict(entry_obj))
    return entries


def _component_slot_id(component: Mapping[str, Any]) -> Optional[int]:
    return _extract_int_field(component, "1")


def _mount_locator_graph_ids(component: Mapping[str, Any]) -> List[int]:
    """读取挂载槽内的全部定位记录，返回其 graph_id 列表（按记录顺序）。"""
    mount = component.get(_MOUNT_FIELD_KEY)
    if not isinstance(mount, Mapping):
        return []
    graph_ids: List[int] = []
    for wrapper in _as_list(mount.get("1")):
        wrapper_obj = _decode_message(wrapper)
        if not isinstance(wrapper_obj, Mapping):
            continue
        locator = _first_dict(wrapper_obj.get("1"))
        graph_id = _extract_int_field(locator, "2")
        if isinstance(graph_id, int):
            graph_ids.append(int(graph_id))
    return graph_ids


def _mount_locator_flags(component: Mapping[str, Any]) -> List[Optional[int]]:
    mount = component.get(_MOUNT_FIELD_KEY)
    if not isinstance(mount, Mapping):
        return []
    flags: List[Optional[int]] = []
    for wrapper in _as_list(mount.get("1")):
        wrapper_obj = _decode_message(wrapper)
        if not isinstance(wrapper_obj, Mapping):
            continue
        locator = _first_dict(wrapper_obj.get("1"))
        flags.append(_extract_int_field(locator, "1"))
    return flags


def _issue(code: str, message: str, **detail: Any) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": str(code), "message": str(message)}
    if detail:
        item["detail"] = dict(detail)
    return item


def read_section10_field7_from_gil(gil_path: Path) -> Optional[int]:
    """
    轻量读取 `.gil` 的 `section10['7']` 原值（只做两层浅解码，不解整份 payload）。

    返回 None 表示 base 没有 section10 或没有该字段（此时不做“保持原值”校验）。
    """
    from ugc_file_tools.gil_dump_codec.protobuf_like import decode_message_to_field_map
    from ugc_file_tools.gil_package_exporter.gil_reader import read_gil_header

    data = Path(gil_path).read_bytes()
    header = read_gil_header(data)
    body_size = int(header.body_size)
    if body_size <= 0:
        return None
    payload = data[20 : 20 + body_size]
    top, _consumed = decode_message_to_field_map(
        data_bytes=payload,
        start_offset=0,
        end_offset=len(payload),
        remaining_depth=1,
    )
    ten = top.get("field_10")
    if not isinstance(ten, Mapping):
        return None

    section10: Any = None
    raw_hex = ten.get("raw_hex")
    if isinstance(raw_hex, str) and raw_hex:
        section10_bytes = bytes.fromhex(raw_hex)
        section10, _ = decode_message_to_field_map(
            data_bytes=section10_bytes,
            start_offset=0,
            end_offset=len(section10_bytes),
            remaining_depth=1,
        )
    else:
        nested = ten.get("message")
        if isinstance(nested, Mapping):
            section10 = {str(k).replace("field_", ""): v for k, v in nested.items() if str(k).startswith("field_")}

    if not isinstance(section10, Mapping):
        return None
    return _extract_int_field(section10, "field_7")


def collect_writeback_preflight_issues(
    *,
    payload_root: Mapping[str, Any],
    base_section10_field7: Optional[int] = None,
) -> Dict[str, Any]:
    """收集写回前校验结果：`{"issues": [...], "warnings": [...], "stats": {...}}`。"""
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    section10_value = payload_root.get(_SECTION_NODE_GRAPH)
    section10: Mapping[str, Any] = section10_value if isinstance(section10_value, Mapping) else {}

    # 1) section10['7'] 必须与 base 保持一致。
    output_field7 = _extract_int_field(section10, "7")
    if isinstance(base_section10_field7, int):
        if output_field7 != int(base_section10_field7):
            issues.append(
                _issue(
                    "section10_field7_changed",
                    f"section10['7'] 被改动：base={int(base_section10_field7)} output={output_field7!r}；"
                    "该字段不是 group 计数，改写会被游戏侧静默拒绝。",
                    base=int(base_section10_field7),
                    output=output_field7,
                )
            )
    elif isinstance(output_field7, int) and len(_as_list(section10.get("1"))) > 0:
        if int(output_field7) != 1:
            warnings.append(
                _issue(
                    "section10_field7_not_one",
                    f"新建节点图段时 section10['7']={int(output_field7)}（真实编辑器存档恒为 1）；"
                    "建议与编辑器口径对齐。",
                    output=int(output_field7),
                )
            )

    # 2) graph_id 不得重复。
    entries = _iter_graph_entries(section10)
    graph_ids = [gid for gid in (_graph_id_from_entry(e) for e in entries) if isinstance(gid, int)]
    duplicated_graph_ids = sorted({gid for gid in graph_ids if graph_ids.count(gid) > 1})
    if duplicated_graph_ids:
        issues.append(
            _issue(
                "duplicate_graph_id",
                f"节点图 graph_id 重复：{duplicated_graph_ids}；同一 graph_id 出现在多张 GraphEntry 会导致覆盖/错挂。",
                graph_ids=duplicated_graph_ids,
            )
        )

    # 3) 客户端脚本信号的独立索引不得重复。
    client_signal_indices: Dict[int, List[str]] = {}
    for node_def in _iter_node_defs(section10):
        if str(node_def.get("200") or "").strip() != _CLIENT_SEND_NODE_TITLE:
            continue
        meta = _decode_message(node_def.get("4"))
        index = _extract_int_field(meta, "5")
        if not isinstance(index, int):
            continue
        binding = _decode_message(node_def.get("107"))
        client_binding = _decode_message(binding.get("108")) if isinstance(binding, Mapping) else None
        name = str(client_binding.get("1") or "").strip() if isinstance(client_binding, Mapping) else ""
        client_signal_indices.setdefault(int(index), []).append(name)
    duplicated_client_indices = {
        index: names for index, names in client_signal_indices.items() if len(names) > 1
    }
    if duplicated_client_indices:
        issues.append(
            _issue(
                "duplicate_client_signal_index",
                "客户端脚本信号索引重复："
                + "；".join(
                    f"index={index} → {sorted(set(names))}"
                    for index, names in sorted(duplicated_client_indices.items())
                ),
                duplicates={int(k): sorted(set(v)) for k, v in duplicated_client_indices.items()},
            )
        )

    # 4) 不得残留通用 300005 客户端发送节点（必须使用信号专属 node_def）。
    generic_nodes: List[Dict[str, Any]] = []
    for entry in entries:
        gid = _graph_id_from_entry(entry)
        for node in _iter_graph_nodes(entry):
            for field in ("2", "3"):
                node_prop = _decode_message(node.get(field))
                if not isinstance(node_prop, Mapping):
                    continue
                if _extract_int_field(node_prop, "5") == _GENERIC_CLIENT_SEND_RUNTIME_ID:
                    generic_nodes.append({"graph_id": gid, "node_id": _extract_int_field(node, "1")})
                    break
    if generic_nodes:
        issues.append(
            _issue(
                "generic_client_send_node",
                f"存在通用 300005 客户端发送节点 {len(generic_nodes)} 处，必须改写成信号专属 node_def，"
                "否则编辑器会报“服务器信号设置错误”。",
                nodes=generic_nodes[:10],
                total=len(generic_nodes),
            )
        )

    # 5) 同一挂载槽内不得重复挂载同一 graph_id。
    known_graph_ids = set(graph_ids)
    duplicate_mounts: List[Dict[str, Any]] = []
    dangling_mounts: List[Dict[str, Any]] = []
    non_enabled_locators = 0
    for entry in _iter_entity_entries(payload_root):
        instance_id = _extract_int_field(entry, "1")
        for component in _as_list(entry.get("6")):
            component_obj = _decode_message(component)
            if not isinstance(component_obj, Mapping):
                continue
            if _component_slot_id(component_obj) != _MOUNT_COMPONENT_SLOT_ID:
                continue
            mounted = _mount_locator_graph_ids(component_obj)
            if not mounted:
                continue
            duplicated = sorted({gid for gid in mounted if mounted.count(gid) > 1})
            if duplicated:
                duplicate_mounts.append({"instance_id": instance_id, "graph_ids": duplicated})
            for gid in mounted:
                if known_graph_ids and gid not in known_graph_ids:
                    dangling_mounts.append({"instance_id": instance_id, "graph_id": gid})
            for flag in _mount_locator_flags(component_obj):
                if isinstance(flag, int) and int(flag) != 1:
                    non_enabled_locators += 1

    if duplicate_mounts:
        issues.append(
            _issue(
                "duplicate_mount_graph_id",
                f"同一挂载槽内重复挂载同一节点图：{len(duplicate_mounts)} 处。",
                slots=duplicate_mounts[:10],
                total=len(duplicate_mounts),
            )
        )
    if dangling_mounts:
        warnings.append(
            _issue(
                "dangling_mount_graph_id",
                f"挂载引用了 section10 中不存在的 graph_id：{len(dangling_mounts)} 处（多为种子模板样本，非本次写回产物）。",
                sample=dangling_mounts[:5],
                total=len(dangling_mounts),
            )
        )
    if non_enabled_locators:
        warnings.append(
            _issue(
                "mount_locator_flag_not_one",
                f"挂载定位记录的 field1 != 1：{int(non_enabled_locators)} 处（真实编辑器存档存在该形态，仅提示）。",
                total=int(non_enabled_locators),
            )
        )

    return {
        "issues": issues,
        "warnings": warnings,
        "stats": {
            "graph_entries": len(entries),
            "graph_ids": len(graph_ids),
            "client_signal_defs": sum(len(v) for v in client_signal_indices.values()),
        },
    }


def run_writeback_preflight_or_raise(
    *,
    payload_root: Mapping[str, Any],
    base_section10_field7: Optional[int] = None,
    context: str = "",
) -> Dict[str, Any]:
    """
    写回前硬校验入口：存在 issues 时抛 `WritebackPreflightError`（调用方不得继续写盘）。

    - 可用 `UGC_WB_DISABLE=preflight`（或 `all`）临时关闭，用于二分定位。
    - 返回 report（含 warnings），便于调用方落盘或展示。
    """
    if not is_writeback_feature_enabled(_PREFLIGHT_FEATURE_NAME):
        return {"disabled": True, "issues": [], "warnings": [], "stats": {}}

    report = collect_writeback_preflight_issues(
        payload_root=payload_root,
        base_section10_field7=base_section10_field7,
    )
    issues = list(report.get("issues") or [])
    if issues:
        report["context"] = str(context)
        raise WritebackPreflightError(issues=issues, report=report)
    if context:
        report["context"] = str(context)
    return report


__all__ = [
    "WritebackPreflightError",
    "collect_writeback_preflight_issues",
    "read_section10_field7_from_gil",
    "run_writeback_preflight_or_raise",
]
