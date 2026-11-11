from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OptimizeOptions:
    """编译期优化 pass 的开关集合。

    默认全部关闭：只有显式开启后才会改变图结构。
    """

    constant_fold: bool = False
    dead_code: bool = False
    local_var_reuse: bool = False
    timer_pooling: bool = False

    @property
    def any_enabled(self) -> bool:
        return bool(self.constant_fold or self.dead_code or self.local_var_reuse or self.timer_pooling)


def options_from_settings() -> OptimizeOptions:
    """从全局设置读取优化开关（缺失时视为关闭）。"""
    from engine.configs.settings import settings

    return OptimizeOptions(
        constant_fold=bool(getattr(settings, "GRAPH_OPTIMIZE_CONSTANT_FOLD", False)),
        dead_code=bool(getattr(settings, "GRAPH_OPTIMIZE_DEAD_CODE", False)),
        local_var_reuse=bool(getattr(settings, "GRAPH_OPTIMIZE_LOCAL_VAR_REUSE", False)),
        timer_pooling=bool(getattr(settings, "GRAPH_OPTIMIZE_TIMER_POOLING", False)),
    )
