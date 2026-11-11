from __future__ import annotations

"""编译期优化 pass 的公共工具。

只做“能证明等价”的变换：任何不确定的情况都返回不可优化，交给调用方跳过。
"""

from typing import Dict, Iterable, List, Optional, Set, Tuple

from engine.graph.common import is_flow_port
from engine.graph.models import EdgeModel, GraphModel, NodeModel

# 结果确定的纯运算节点白名单：title -> (输入端口顺序, 输出端口, 求值函数)
# 只收录“输入全是字面量、输出不受运行态影响”的节点。
_NUMERIC_BINARY = {
    "加法运算": (("左值", "右值"), "结果", lambda a, b: a + b),
    "减法运算": (("左值", "右值"), "结果", lambda a, b: a - b),
    "乘法运算": (("左值", "右值"), "结果", lambda a, b: a * b),
    "模运算": (("被模数", "模数"), "结果", lambda a, b: a % b),
    "取较大值": (("输入1", "输入2"), "较大值", lambda a, b: a if a >= b else b),
    "取较小值": (("输入1", "输入2"), "较小值", lambda a, b: a if a <= b else b),
}

_NUMERIC_UNARY = {
    "绝对值运算": (("输入",), "结果", lambda a: abs(a)),
}

_COMPARE_BINARY = {
    "数值大于": (("左值", "右值"), "结果", lambda a, b: a > b),
    "数值大于等于": (("左值", "右值"), "结果", lambda a, b: a >= b),
    "数值小于": (("左值", "右值"), "结果", lambda a, b: a < b),
    "数值小于等于": (("左值", "右值"), "结果", lambda a, b: a <= b),
    "是否相等": (("输入1", "输入2"), "结果", lambda a, b: a == b),
}

_LOGIC_BINARY = {
    "逻辑与运算": (("输入1", "输入2"), "结果", lambda a, b: bool(a) and bool(b)),
    "逻辑或运算": (("输入1", "输入2"), "结果", lambda a, b: bool(a) or bool(b)),
    "逻辑异或运算": (("输入1", "输入2"), "结果", lambda a, b: bool(a) != bool(b)),
}

_LOGIC_UNARY = {
    "逻辑非运算": (("输入",), "结果", lambda a: not bool(a)),
}

_DIVISION = {"除法运算": (("左值", "右值"), "结果")}

FOLDABLE_BINARY = {**_NUMERIC_BINARY, **_COMPARE_BINARY, **_LOGIC_BINARY}

GET_LOCAL_VAR_TITLE = "获取局部变量"
SET_LOCAL_VAR_TITLE = "设置局部变量"
START_TIMER_TITLE = "启动定时器"
LOCAL_VAR_VALUE_PORT = "值"
LOCAL_VAR_INITIAL_PORT = "初始值"


def node_flow_ports(node: NodeModel) -> Tuple[List[str], List[str]]:
    """返回节点的 (流程输入端口, 流程输出端口)。"""
    flow_inputs = [p.name for p in (node.inputs or []) if is_flow_port(node, p.name, False)]
    flow_outputs = [p.name for p in (node.outputs or []) if is_flow_port(node, p.name, True)]
    return flow_inputs, flow_outputs


def is_pure_data_node(node: NodeModel) -> bool:
    flow_inputs, flow_outputs = node_flow_ports(node)
    return not flow_inputs and not flow_outputs


def build_edge_indexes(model: GraphModel) -> Tuple[Dict[str, List[EdgeModel]], Dict[str, List[EdgeModel]]]:
    """返回 (按源节点分组的出边, 按目标节点分组的入边)。"""
    outgoing: Dict[str, List[EdgeModel]] = {}
    incoming: Dict[str, List[EdgeModel]] = {}
    for edge in model.edges.values():
        outgoing.setdefault(edge.src_node, []).append(edge)
        incoming.setdefault(edge.dst_node, []).append(edge)
    return outgoing, incoming


def node_is_foldable(node: NodeModel) -> bool:
    if not is_pure_data_node(node):
        return False
    title = str(node.title or "").strip()
    return title in FOLDABLE_BINARY or title in _NUMERIC_UNARY or title in _LOGIC_UNARY or title in _DIVISION


def remove_edges(model: GraphModel, edge_ids: Iterable[str]) -> int:
    removed = 0
    for edge_id in list(edge_ids):
        if model.edges.pop(edge_id, None) is not None:
            removed += 1
    if removed:
        model.touch_edges_revision()
    return removed


def drop_stale_block_members(model: GraphModel) -> None:
    """删除已不存在的节点 id 在 basic_blocks / 事件顺序中的残留引用。"""
    existing = set(model.nodes.keys())
    for block in list(getattr(model, "basic_blocks", []) or []):
        block.nodes = [node_id for node_id in list(block.nodes or []) if node_id in existing]
    model.basic_blocks = [b for b in list(model.basic_blocks or []) if list(b.nodes or [])]

    kept_order: List[str] = []
    kept_titles: List[str] = []
    for index, node_id in enumerate(list(model.event_flow_order or [])):
        if node_id in existing:
            kept_order.append(node_id)
            if index < len(model.event_flow_titles or []):
                kept_titles.append(model.event_flow_titles[index])
    model.event_flow_order = kept_order
    if model.event_flow_titles:
        model.event_flow_titles = kept_titles


def numeric_value_ok(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def scalar_value_ok(value: object) -> bool:
    return isinstance(value, (bool, int, float, str))


def event_node_ids(model: GraphModel) -> Set[str]:
    return {node_id for node_id in (model.event_flow_order or []) if node_id in model.nodes}


def flow_reachable_from(model: GraphModel, roots: Iterable[str], outgoing: Dict[str, List[EdgeModel]]) -> Set[str]:
    """沿流程边从 roots 出发可达的节点集合。"""
    reached: Set[str] = set()
    stack = [node_id for node_id in roots if node_id in model.nodes]
    while stack:
        node_id = stack.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        node = model.nodes.get(node_id)
        if node is None:
            continue
        for edge in outgoing.get(node_id, []):
            if edge.dst_node not in model.nodes:
                continue
            if is_flow_port(node, edge.src_port, True):
                stack.append(edge.dst_node)
    return reached


def data_ancestors_of(
    model: GraphModel,
    seeds: Iterable[str],
    incoming: Dict[str, List[EdgeModel]],
) -> Set[str]:
    """沿数据边反向收集 seeds 的数据来源节点（含 seeds 自身）。"""
    reached: Set[str] = set()
    stack = [node_id for node_id in seeds if node_id in model.nodes]
    while stack:
        node_id = stack.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        node = model.nodes.get(node_id)
        if node is None:
            continue
        for edge in incoming.get(node_id, []):
            if edge.src_node not in model.nodes:
                continue
            src_node = model.nodes.get(edge.src_node)
            if src_node is None:
                continue
            if is_flow_port(src_node, edge.src_port, True):
                continue
            stack.append(edge.src_node)
    return reached


def constant_signature(value: object) -> Optional[str]:
    """返回可哈希的常量签名；不可哈希/不确定的值返回 None。"""
    if isinstance(value, bool):
        return f"bool:{value}"
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, float):
        return f"float:{repr(value)}"
    if isinstance(value, str):
        return f"str:{value}"
    if isinstance(value, (list, tuple)):
        parts: List[str] = []
        for item in value:
            inner = constant_signature(item)
            if inner is None:
                return None
            parts.append(inner)
        return "list:[" + ",".join(parts) + "]"
    return None
