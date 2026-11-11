from __future__ import annotations

"""Pass 4：定时器池化（仅处理可证明冗余的相邻重复启动）。

做法：同一条流程链上，A【启动定时器】的流程出直接接到 B【启动定时器】的流程入，
且 A、B 的全部非流程输入都是相同常量（目标实体 / 定时器名称 / 是否循环 / 定时器序列）时，
B 的启动在 A 的启动之后立即发生，等价于只启动一次：删除 B，把 A 的流程出接到 B 的流程出。

只处理“相邻”的情况，不做跨分支、跨事件流的合并，也不改动任何定时器名称常量。
"""

from typing import Any, Dict, List, Optional

from engine.graph.models import GraphModel

from .common import START_TIMER_TITLE, build_edge_indexes, constant_signature, is_pure_data_node, remove_edges


def _constant_inputs(node) -> Optional[tuple]:
    signature: List[tuple] = []
    for port in node.inputs or []:
        if port.name.startswith("__"):
            continue
        if port.name == "流程入":
            continue
        if port.name not in (node.input_constants or {}):
            return None
        sig = constant_signature((node.input_constants or {})[port.name])
        if sig is None:
            return None
        signature.append((port.name, sig))
    return tuple(signature)


def run_timer_pooling(model: GraphModel) -> Dict[str, Any]:
    outgoing, incoming = build_edge_indexes(model)
    removed_ids: List[str] = []
    pooled = 0

    for node_id in list(model.nodes.keys()):
        node = model.nodes.get(node_id)
        if node is None or str(node.title or "").strip() != START_TIMER_TITLE:
            continue
        if is_pure_data_node(node):
            continue

        # 只处理唯一入边（流程入）且来源也是启动定时器的情形。
        in_edges = incoming.get(node_id, [])
        if len(in_edges) != 1 or in_edges[0].dst_port != "流程入":
            continue
        prev_id = in_edges[0].src_node
        prev = model.nodes.get(prev_id)
        if prev is None or str(prev.title or "").strip() != START_TIMER_TITLE:
            continue
        prev_outs = [e for e in outgoing.get(prev_id, []) if e.src_port == "流程出"]
        if len(prev_outs) != 1 or prev_outs[0].dst_node != node_id:
            continue

        # B 的流程出必须只有一条，才能安全上提。
        node_outs = [e for e in outgoing.get(node_id, []) if e.src_port == "流程出"]
        if len(node_outs) > 1:
            continue

        prev_sig = _constant_inputs(prev)
        node_sig = _constant_inputs(node)
        if prev_sig is None or node_sig is None or prev_sig != node_sig:
            continue

        # 上提：prev.流程出 -> B 的下游
        for edge in node_outs:
            edge.src_node = prev_id
            edge.src_port = "流程出"
        model.touch_edges_revision()
        remove_edges(model, [e.id for e in model.edges.values() if e.dst_node == node_id or e.src_node == node_id])
        model.nodes.pop(node_id, None)
        removed_ids.append(node_id)
        pooled += 1

    return {"pass": "timer_pooling", "pooled_nodes": pooled, "removed_node_ids": removed_ids}
