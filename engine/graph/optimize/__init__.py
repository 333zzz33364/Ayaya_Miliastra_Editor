from __future__ import annotations

"""
engine.graph.optimize

编译期优化 pass（IR 之后、布局之前）。

设计约束：
- 每个 pass 独立开关，默认全部关闭（由 `engine.configs.settings` 控制）。
- pass 只做“等价变换”，不得让图违反 `validate_graph_model` 的 fail-closed 规则：
  - 非事件节点的流程入口必须仍有入边；
  - 数据输入必须仍有来源（连线 / 常量 / 虚拟引脚）；
  - 删节点不得留下悬空边、不得留下已删除节点 id 的基本块引用。
- 任何一处无法确认等价时，pass 必须放弃该节点，而不是猜。
"""

from .options import OptimizeOptions, options_from_settings
from .runner import apply_graph_optimizations

__all__ = [
    "OptimizeOptions",
    "apply_graph_optimizations",
    "options_from_settings",
]
