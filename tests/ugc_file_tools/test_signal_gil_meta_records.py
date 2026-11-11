"""GIL 专属 META 不得携带 GIA source_ref；通用节点保留显式绑定。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "private_extensions"))
from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import binary_data_text_to_numeric_message
from ugc_file_tools.node_graph_writeback.constants_writeback_parts.stage_signal_meta import _write_or_patch_signal_meta_pin_record


class SignalGilMetaTests(unittest.TestCase):
    def write(self, records, runtime):
        _write_or_patch_signal_meta_pin_record(
            records=records, signal_name="测试信号", node_runtime_id_int=runtime,
            source_ref_node_def_id_int=0x60000001, composite_pin_index_int=15,
        )
        return binary_data_text_to_numeric_message(records[0])

    def test_specific_runtime_omits_source_ref(self):
        for runtime in (0x40000001, 0x60000001):
            with self.subTest(runtime=runtime):
                value = self.write([], runtime)
                self.assertNotIn("100", value["1"])
                self.assertEqual(value["7"], 15)

    def test_generic_runtime_keeps_source_ref(self):
        value = self.write([], 300005)
        self.assertEqual(value["1"]["100"]["1"], 0x60000001)

    def test_rebinding_existing_meta_removes_old_source_ref(self):
        records = []
        self.write(records, 300005)
        value = self.write(records, 0x60000001)
        self.assertEqual(len(records), 1)
        self.assertNotIn("100", value["1"])


if __name__ == "__main__":
    unittest.main()
