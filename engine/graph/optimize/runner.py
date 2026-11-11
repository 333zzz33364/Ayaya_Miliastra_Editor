from __future__ import annotations

from typing import Any, Dict, List

from engine.graph.models import GraphModel

from .common import drop_stale_block_members
from .constant_fold import run_constant_folding
from .dead_code import run_dead_code_elimination
from .local_var_reuse import run_local_var_reuse
from .options import OptimizeOptions
from .timer_pooling import run_timer_pooling


def apply_graph_optimizations(
    model: GraphModel,
    options: OptimizeOptions,
    *,
    node_library: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """按开关顺序执行优化 pass，返回逐 pass 的变更报告。

    顺序：常量折叠 → 局部变量复用 → 定时器池化 → 死代码删除。
    死代码放最后，让前面几个 pass 产生的孤立节点一并清掉。
    """
    report: Dict[str, Any] = {
        "enabled": {
            "constant_fold": bool(options.constant_fold),
            "dead_code": bool(options.dead_code),
            "local_var_reuse": bool(options.local_var_reuse),
            "timer_pooling": bool(options.timer_pooling),
        },
        "nodes_before": len(model.nodes),
        "edges_before": len(model.edges),
        "passes": [],
    }

    if not options.any_enabled:
        report["nodes_after"] = len(model.nodes)
        report["edges_after"] = len(model.edges)
        return report

    passes: List[Dict[str, Any]] = []
    if options.constant_fold:
        passes.append(run_constant_folding(model, node_library=node_library))
    if options.local_var_reuse:
        passes.append(run_local_var_reuse(model))
    if options.timer_pooling:
        passes.append(run_timer_pooling(model))
    if options.dead_code:
        passes.append(run_dead_code_elimination(model))

    drop_stale_block_members(model)

    report["passes"] = passes
    report["nodes_after"] = len(model.nodes)
    report["edges_after"] = len(model.edges)
    return report
