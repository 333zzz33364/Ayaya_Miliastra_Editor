from __future__ import annotations

"""Pass 2：死节点 / 死边删除。

判定顺序（保守）：
1. 纯数据节点若没有任何出边，删除（它的输出没人用）。
2. 从事件节点沿流程边求可达集合；数据节点只要被可达节点的数据输入依赖就保留。
3. 删除既不在流程可达集合、也不是可达节点数据祖先的节点。
4. 循环到不再变化为止（删掉一个节点可能让上游数据节点也变成死节点）。

保留项：
- 事件节点永远保留（是执行入口）；
- 带 `is_virtual_pin` 的复合节点虚拟引脚节点永远不会被删（它们是子图接口）；
- 图里没有事件节点时直接跳过（无法判定可达性）。
"""

from typing import Any, Dict, List, Set

from engine.graph.models import GraphModel

from .common import (
    build_edge_indexes,
    data_ancestors_of,
    drop_stale_block_members,
    event_node_ids,
    flow_reachable_from,
    is_pure_data_node,
    remove_edges,
)


def run_dead_code_elimination(model: GraphModel) -> Dict[str, Any]:
    roots = event_node_ids(model)
    removed_ids: List[str] = []
    removed_edges = 0

    if not roots:
        # 没有事件节点：保守跳过，避免把整张图当死代码删光。
        return {"pass": "dead_code", "removed_nodes": 0, "removed_edges": 0, "skipped": "no_event_node"}

    if any(bool(getattr(node, "is_virtual_pin", False)) for node in model.nodes.values()):
        # 复合节点子图：虚拟引脚是接口，且子图可达性语义与主图不同，保守跳过。
        return {"pass": "dead_code", "removed_nodes": 0, "removed_edges": 0, "skipped": "virtual_pin_graph"}

    changed = True
    while changed:
        changed = False
        outgoing, incoming = build_edge_indexes(model)

        # 1) 没有出边的纯数据节点
        for node_id, node in list(model.nodes.items()):
            if node_id in roots:
                continue
            if not is_pure_data_node(node):
                continue
            if not outgoing.get(node_id):
                edge_ids = [e.id for e in incoming.get(node_id, [])]
                removed_edges += remove_edges(model, edge_ids)
                model.nodes.pop(node_id, None)
                removed_ids.append(node_id)
                changed = True

        if changed:
            continue

        # 2) 流程可达集合
        live: Set[str] = flow_reachable_from(model, roots, build_edge_indexes(model)[0])
        # 3) 加上可达节点的数据祖先
        _outgoing, incoming2 = build_edge_indexes(model)
        live |= data_ancestors_of(model, live, incoming2)

        # 4) 删除其余节点
        for node_id in list(model.nodes.keys()):
            if node_id in live:
                continue
            node = model.nodes.get(node_id)
            if node is None:
                continue
            if bool(getattr(node, "is_virtual_pin", False)):
                continue
            edge_ids = [e.id for e in model.edges.values() if e.src_node == node_id or e.dst_node == node_id]
            removed_edges += remove_edges(model, edge_ids)
            model.nodes.pop(node_id, None)
            removed_ids.append(node_id)
            changed = True

    drop_stale_block_members(model)
    return {
        "pass": "dead_code",
        "removed_nodes": len(removed_ids),
        "removed_edges": removed_edges,
        "removed_node_ids": removed_ids,
    }
