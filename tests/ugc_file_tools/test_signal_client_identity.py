from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


EDITOR_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EDITOR_ROOT.parents[1]
sys.path.insert(0, str(EDITOR_ROOT / "private_extensions"))

from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import (  # noqa: E402
    binary_data_text_to_numeric_message,
    numeric_message_to_binary_data_text,
)
from ugc_file_tools.signal_writeback import helpers as h  # noqa: E402
from ugc_file_tools.signal_writeback import writer  # noqa: E402
from ugc_file_tools.signal_writeback.client_identity import normalize_client_identities  # noqa: E402


SAVE5 = PROJECT_ROOT / "项目" / "跳一跳" / "存档5.gil"


def _decode(value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value.startswith("<binary_data>"):
        return binary_data_text_to_numeric_message(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise AssertionError(f"expected a message, got {value!r}")


def _encode_like(original: Any, value: Mapping[str, Any]) -> Any:
    if isinstance(original, str) and original.startswith("<binary_data>"):
        return numeric_message_to_binary_data_text(value)
    return dict(value)


def _load_root(path: Path) -> dict[str, Any]:
    return h._get_payload_root(h._dump_gil_to_raw_json_object(path))


def _write_spec(path: Path, signals: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps({"signals": signals}, ensure_ascii=False), encoding="utf-8")


def _run_writer(input_path: Path, output_path: Path, spec_path: Path) -> dict[str, Any]:
    with patch.object(writer, "save_cached_signal_node_def_templates", lambda **_kwargs: None):
        return writer.add_signals_to_gil(
            input_gil_file_path=input_path,
            output_gil_file_path=output_path,
            template_gil_file_path=None,
            spec_json_path=spec_path,
            bootstrap_template_gil_file_path=None,
            param_build_mode="semantic",
        )


def _node_defs(root: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    return h._index_node_defs_by_id(root)


def _client_name(node_def: Mapping[str, Any]) -> str:
    binding = _decode(node_def.get("107"))
    client_binding = _decode(binding.get("108"))
    return str(client_binding.get("1") or "").strip()


def _client_index(node_def: Mapping[str, Any]) -> int:
    value = h._extract_nested_int(node_def, ["4", "5"])
    if type(value) is not int:
        raise AssertionError(f"client definition has no field 4/5 index: {node_def!r}")
    return int(value)


def _definition_id(raw: Any) -> int | None:
    try:
        value = h._extract_nested_int(_decode(raw), ["5"])
    except (AssertionError, ValueError, TypeError):
        return None
    return int(value) if type(value) is int else None


class SignalClientIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        if not SAVE5.is_file():
            self.skipTest(f"real save fixture unavailable: {SAVE5}")

    def test_real_save5_merge_indices_and_repeatability(self) -> None:
        original_sha = hashlib.sha256(SAVE5.read_bytes()).hexdigest()
        root = _load_root(SAVE5)
        before = _node_defs(root)
        before_ids = set(before)
        before_table_size = len(root["10"]["5"]["2"])
        self.assertEqual(len(root["10"]["5"]["3"]), 11)
        self.assertEqual({1610612752, 1610612802} & before_ids, {1610612752, 1610612802})

        result = normalize_client_identities(root)
        self.assertEqual(result["merged_client_ids"], {"1610612752": 1610612802})
        self.assertEqual(result["removed_client_definition_count"], 1)
        self.assertEqual(result["rewritten_node_definition_refs"], 0)
        self.assertEqual(result["rewritten_pin_source_refs"], 0)
        self.assertEqual(result["rewritten_pin_composite_indices"], 0)

        after = _node_defs(root)
        self.assertEqual(set(after), before_ids - {1610612752})
        self.assertIn(1610612802, after)
        self.assertEqual(len(root["10"]["2"]), 44)
        index_table = root["10"]["5"]["2"]
        self.assertEqual(len(index_table), before_table_size)
        self.assertFalse(any(_definition_id(item) == 1610612752 for item in index_table))

        entry_by_name = {
            str(entry.get("3") or "").strip(): entry
            for entry in root["10"]["5"]["3"]
            if isinstance(entry, Mapping)
        }
        expected_indices: dict[str, int] = {}
        canonical_ids: dict[str, int] = {}
        for node_id, node_def in after.items():
            if node_def.get("200") != "发送客户端脚本信号":
                continue
            name = _client_name(node_def)
            entry = entry_by_name[name]
            signal_index = h._extract_nested_int(entry, ["6"])
            self.assertIs(type(signal_index), int)
            expected = int(signal_index) + 1
            self.assertEqual(_client_index(node_def), expected, name)
            expected_indices[name] = expected
            canonical_ids[name] = int(node_id)
        self.assertEqual(len(expected_indices), 11)
        self.assertEqual(len(set(expected_indices.values())), 11)
        self.assertEqual(expected_indices["结算"], 14)

        for entry_name, entry in entry_by_name.items():
            entry_client_id = _definition_id(entry.get("9"))
            self.assertEqual(entry_client_id, canonical_ids[entry_name])

        for graph_wrapper in root["10"]["1"]:
            graph = graph_wrapper.get("1") if isinstance(graph_wrapper, Mapping) else None
            if not isinstance(graph, Mapping):
                continue
            for node in graph.get("3", []):
                if not isinstance(node, Mapping):
                    continue
                node_client_ids = {
                    node_id
                    for field_number in ("2", "3")
                    if (node_id := _definition_id(node.get(field_number))) in canonical_ids.values()
                }
                if node_client_ids:
                    self.assertEqual(len(node_client_ids), 1)
                    client_id = next(iter(node_client_ids))
                    signal_name = _client_name(after[client_id])
                    self.assertEqual(node.get("9"), expected_indices[signal_name])

        self.assertEqual(hashlib.sha256(SAVE5.read_bytes()).hexdigest(), original_sha)
        snapshot = copy.deepcopy(root)
        second = normalize_client_identities(root)
        self.assertEqual(second["merged_client_ids"], {})
        self.assertEqual(second["client_indices"], [])
        self.assertEqual(second["changed_entry_count"], 0)
        self.assertEqual(second["changed_node_count"], 0)
        self.assertEqual(root, snapshot)
        self.assertEqual(hashlib.sha256(SAVE5.read_bytes()).hexdigest(), original_sha)

    def test_alias_node_refs_and_composite_pin_indices_migrate(self) -> None:
        root = _load_root(SAVE5)
        alias_meta = {"1": 10001, "2": 20000, "3": 22001, "5": 1610612752}
        old_cpis = [338, 339, 340, 341]

        def _synthetic_node(node_ordinal: int) -> dict[str, Any]:
            return {
                "1": node_ordinal,
                "2": numeric_message_to_binary_data_text(alias_meta),
                "3": numeric_message_to_binary_data_text(alias_meta),
                "4": [
                    {"1": {"100": 1}, "2": {"100": 1}, "4": 1, "7": old_cpis[0], "99": "keep-flow-in"},
                    {"1": {"100": 2}, "2": {"100": 2}, "4": 1, "7": old_cpis[1], "99": "keep-flow-out"},
                    numeric_message_to_binary_data_text(
                        {"1": {"100": 6}, "2": {"100": 1}, "4": 1, "7": old_cpis[2], "99": 3}
                    ),
                    {"1": {"100": 5}, "2": {"100": 1}, "4": 1, "7": old_cpis[3], "99": "keep-target"},
                ],
                "9": 6,
                "777": "untouched-node-field",
            }

        plain_node = _synthetic_node(999999)
        binary_node = _synthetic_node(1000000)
        generic_node = {
            "1": 1000001,
            "2": numeric_message_to_binary_data_text({"1": 10001, "2": 20000, "3": 22001, "5": 300005}),
            "3": {"1": 10001, "2": 20000, "3": 22001, "5": 300005},
            "4": [
                numeric_message_to_binary_data_text(
                    {
                        "1": {"100": {"1": 1610612752, "2": "keep-flow-source"}},
                        "2": {"100": {"1": 1610612752, "2": "keep-target-source"}},
                        "4": 1,
                        "7": 340,
                        "99": "keep-generic-pin-field",
                    }
                )
            ],
            "9": 77,
            "777": "keep-generic-node-field",
        }
        graph_plain = {"1": {"1": 10000, "2": 20000, "3": 21001, "5": 900001}, "2": "synthetic plain graph", "3": [plain_node]}
        graph_binary = {
            "1": {"1": 10000, "2": 20000, "3": 21001, "5": 900002},
            "2": "synthetic binary graph",
            "3": [binary_node, numeric_message_to_binary_data_text(generic_node)],
        }
        multi_graph_group = {"1": [graph_plain, numeric_message_to_binary_data_text(graph_binary)], "88": "keep-group-field"}
        root["10"]["1"].append(multi_graph_group)

        result = normalize_client_identities(root)
        self.assertEqual(result["rewritten_node_definition_refs"], 4)
        self.assertEqual(result["rewritten_pin_source_refs"], 2)
        self.assertEqual(result["rewritten_pin_composite_indices"], 9)
        self.assertGreaterEqual(result["changed_node_count"], 3)
        self.assertEqual(multi_graph_group["88"], "keep-group-field")
        self.assertIsInstance(multi_graph_group["1"], list)
        self.assertIsInstance(multi_graph_group["1"][1], str)

        migrated_binary_graph = _decode(multi_graph_group["1"][1])
        binary_nodes = migrated_binary_graph["3"]
        self.assertIsInstance(binary_nodes, list)
        migrated_binary_node = binary_nodes[0]
        migrated_generic_node = _decode(binary_nodes[1])
        migrated_nodes = [plain_node, migrated_binary_node]
        for synthetic_node in migrated_nodes:
            self.assertEqual(_definition_id(synthetic_node["2"]), 1610612802)
            self.assertEqual(_definition_id(synthetic_node["3"]), 1610612802)
            self.assertEqual(synthetic_node["9"], 14)
            self.assertEqual(synthetic_node["777"], "untouched-node-field")

            new_cpis: list[int] = []
            for pin in synthetic_node["4"]:
                decoded = _decode(pin)
                new_cpis.append(int(h._extract_nested_int(decoded, ["7"])))
            self.assertEqual(new_cpis, [542, 543, 544, 819])
            self.assertEqual(synthetic_node["4"][0]["99"], "keep-flow-in")
            self.assertEqual(synthetic_node["4"][1]["99"], "keep-flow-out")
            self.assertEqual(synthetic_node["4"][3]["99"], "keep-target")

        self.assertEqual(_definition_id(migrated_generic_node["2"]), 300005)
        self.assertEqual(_definition_id(migrated_generic_node["3"]), 300005)
        self.assertEqual(migrated_generic_node["9"], 77)
        self.assertEqual(migrated_generic_node["777"], "keep-generic-node-field")
        generic_pins = migrated_generic_node["4"]
        generic_pin = _decode(generic_pins[0] if isinstance(generic_pins, list) else generic_pins)
        self.assertEqual(h._extract_nested_int(generic_pin, ["1", "100", "1"]), 1610612802)
        self.assertEqual(h._extract_nested_int(generic_pin, ["2", "100", "1"]), 1610612802)
        self.assertEqual(generic_pin["1"]["100"]["2"], "keep-flow-source")
        self.assertEqual(generic_pin["2"]["100"]["2"], "keep-target-source")
        self.assertEqual(generic_pin["7"], 544)
        self.assertEqual(generic_pin["99"], "keep-generic-pin-field")

    def test_valid_entry9_wins_and_registry_repair_fixes_alias_peer(self) -> None:
        root = _load_root(SAVE5)
        before = _node_defs(root)
        settlement_entry = next(entry for entry in root["10"]["5"]["3"] if entry.get("3") == "结算")
        canonical_id = 1610612802
        alias_id = 1610612752
        settlement_entry["9"] = numeric_message_to_binary_data_text(
            {"1": 10001, "2": 20000, "3": 22001, "5": canonical_id}
        )

        send_id = _definition_id(settlement_entry["1"])
        self.assertIsNotNone(send_id)
        send_node = before[int(send_id)]
        outer_raw = send_node["107"]
        outer = _decode(outer_raw)
        binding_raw = outer["101"]
        binding = _decode(binding_raw)
        peer_raw = binding["4"]
        peer = _decode(peer_raw)
        peer["5"] = alias_id
        binding["4"] = _encode_like(peer_raw, peer)
        outer["101"] = _encode_like(binding_raw, binding)
        send_node["107"] = _encode_like(outer_raw, outer)

        result = normalize_client_identities(root)
        self.assertEqual(result["merged_client_ids"], {str(alias_id): canonical_id})
        repaired_settlement = next(
            item for item in result["registry_repairs"] if item.get("signal_name") == "结算"
        )
        self.assertIn("send", repaired_settlement.get("role_bindings", []))
        repaired_outer = _decode(send_node["107"])
        repaired_binding = _decode(repaired_outer["101"])
        self.assertEqual(_definition_id(repaired_binding["4"]), canonical_id)

    def test_writer_assigns_distinct_new_client_indices_from_each_entry(self) -> None:
        signals = [
            {"signal_name": "新建客户端索引甲", "params": []},
            {"signal_name": "新建客户端索引乙", "params": []},
        ]
        with tempfile.TemporaryDirectory(prefix="signal-client-index-") as temp:
            temp_path = Path(temp)
            spec_path = temp_path / "signals.json"
            output_path = temp_path / "new-signals.gil"
            _write_spec(spec_path, signals)
            result = _run_writer(SAVE5, output_path, spec_path)
            client_additions = [item for item in result["added_signals"] if "client_send_node_def_id" in item]
            self.assertEqual(
                {str(item["signal_name"]) for item in client_additions},
                {str(spec["signal_name"]) for spec in signals},
            )

            root = _load_root(output_path)
            entries = {
                str(entry.get("3") or "").strip(): entry
                for entry in root["10"]["5"]["3"]
                if isinstance(entry, Mapping)
            }
            node_defs = _node_defs(root)
            client_by_name: dict[str, dict[str, Any]] = {}
            for node_def in node_defs.values():
                if node_def.get("200") == "发送客户端脚本信号":
                    client_by_name[_client_name(node_def)] = node_def

            indices: list[int] = []
            for spec in signals:
                name = str(spec["signal_name"])
                entry_index = h._extract_nested_int(entries[name], ["6"])
                self.assertIs(type(entry_index), int)
                expected = int(entry_index) + 1
                self.assertEqual(_client_index(client_by_name[name]), expected, name)
                indices.append(expected)
            self.assertEqual(len(set(indices)), 2)


if __name__ == "__main__":
    unittest.main()
