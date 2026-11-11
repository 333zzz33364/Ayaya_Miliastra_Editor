"""客户端发送定义使用独立索引，不能用同名 entry 的序号替换。"""
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOL / "private_extensions"))
from ugc_file_tools.gil_dump_codec.dump_json_tree import load_gil_payload_as_numeric_message
from ugc_file_tools.node_graph_writeback.pipeline_parts.pipeline_signals import _extract_signal_node_def_id_maps_from_payload_root


SAVE5 = TOOL.parents[1] / "项目/跳一跳/存档5.gil"


class ClientSignalIndexTests(unittest.TestCase):
    def setUp(self):
        if not SAVE5.is_file():
            self.skipTest(f"real save fixture unavailable: {SAVE5}")

    def test_native_save_has_different_entry_and_client_indices(self):
        root = load_gil_payload_as_numeric_message(SAVE5)
        maps = _extract_signal_node_def_id_maps_from_payload_root(payload_root=root)
        for name, entry_index in [("两分钟投票", 8), ("跳一跳_座位同步", 9), ("跳一跳_调试推送", 12)]:
            with self.subTest(name=name):
                self.assertEqual(maps.signal_index_by_signal_name[name], entry_index)
                self.assertEqual(maps.client_send_signal_index_by_signal_name[name], 6)

    def test_duplicate_name_keeps_selected_definition_index(self):
        root = load_gil_payload_as_numeric_message(SAVE5)
        maps = _extract_signal_node_def_id_maps_from_payload_root(payload_root=root)
        self.assertEqual(maps.client_send_node_def_id_by_signal_name["结算"], 1610612802)
        self.assertEqual(maps.client_send_signal_index_by_signal_name["结算"], 14)

    def test_empty_payload_has_empty_client_index_map(self):
        maps = _extract_signal_node_def_id_maps_from_payload_root(payload_root={})
        self.assertEqual(maps.client_send_signal_index_by_signal_name, {})

    def test_registered_duplicate_wins_over_definition_order(self):
        root = load_gil_payload_as_numeric_message(SAVE5)
        entry = next(e for e in root["10"]["5"]["3"] if e["3"] == "结算")
        registered = next(w["1"] for w in root["10"]["2"] if w["1"]["4"]["1"]["5"] == 1610612752)
        entry["9"] = registered["4"]["1"]
        maps = _extract_signal_node_def_id_maps_from_payload_root(payload_root=root)
        self.assertEqual(maps.client_send_node_def_id_by_signal_name["结算"], 1610612752)
        self.assertEqual(maps.client_send_signal_index_by_signal_name["结算"], 6)


if __name__ == "__main__":
    unittest.main()
