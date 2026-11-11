from __future__ import annotations

"""Pass 1：常量折叠。

把“所有数据输入都是常量”的纯运算节点求值，写成下游端口的常量，然后删除该节点。

等价性前提（缺一不可）：
- 节点是纯数据节点（无流程端口），且 title 在白名单内；
- 每个数据输入端口都没有入边，且 `input_constants` 里存在可比较的字面量；
- 每个输出端口的每条出边都指向真实存在的目标节点与输入端口，并且该目标端口没有其它入边。
不满足时跳过该节点，不做部分折叠。
"""

from typing import Any, Dict, List, Optional, Set

from engine.graph.models import GraphModel

from .common import (
    FOLDABLE_BINARY,
    _DIVISION,
    _LOGIC_UNARY,
    _NUMERIC_UNARY,
    build_edge_indexes,
    is_pure_data_node,
    node_is_foldable,
    remove_edges,
    scalar_value_ok,
)


def _evaluate(title: str, inputs: Dict[str, Any]) -> Optional[Any]:
    if title in _DIVISION:
        left_key, right_key = _DIVISION[title][0]
        left = inputs.get(left_key)
        right = inputs.get(right_key)
        if not isinstance(left, (int, float)) or isinstance(left, bool):
            return None
        if not isinstance(right, (int, float)) or isinstance(right, bool):
            return None
        if float(right) == 0.0:
            return None
        return left / right

    spec = FOLDABLE_BINARY.get(title) or _NUMERIC_UNARY.get(title) or _LOGIC_UNARY.get(title)
    if spec is None:
        return None
    port_names, output_name, func = spec  # type: ignore[misc]
    args: List[Any] = []
    for name in port_names:
        if name not in inputs:
            return None
        value = inputs[name]
        if isinstance(value, (dict, tuple)):
            return None
        if value is None:
            return None
        args.append(value)
    try:
        result = func(*args)  # type: ignore[misc]
    except Exception:
        return None
    if not scalar_value_ok(result):
        return None
    return result


def run_constant_folding(model: GraphModel, node_library: Dict[str, Any] | None = None) -> Dict[str, Any]:
    removed_nodes: List[str] = []
    folded = 0

    # 折叠会把上游结果写成下游常量，可能让下游也变成可折叠节点：迭代到不再变化。
    changed = True
    while changed:
        changed = False
        outgoing, incoming = build_edge_indexes(model)
        for node_id in list(model.nodes.keys()):
            node = model.nodes.get(node_id)
            if node is None or not node_is_foldable(node):
                continue
            if not is_pure_data_node(node):
                continue

            input_edges = [e for e in incoming.get(node_id, [])]
            if input_edges:
                continue

            title = str(node.title or "").strip()
            values: Dict[str, Any] = {}
            ok = True
            for port in node.inputs or []:
                if is_flow_port_name_guard(node, port.name):
                    ok = False
                    break
                if port.name not in (node.input_constants or {}):
                    ok = False
                    break
                values[port.name] = node.input_constants[port.name]
            if not ok:
                continue

            result = _evaluate(title, values)
            if result is None:
                continue

            output_name = _resolve_output_port(title, node)
            if output_name is None:
                continue

            node_edges = [e for e in outgoing.get(node_id, []) if e.src_port == output_name]
            other_edges = [e for e in outgoing.get(node_id, []) if e.src_port != output_name]
            if other_edges:
                continue

            replacements = _prepare_replacements(model, node_edges, incoming)
            if replacements is None:
                continue
            if not _value_matches_destination_types(model, replacements, result, node_library):
                continue

            for dst_node_id, dst_port in replacements:
                dst_node = model.nodes.get(dst_node_id)
                if dst_node is None:
                    continue
                dst_node.input_constants = dict(dst_node.input_constants or {})
                dst_node.input_constants[dst_port] = result

            remove_edges(model, [e.id for e in node_edges])
            model.nodes.pop(node_id, None)
            removed_nodes.append(node_id)
            folded += 1
            changed = True

    return {"pass": "constant_fold", "folded_nodes": folded, "removed_node_ids": removed_nodes}


def _resolve_output_port(title: str, node) -> Optional[str]:
    spec = FOLDABLE_BINARY.get(title) or _NUMERIC_UNARY.get(title) or _LOGIC_UNARY.get(title) or _DIVISION.get(title)
    if spec is None:
        return None
    output_name = spec[1]
    if not any(port.name == output_name for port in (node.outputs or [])):
        return None
    return output_name


def _prepare_replacements(
    model: GraphModel,
    node_edges,
    incoming: Dict[str, List[Any]],
) -> Optional[List[tuple]]:
    """确认每条出边都能安全替换成常量；返回 [(dst_node_id, dst_port), ...]。"""
    seen: Set[tuple] = set()
    replacements: List[tuple] = []
    for edge in node_edges:
        dst_node = model.nodes.get(edge.dst_node)
        if dst_node is None:
            return None
        if not any(port.name == edge.dst_port and port.is_input for port in (dst_node.inputs or [])):
            return None
        dst_in_edges = [e for e in incoming.get(edge.dst_node, []) if e.dst_port == edge.dst_port]
        if len(dst_in_edges) != 1:
            # 同一目标端口存在多条入边：不折叠，避免覆盖其它来源。
            return None
        key = (edge.dst_node, edge.dst_port)
        if key in seen:
            return None
        seen.add(key)
        replacements.append(key)
    return replacements


def is_flow_port_name_guard(node, port_name: str) -> bool:
    from engine.graph.common import is_flow_port

    return is_flow_port(node, port_name, False)


# 折叠结果与目标端口有效类型的兼容表（只处理可确定的基础类型；泛型/枚举/结构体一律不折叠）。
_VALUE_TYPE_BY_PYTHON = {
    bool: "布尔值",
    int: "整数",
    float: "浮点数",
    str: "字符串",
}
_COMPATIBLE_TARGET_TYPES = {
    "布尔值": {"布尔值"},
    "整数": {"整数", "浮点数"},
    "浮点数": {"浮点数", "整数"},
    "字符串": {"字符串"},
}


def _value_matches_destination_types(
    model: GraphModel,
    replacements: List[tuple],
    value,
    node_library: Dict[str, Any] | None,
) -> bool:
    """确认折叠结果能被每个目标端口接受；类型无法确定（泛型/查不到 NodeDef）时保守放弃。"""
    value_type = _VALUE_TYPE_BY_PYTHON.get(type(value))
    if value_type is None:
        return False
    allowed = _COMPATIBLE_TARGET_TYPES.get(value_type)
    if not allowed:
        return False

    for dst_node_id, dst_port in replacements:
        dst_node = model.nodes.get(dst_node_id)
        if dst_node is None:
            return False
        declared = _resolve_input_port_type(dst_node, dst_port, node_library)
        if declared == "":
            return False
        if declared not in allowed:
            return False
    return True


def _resolve_input_port_type(node, port_name: str, node_library: Dict[str, Any] | None) -> str:
    """优先用节点自带的类型快照；IR 阶段快照尚未补齐时回退查 NodeDef。"""
    declared = str((getattr(node, "effective_input_types", {}) or {}).get(port_name) or "").strip()
    if declared != "":
        return declared
    if not node_library:
        return ""
    node_def_ref = getattr(node, "node_def_ref", None)
    key = str(getattr(node_def_ref, "key", "") or "").strip()
    if key == "":
        return ""
    node_def = node_library.get(key)
    if node_def is None:
        return ""
    input_types = getattr(node_def, "input_types", None)
    if not isinstance(input_types, dict):
        return ""
    return str(input_types.get(port_name) or "").strip()
