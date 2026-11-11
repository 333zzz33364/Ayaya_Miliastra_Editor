"""写回端 preflight：校验 section10/7、graph_id、client 信号索引、300005 与挂载槽。

只使用合成 payload，不依赖本机私有存档。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

EDITOR_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(EDITOR_ROOT / "private_extensions"))

from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import numeric_message_to_binary_data_text
from ugc_file_tools.writeback_preflight import (
    WritebackPreflightError,
    collect_writeback_preflight_issues,
    run_writeback_preflight_or_raise,
)


def _graph_entry(graph_id: int, node_def_ids: list[int] | None = None) -> dict:
    nodes = []
    for ordinal, node_def_id in enumerate(node_def_ids or [], start=1):
        nodes.append(
            {
                "1": ordinal,
                "2": numeric_message_to_binary_data_text({"5": int(node_def_id)}),
            }
        )
    return {"1": [{"5": int(graph_id)}], "2": [f"图{graph_id}"], "3": nodes}


def _payload(
    *,
    graph_ids: list[int],
    field7: int = 1,
    node_def_ids_by_graph: dict[int, list[int]] | None = None,
) -> dict:
    node_def_ids_by_graph = node_def_ids_by_graph or {}
    return {
        "10": {
            "1": [{"1": [_graph_entry(gid, node_def_ids_by_graph.get(gid)) for gid in graph_ids]}],
            "2": [],
            "5": {"2": [], "3": []},
            "7": int(field7),
        }
    }


def _client_def(index: int, signal_name: str, node_def_id: int) -> dict:
    return {
        "1": {
            "4": {"5": int(index), "1": {"1": 10001, "2": 20000, "3": 22001, "5": int(node_def_id)}},
            "107": {"1": 1006, "108": {"1": str(signal_name)}},
            "200": "发送客户端脚本信号",
        }
    }


class WritebackPreflightTests(unittest.TestCase):
    def test_clean_payload_raises_nothing(self) -> None:
        report = collect_writeback_preflight_issues(
            payload_root=_payload(graph_ids=[1073741825, 1073741826]),
            base_section10_field7=1,
        )
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["stats"]["graph_ids"], 2)

    def test_section10_field7_must_match_base(self) -> None:
        report = collect_writeback_preflight_issues(
            payload_root=_payload(graph_ids=[1073741825], field7=3),
            base_section10_field7=1,
        )
        codes = [item["code"] for item in report["issues"]]
        self.assertEqual(codes, ["section10_field7_changed"])
        with self.assertRaises(WritebackPreflightError):
            run_writeback_preflight_or_raise(payload_root=_payload(graph_ids=[1], field7=3), base_section10_field7=1)

    def test_duplicate_graph_id_is_blocking(self) -> None:
        report = collect_writeback_preflight_issues(
            payload_root=_payload(graph_ids=[1073741825, 1073741825]),
            base_section10_field7=1,
        )
        self.assertEqual([item["code"] for item in report["issues"]], ["duplicate_graph_id"])

    def test_duplicate_client_signal_index_is_blocking(self) -> None:
        payload = _payload(graph_ids=[1])
        payload["10"]["2"] = [
            _client_def(6, "甲", 1610612749),
            _client_def(6, "乙", 1610612750),
        ]
        report = collect_writeback_preflight_issues(payload_root=payload, base_section10_field7=1)
        self.assertEqual([item["code"] for item in report["issues"]], ["duplicate_client_signal_index"])

    def test_distinct_client_signal_index_passes(self) -> None:
        payload = _payload(graph_ids=[1])
        payload["10"]["2"] = [
            _client_def(6, "甲", 1610612749),
            _client_def(14, "乙", 1610612750),
        ]
        report = collect_writeback_preflight_issues(payload_root=payload, base_section10_field7=1)
        self.assertEqual(report["issues"], [])

    def test_generic_300005_node_is_blocking(self) -> None:
        payload = _payload(graph_ids=[1073741825], node_def_ids_by_graph={1073741825: [300005]})
        report = collect_writeback_preflight_issues(payload_root=payload, base_section10_field7=1)
        self.assertEqual([item["code"] for item in report["issues"]], ["generic_client_send_node"])

    def test_specific_client_send_node_passes(self) -> None:
        payload = _payload(graph_ids=[1073741825], node_def_ids_by_graph={1073741825: [1610612749]})
        report = collect_writeback_preflight_issues(payload_root=payload, base_section10_field7=1)
        self.assertEqual(report["issues"], [])

    def test_duplicate_mount_graph_id_in_same_slot_is_blocking(self) -> None:
        payload = _payload(graph_ids=[1073741825])
        payload["5"] = {
            "1": [
                {
                    "1": 1094713345,
                    "6": [
                        {
                            "1": 3,
                            "13": {
                                "1": [
                                    {"1": {"1": 1, "2": 1073741825, "501": 20000}},
                                    {"1": {"1": 1, "2": 1073741825, "501": 20000}},
                                ]
                            },
                        }
                    ],
                }
            ]
        }
        report = collect_writeback_preflight_issues(payload_root=payload, base_section10_field7=1)
        self.assertEqual([item["code"] for item in report["issues"]], ["duplicate_mount_graph_id"])

    def test_multiple_distinct_mounts_in_one_slot_are_allowed(self) -> None:
        payload = _payload(graph_ids=[1073741825, 1073741826])
        payload["5"] = {
            "1": [
                {
                    "1": 1077936525,
                    "6": [
                        {
                            "1": 3,
                            "13": {
                                "1": [
                                    {"1": {"1": 1, "2": 1073741825, "501": 20000}},
                                    {"1": {"1": 2, "2": 1073741826, "501": 20000}},
                                ]
                            },
                        }
                    ],
                }
            ]
        }
        report = collect_writeback_preflight_issues(payload_root=payload, base_section10_field7=1)
        self.assertEqual(report["issues"], [])
        self.assertEqual([item["code"] for item in report["warnings"]], ["mount_locator_flag_not_one"])

    def test_dangling_mount_reference_is_only_a_warning(self) -> None:
        payload = _payload(graph_ids=[1073741825])
        payload["5"] = {
            "1": [
                {
                    "1": 1086324737,
                    "6": [{"1": 3, "13": {"1": [{"1": {"1": 1, "2": 999, "501": 20000}}]}}],
                }
            ]
        }
        report = collect_writeback_preflight_issues(payload_root=payload, base_section10_field7=1)
        self.assertEqual(report["issues"], [])
        self.assertEqual([item["code"] for item in report["warnings"]], ["dangling_mount_graph_id"])

    def test_missing_section10_is_tolerated(self) -> None:
        report = collect_writeback_preflight_issues(payload_root={}, base_section10_field7=None)
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["warnings"], [])


EMPTY_BASE = (
    EDITOR_ROOT
    / "private_extensions/ugc_file_tools/builtin_resources/empty_base_samples/empty_base_with_infra.gil"
)


class WritebackPreflightWiringTests(unittest.TestCase):
    """校验写盘入口真的在落盘前拦截（而不是只提供函数）。"""

    def setUp(self) -> None:
        if not EMPTY_BASE.is_file():
            self.skipTest(f"builtin empty base unavailable: {EMPTY_BASE}")

    def test_graph_write_entry_refuses_bad_payload(self) -> None:
        import tempfile

        from ugc_file_tools.node_graph_writeback.pipeline_parts.pipeline_wire_write import (
            write_patched_gil_by_sections_and_return_output_path,
        )

        with tempfile.TemporaryDirectory(prefix="preflight-wire-") as temp:
            output_path = Path(temp) / "bad.gil"
            with self.assertRaises(WritebackPreflightError):
                write_patched_gil_by_sections_and_return_output_path(
                    effective_base_gil_path=EMPTY_BASE,
                    output_gil_path=output_path,
                    payload_root=_payload(graph_ids=[1073741825, 1073741825]),
                    include_section5=False,
                )
            self.assertFalse(output_path.exists(), "校验失败时不应写出任何文件")


if __name__ == "__main__":
    unittest.main()
