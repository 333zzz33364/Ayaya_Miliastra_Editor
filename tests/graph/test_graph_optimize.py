from __future__ import annotations

"""编译期优化 pass 的回归：只验证“等价变换”，不联调节点库。"""

import sys
import unittest
from pathlib import Path

EDITOR_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDITOR_ROOT))

from engine.graph.models.graph_model import EdgeModel, GraphModel, NodeModel, NodeDefRef, PortModel
from engine.graph.optimize import OptimizeOptions, apply_graph_optimizations


def _node(
    model: GraphModel,
    node_id: str,
    title: str,
    inputs: list[str],
    outputs: list[str],
    *,
    constants: dict | None = None,
    input_types: dict | None = None,
) -> NodeModel:
    node = NodeModel(id=node_id, title=title, category="", node_def_ref=NodeDefRef(kind="builtin", key=title))
    node.inputs = [PortModel(name=n, is_input=True) for n in inputs]
    node.outputs = [PortModel(name=n, is_input=False) for n in outputs]
    node.input_constants = dict(constants or {})
    node.effective_input_types = dict(input_types or {})
    node._rebuild_port_maps()
    model.nodes[node_id] = node
    return node


def _edge(model: GraphModel, edge_id: str, src: str, src_port: str, dst: str, dst_port: str) -> None:
    model.edges[edge_id] = EdgeModel(id=edge_id, src_node=src, src_port=src_port, dst_node=dst, dst_port=dst_port)


def _model_with_flow() -> GraphModel:
    """事件节点 → 启动定时器 → 终止定时器 的最小流程骨架。"""
    model = GraphModel(graph_id="g1", graph_name="t")
    event = _node(model, "ev", "节点图开始", [], ["流程出"])
    start = _node(model, "t1", "启动定时器", ["流程入", "目标实体", "定时器名称", "是否循环", "定时器序列"], ["流程出"])
    stop = _node(model, "t2", "终止定时器", ["流程入", "目标实体", "定时器名称"], ["流程出"])
    _edge(model, "e1", "ev", "流程出", "t1", "流程入")
    _edge(model, "e2", "t1", "流程出", "t2", "流程入")
    model.event_flow_order = ["ev"]
    model.event_flow_titles = ["节点图开始"]
    return model


class OptimizeOptionsTests(unittest.TestCase):
    def test_all_disabled_by_default_is_noop(self) -> None:
        model = _model_with_flow()
        before = (set(model.nodes), set(model.edges))
        report = apply_graph_optimizations(model, OptimizeOptions())
        self.assertEqual((set(model.nodes), set(model.edges)), before)
        self.assertEqual(report["passes"], [])


class ConstantFoldTests(unittest.TestCase):
    def test_folds_arithmetic_with_constant_inputs(self) -> None:
        model = _model_with_flow()
        add = _node(model, "add", "加法运算", ["左值", "右值"], ["结果"], constants={"左值": 1, "右值": 2})
        stop = model.nodes["t2"]
        stop.inputs.append(PortModel(name="数量", is_input=True))
        stop.effective_input_types["数量"] = "整数"
        stop._rebuild_port_maps()
        _edge(model, "e3", "add", "结果", "t2", "数量")

        report = apply_graph_optimizations(model, OptimizeOptions(constant_fold=True))
        self.assertNotIn("add", model.nodes)
        self.assertEqual(model.nodes["t2"].input_constants.get("数量"), 3)
        self.assertFalse(any(e.src_node == "add" for e in model.edges.values()))
        self.assertEqual(report["passes"][0]["pass"], "constant_fold")
        self.assertEqual(report["passes"][0]["folded_nodes"], 1)

    def test_skips_when_destination_type_is_generic(self) -> None:
        model = _model_with_flow()
        _node(model, "add", "加法运算", ["左值", "右值"], ["结果"], constants={"左值": 1, "右值": 2})
        stop = model.nodes["t2"]
        stop.inputs.append(PortModel(name="数量", is_input=True))
        stop.effective_input_types["数量"] = "泛型"
        stop._rebuild_port_maps()
        _edge(model, "e3", "add", "结果", "t2", "数量")

        apply_graph_optimizations(model, OptimizeOptions(constant_fold=True))
        self.assertIn("add", model.nodes)

    def test_skips_when_input_has_edge(self) -> None:
        model = _model_with_flow()
        add = _node(model, "add", "加法运算", ["左值", "右值"], ["结果"], constants={"左值": 1})
        src = _node(model, "src", "取较大值", ["输入1", "输入2"], ["较大值"], constants={"输入1": 1, "输入2": 2})
        stop = model.nodes["t2"]
        stop.inputs.append(PortModel(name="数量", is_input=True))
        stop.effective_input_types["数量"] = "整数"
        add.effective_input_types["右值"] = "整数"
        stop._rebuild_port_maps()
        _edge(model, "e3", "src", "较大值", "add", "右值")
        _edge(model, "e4", "add", "结果", "t2", "数量")

        apply_graph_optimizations(model, OptimizeOptions(constant_fold=True))
        # src 是常量节点可被折叠；add 的右值被折叠成常量后也应可折叠。
        self.assertNotIn("src", model.nodes)
        self.assertNotIn("add", model.nodes)
        self.assertEqual(model.nodes["t2"].input_constants.get("数量"), 3)

    def test_does_not_fold_flow_node(self) -> None:
        model = _model_with_flow()
        apply_graph_optimizations(model, OptimizeOptions(constant_fold=True))
        self.assertIn("t1", model.nodes)
        self.assertIn("t2", model.nodes)


