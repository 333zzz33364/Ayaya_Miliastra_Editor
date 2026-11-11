# engine/graph/optimize 目录说明

## 目录用途

- 编译期优化 pass 的容器：在 IR 建模之后、自动布局之前对 `GraphModel` 做**可证明等价**的结构变换。
- 由 `engine/graph/code_to_graph_orchestrator.py` 的 `_apply_optimize_passes()` 调用，结果写入
  `graph_model.metadata["optimize_report"]`。

## 当前状态

- 全部 pass **默认关闭**，开关在 `engine/configs/settings.py`：
  - `GRAPH_OPTIMIZE_CONSTANT_FOLD`：常量折叠
  - `GRAPH_OPTIMIZE_DEAD_CODE`：死节点/死边删除
  - `GRAPH_OPTIMIZE_LOCAL_VAR_REUSE`：局部变量复用
  - `GRAPH_OPTIMIZE_TIMER_POOLING`：定时器池化
- 执行顺序（`runner.py`）：常量折叠 → 局部变量复用 → 定时器池化 → 死代码删除；最后清理
  `basic_blocks` / `event_flow_order` 中已删除节点的残留引用。
- 各 pass 的等价性前提见对应模块文件头：任何一条不满足就跳过该节点，不做部分变换。
- 缓存一致性：优化开关会写进 `GraphCacheFacade.current_layout_settings_snapshot()`
  （仅在任一开关开启时），避免切换开关后误命中旧 `graph_cache`。

## 注意事项

- 变换后必须满足 `validate_graph_model` 的 fail-closed 规则：非事件节点的流程入口仍有入边、
  数据输入仍有来源（连线/常量/虚拟引脚）、不留悬空边。
- 常量折叠只处理白名单内的纯运算节点（四则/比较/逻辑），且要求目标端口有效类型可判定；
  泛型/枚举/结构体一律不折叠。
- 复合节点子图（含虚拟引脚）不参与死代码删除；没有事件节点的图直接跳过。
- 该目录只做图变换，不做 IO、不写盘、不读节点库以外的东西。
