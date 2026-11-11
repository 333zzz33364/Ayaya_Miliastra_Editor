from __future__ import annotations

"""Pass 3：局部变量复用。

把“同一个事件流内、初始值相同、且从未被 设置局部变量 覆写”的多个
【获取局部变量】合并成一个：把重复节点的 `值` 出边改接到保留节点的 `值` 输出。

等价性前提（缺一不可）：
- 候选节点的 `初始值` 端口没有入边，且 `input_constants` 内有字面量；
- 候选节点的 `局部变量` 输出端口没有任何出边（即没有接到任何 设置局部变量）；
- 各候选节点的 `初始值` 常量签名相同，且 `值` 的有效类型一致。
任何一条不满足就保留原样。
"""

from typing import Any, Dict, List

from engine.graph.models import GraphModel

from .common import (
    GET_LOCAL_VAR_TITLE,
    LOCAL_VAR_INITIAL_PORT,
    LOCAL_VAR_VALUE_PORT,
    build_edge_indexes,
    constant_signature,
    remove_edges,
)


def run_local_var_reuse(model: GraphModel) -> Dict[str, Any]:
    outgoing, incoming = build_edge_indexes(model)
    groups: Dict[tuple, List[str]] = {}

    for node_id, node in model.nodes.items():
        if str(node.title or "").strip() != GET_LOCAL_VAR_TITLE:
            continue
        if incoming.get(node_id):
            # 初始值来自上游计算时不合并，避免把计算点搬走。
            continue
        constants = dict(node.input_constants or {})
        if LOCAL_VAR_INITIAL_PORT not in constants:
            continue
        signature = constant_signature(constants.get(LOCAL_VAR_INITIAL_PORT))
        if signature is None:
            continue
        value_type = str((node.effective_output_types or {}).get(LOCAL_VAR_VALUE_PORT) or "")
        groups.setdefault((signature, value_type), []).append(node_id)

    merged = 0
    removed_ids: List[str] = []
    for _key, node_ids in groups.items():
        if len(node_ids) < 2:
            continue

        # 只能保留“没有被设置局部变量连接”的节点作为合并目标。
        candidates = [
            node_id
            for node_id in sorted(node_ids)
            if not [e for e in outgoing.get(node_id, []) if e.src_port == "局部变量"]
        ]
        if len(candidates) < 2:
            continue
        keeper_id = candidates[0]
        keeper = model.nodes.get(keeper_id)
        if keeper is None:
            continue

        for node_id in candidates[1:]:
            node = model.nodes.get(node_id)
            if node is None:
                continue
            value_edges = [e for e in outgoing.get(node_id, []) if e.src_port == LOCAL_VAR_VALUE_PORT]
            if not value_edges:
                # 没有任何消费者：交给死代码 pass 处理，不在这里删。
                continue
            if len(value_edges) != len(outgoing.get(node_id, [])):
                continue

            ok = True
            for edge in value_edges:
                dst_node = model.nodes.get(edge.dst_node)
                if dst_node is None or not any(
                    port.name == edge.dst_port and port.is_input for port in (dst_node.inputs or [])
                ):
                    ok = False
                    break
                # 目标端口只能有这一条入边，否则改接会改变其它来源。
                dst_in_edges = [e for e in incoming.get(edge.dst_node, []) if e.dst_port == edge.dst_port]
                if len(dst_in_edges) != 1:
                    ok = False
                    break
            if not ok:
                continue

            for edge in value_edges:
                edge.src_node = keeper_id
                edge.src_port = LOCAL_VAR_VALUE_PORT
            model.touch_edges_revision()
            remove_edges(model, [e.id for e in model.edges.values() if e.dst_node == node_id])
            model.nodes.pop(node_id, None)
            removed_ids.append(node_id)
            merged += 1

    return {
        "pass": "local_var_reuse",
        "merged_nodes": merged,
        "removed_node_ids": removed_ids,
    }