class DeadCodeTests(unittest.TestCase):
    def test_removes_unused_pure_data_node(self) -> None:
        model = _model_with_flow()
        _node(model, "orphan", "加法运算", ["左值", "右值"], ["结果"], constants={"左值": 1, "右值": 2})
        apply_graph_optimizations(model, OptimizeOptions(dead_code=True))
        self.assertNotIn("orphan", model.nodes)

    def test_keeps_data_node_feeding_live_flow_node(self) -> None:
        model = _model_with_flow()
        stop = model.nodes["t2"]
        stop.inputs.append(PortModel(name="数量", is_input=True))
        stop._rebuild_port_maps()
        _node(model, "add", "加法运算", ["左值", "右值"], ["结果"], constants={"左值": 1, "右值": 2})
        _edge(model, "e3", "add", "结果", "t2", "数量")
        apply_graph_optimizations(model, OptimizeOptions(dead_code=True))
        self.assertIn("add", model.nodes)

    def test_skips_graph_without_event_node(self) -> None:
        model = GraphModel(graph_id="g2", graph_name="empty")
        _node(model, "orphan", "加法运算", ["左值", "右值"], ["结果"])
        report = apply_graph_optimizations(model, OptimizeOptions(dead_code=True))
        self.assertIn("orphan", model.nodes)
        self.assertEqual(report["passes"][0].get("skipped"), "no_event_node")


class LocalVarReuseTests(unittest.TestCase):
    def test_merges_identical_unwritten_getters(self) -> None:
        model = _model_with_flow()
        stop = model.nodes["t2"]
        stop.inputs.extend([PortModel(name="值1", is_input=True), PortModel(name="值2", is_input=True)])
        stop._rebuild_port_maps()
        _node(model, "g1", "获取局部变量", ["初始值"], ["局部变量", "值"], constants={"初始值": 0})
        _node(model, "g2", "获取局部变量", ["初始值"], ["局部变量", "值"], constants={"初始值": 0})
        _edge(model, "e3", "g1", "值", "t2", "值1")
        _edge(model, "e4", "g2", "值", "t2", "值2")

        apply_graph_optimizations(model, OptimizeOptions(local_var_reuse=True))
        getters = [nid for nid, n in model.nodes.items() if n.title == "获取局部变量"]
        self.assertEqual(getters, ["g1"])
        self.assertTrue(all(e.src_node == "g1" for e in model.edges.values() if e.src_port == "值"))

    def test_does_not_merge_written_variable(self) -> None:
        model = _model_with_flow()
        stop = model.nodes["t2"]
        stop.inputs.extend([PortModel(name="值1", is_input=True), PortModel(name="值2", is_input=True)])
        stop._rebuild_port_maps()
        setter = _node(model, "s1", "设置局部变量", ["流程入", "局部变量", "值"], ["流程出"])
        _node(model, "g1", "获取局部变量", ["初始值"], ["局部变量", "值"], constants={"初始值": 0})
        _node(model, "g2", "获取局部变量", ["初始值"], ["局部变量", "值"], constants={"初始值": 0})
        _edge(model, "e3", "g1", "局部变量", "s1", "局部变量")
        _edge(model, "e4", "g1", "值", "t2", "值1")
        _edge(model, "e5", "g2", "值", "t2", "值2")
        apply_graph_optimizations(model, OptimizeOptions(local_var_reuse=True))
        getters = [nid for nid, n in model.nodes.items() if n.title == "获取局部变量"]
        self.assertEqual(sorted(getters), ["g1", "g2"])


class TimerPoolingTests(unittest.TestCase):
    def test_pools_adjacent_identical_starts(self) -> None:
        model = _model_with_flow()
        stop = model.nodes["t2"]
        _edge(model, "e2b", "t1", "流程出", "t1b", "流程入")
        t1b = _node(model, "t1b", "启动定时器", ["流程入", "目标实体", "定时器名称", "是否循环", "定时器序列"], ["流程出"])
        for node in (model.nodes["t1"], t1b):
            node.input_constants.update({"目标实体": 0, "定时器名称": "计时", "是否循环": False, "定时器序列": [1.0]})
        # t1 -> t1b -> t2
        model.edges.pop("e2", None)
        _edge(model, "e2", "t1b", "流程出", "t2", "流程入")

        apply_graph_optimizations(model, OptimizeOptions(timer_pooling=True))
        self.assertNotIn("t1b", model.nodes)
        self.assertIn("t1", model.nodes)
        self.assertTrue(any(e.src_node == "t1" and e.dst_node == "t2" for e in model.edges.values()))

    def test_does_not_pool_when_constants_differ(self) -> None:
        model = _model_with_flow()
        t1b = _node(model, "t1b", "启动定时器", ["流程入", "目标实体", "定时器名称", "是否循环", "定时器序列"], ["流程出"])
        model.nodes["t1"].input_constants.update({"目标实体": 0, "定时器名称": "甲", "是否循环": False, "定时器序列": [1.0]})
        t1b.input_constants.update({"目标实体": 0, "定时器名称": "乙", "是否循环": False, "定时器序列": [1.0]})
        model.edges.pop("e2", None)
        _edge(model, "e2", "t1", "流程出", "t1b", "流程入")
        _edge(model, "e2b", "t1b", "流程出", "t2", "流程入")
        apply_graph_optimizations(model, OptimizeOptions(timer_pooling=True))
        self.assertIn("t1b", model.nodes)


if __name__ == "__main__":
    unittest.main()
