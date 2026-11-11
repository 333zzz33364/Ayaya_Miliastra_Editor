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
    binary_data_text_to_decoded_field_map,
    binary_data_text_to_numeric_message,
    decoded_field_map_to_binary_data_text,
)
from ugc_file_tools.signal_writeback import helpers as h  # noqa: E402
from ugc_file_tools.signal_writeback import writer  # noqa: E402
from ugc_file_tools.signal_writeback.registry_repair import repair_signal_registry  # noqa: E402


SAVE5 = PROJECT_ROOT / "项目" / "跳一跳" / "存档5.gil"
SIGNAL_NAME = "跳一跳_跳跃上报"
SIGNAL_PARAMS = [
    {"param_name": "座位号", "type_id": 3},
    {"param_name": "平台序号", "type_id": 3},
    {"param_name": "落点X", "type_id": 5},
    {"param_name": "成功", "type_id": 3},
]


def _write_spec(path: Path) -> None:
    path.write_text(
        json.dumps(
            {"signals": [{"signal_name": SIGNAL_NAME, "params": SIGNAL_PARAMS}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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


def _load_root(path: Path) -> dict[str, Any]:
    return h._get_payload_root(h._dump_gil_to_raw_json_object(path))


def _decode_message(value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value.startswith("<binary_data>"):
        return binary_data_text_to_numeric_message(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise AssertionError(f"expected message, got {value!r}")


def _entries(root: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    section5 = root["10"]["5"]
    return {
        str(entry.get("3") or "").strip(): entry
        for entry in section5["3"]
        if isinstance(entry, Mapping) and str(entry.get("3") or "").strip()
    }


def _node_defs(root: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    return h._index_node_defs_by_id(root)


def _role_ids(entry: Mapping[str, Any]) -> dict[str, int]:
    server_meta = _decode_message(entry["7"])
    values = {
        "send": h._extract_nested_int(entry, ["1", "5"]),
        "listen": h._extract_nested_int(entry, ["2", "5"]),
        "server": h._extract_nested_int(server_meta, ["5"]),
    }
    if not all(type(value) is int for value in values.values()):
        raise AssertionError(f"incomplete role IDs: {values!r}")
    return {key: int(value) for key, value in values.items()}


def _client_name(node_def: Mapping[str, Any]) -> str:
    outer = node_def.get("107")
    if isinstance(outer, str):
        outer = _decode_message(outer)
    binding = outer.get("108") if isinstance(outer, Mapping) else None
    if isinstance(binding, str):
        binding = _decode_message(binding)
    return str(binding.get("1") or "").strip() if isinstance(binding, Mapping) else ""


def _named_clients(node_defs: Mapping[int, Mapping[str, Any]], name: str) -> dict[int, Mapping[str, Any]]:
    return {
        int(node_id): node_def
        for node_id, node_def in node_defs.items()
        if str(node_def.get("200") or "").strip() == "发送客户端脚本信号" and _client_name(node_def) == name
    }


def _param_items(node_def: Mapping[str, Any]) -> list[Any]:
    value = node_def.get("102")
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _client_param(item: Any) -> tuple[str, int, int]:
    if isinstance(item, str):
        decoded = binary_data_text_to_decoded_field_map(item)
        name = str(decoded.get("field_1", {}).get("utf8") or "").strip()
        type_message = decoded.get("field_4", {}).get("message", {})
        type_id = h._extract_nested_int(type_message, ["field_3", "int"])
        port = h._extract_nested_int(decoded, ["field_8", "int"])
    elif isinstance(item, Mapping):
        name = str(item.get("1") or "").strip()
        type_descriptor = item.get("4")
        type_id = h._extract_nested_int(type_descriptor, ["3"]) if isinstance(type_descriptor, Mapping) else None
        port = h._extract_nested_int(item, ["8"])
    else:
        raise AssertionError(f"invalid client parameter: {item!r}")
    if not name or type(type_id) is not int or type(port) is not int:
        raise AssertionError(f"cannot read client parameter: {item!r}")
    return name, int(type_id), int(port)


def _entry_params(entry: Mapping[str, Any]) -> list[Any]:
    value = entry.get("4")
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _entry_param(item: Any) -> tuple[str, int, int | None]:
    if isinstance(item, str):
        decoded = binary_data_text_to_decoded_field_map(item)
        name = str(decoded.get("field_1", {}).get("utf8") or "").strip()
        type_id = h._extract_nested_int(decoded, ["field_2", "int"])
        port = h._extract_nested_int(decoded, ["field_7", "int"])
    elif isinstance(item, Mapping):
        name = str(item.get("1") or "").strip()
        type_id = h._extract_nested_int(item, ["2"])
        port = h._extract_nested_int(item, ["7"])
    else:
        raise AssertionError(f"invalid entry parameter: {item!r}")
    if not name or type(type_id) is not int:
        raise AssertionError(f"cannot read entry parameter: {item!r}")
    return name, int(type_id), port


def _binding(node_def: Mapping[str, Any], field_number: int) -> dict[str, Any]:
    outer = node_def.get("107")
    if isinstance(outer, str):
        outer = _decode_message(outer)
    value = outer.get(str(field_number)) if isinstance(outer, Mapping) else None
    return _decode_message(value)


def _meta_id(value: Any) -> int:
    meta = _decode_message(value)
    node_id = h._extract_nested_int(meta, ["5"])
    if type(node_id) is not int:
        raise AssertionError(f"metadata has no ID: {value!r}")
    return int(node_id)


def _client_index(node_def: Mapping[str, Any]) -> int:
    value = h._extract_nested_int(node_def, ["4", "5"])
    if type(value) is not int:
        raise AssertionError("client definition has no independent index")
    return int(value)


class SignalRegistryRepairTests(unittest.TestCase):
    def setUp(self) -> None:
        if not SAVE5.is_file():
            self.skipTest(f"real save fixture unavailable: {SAVE5}")

    def test_real_save_five_registry_repair_and_writer_idempotence(self) -> None:
        original_sha = hashlib.sha256(SAVE5.read_bytes()).hexdigest()
        before_root = _load_root(SAVE5)
        before_entries = _entries(before_root)
        before_defs = _node_defs(before_root)
        before_identity = {
            node_id: _client_index(node_def)
            for node_id, node_def in before_defs.items()
            if str(node_def.get("200") or "").strip() == "发送客户端脚本信号"
        }
        before_client_ports = {
            node_id: [_client_param(item)[2] for item in _param_items(node_def)]
            for node_id, node_def in before_defs.items()
            if str(node_def.get("200") or "").strip() == "发送客户端脚本信号"
        }
        before_node_ports = {
            node_id: {field: copy.deepcopy(node_def.get(field)) for field in ("100", "101", "102", "103")}
            for node_id, node_def in before_defs.items()
        }
        self.assertEqual(len(before_entries), 11)
        settlement_clients = _named_clients(before_defs, "结算")
        self.assertEqual(set(settlement_clients), {1610612752, 1610612802})
        self.assertEqual({key: _client_index(value) for key, value in settlement_clients.items()}, {1610612752: 6, 1610612802: 14})

        with tempfile.TemporaryDirectory(prefix="signal-registry-repair-") as temp:
            temp_path = Path(temp)
            spec_path = temp_path / "signals.json"
            output1 = temp_path / "repaired.gil"
            output2 = temp_path / "idempotent.gil"
            _write_spec(spec_path)

            first_result = _run_writer(SAVE5, output1, spec_path)
            self.assertEqual(first_result["added_signals"], [])
            self.assertEqual(first_result["updated_signals"], [])
            repairs = first_result["registry_repairs"]
            self.assertEqual(len(repairs), 11)
            self.assertEqual(sum(len(item.get("client_ports", [])) for item in repairs), 12)

            after_root = _load_root(output1)
            after_entries = _entries(after_root)
            after_defs = _node_defs(after_root)
            self.assertEqual(set(after_entries), set(before_entries))
            self.assertEqual(set(after_defs), set(before_defs))
            self.assertEqual(
                {node_id: _client_index(node_def) for node_id, node_def in after_defs.items() if node_id in before_identity},
                before_identity,
            )
            self.assertEqual(
                {
                    node_id: [_client_param(item)[2] for item in _param_items(after_defs[node_id])]
                    for node_id in before_client_ports
                },
                before_client_ports,
            )
            self.assertEqual(
                {
                    node_id: {field: after_defs[node_id].get(field) for field in ("100", "101", "102", "103")}
                    for node_id in before_defs
                },
                before_node_ports,
            )

            repair_by_name = {item["signal_name"]: item for item in repairs}
            for signal_name, entry in after_entries.items():
                roles = _role_ids(entry)
                clients = _named_clients(after_defs, signal_name)
                self.assertTrue(clients, signal_name)
                canonical_id = repair_by_name[signal_name]["canonical_client_node_def_id"]
                self.assertIn(canonical_id, clients)
                self.assertEqual(_meta_id(entry["9"]), canonical_id)

                expected = {
                    "send": roles["send"],
                    "listen": roles["listen"],
                    "server": roles["server"],
                    "client": canonical_id,
                }
                send_binding = _binding(after_defs[roles["send"]], 101)
                listen_binding = _binding(after_defs[roles["listen"]], 102)
                server_binding = _binding(after_defs[roles["server"]], 101)
                self.assertEqual(
                    (_meta_id(send_binding["2"]), _meta_id(send_binding["3"]), _meta_id(send_binding["4"])),
                    (expected["listen"], expected["server"], expected["client"]),
                )
                self.assertEqual(
                    (_meta_id(listen_binding["2"]), _meta_id(listen_binding["3"]), _meta_id(listen_binding["4"])),
                    (expected["send"], expected["server"], expected["client"]),
                )
                self.assertEqual(
                    (_meta_id(server_binding["2"]), _meta_id(server_binding["3"]), _meta_id(server_binding["4"])),
                    (expected["listen"], expected["send"], expected["client"]),
                )

                for client_id, client_def in clients.items():
                    client_binding = _binding(client_def, 108)
                    self.assertEqual(
                        (_meta_id(client_binding["2"]), _meta_id(client_binding["3"]), _meta_id(client_binding["4"])),
                        (expected["send"], expected["server"], expected["listen"]),
                    )
                    client_params = [_client_param(item) for item in _param_items(client_def)]
                    self.assertTrue(client_params, (signal_name, client_id))
                    self.assertEqual(client_params[0][0], "目标玩家", (signal_name, client_id))

                entry_params = [_entry_param(item) for item in _entry_params(entry)]
                client_signature = [_client_param(item) for item in _param_items(clients[canonical_id])][1:]
                self.assertEqual(
                    [(name, type_id) for name, type_id, _ in entry_params],
                    [(name, type_id) for name, type_id, _ in client_signature],
                    signal_name,
                )
                self.assertEqual(
                    [port for _, _, port in entry_params],
                    [port for _, _, port in client_signature],
                    signal_name,
                )

                server_meta = _decode_message(after_defs[roles["server"]]["4"]).get("2")
                server_meta = _decode_message(server_meta)
                self.assertEqual(
                    {key: server_meta.get(key) for key in ("1", "2", "3", "5")},
                    {"1": 10001, "2": 20002, "3": 22000, "5": 2000},
                    signal_name,
                )

                if not entry_params:
                    for client_id, client_def in clients.items():
                        self.assertEqual(_client_param(_param_items(client_def)[0])[0], "目标玩家", (signal_name, client_id))

            self.assertEqual(
                {key: _client_index(value) for key, value in _named_clients(after_defs, "结算").items()},
                {1610612752: 6, 1610612802: 14},
            )
            self.assertEqual(hashlib.sha256(SAVE5.read_bytes()).hexdigest(), original_sha)

            second_result = _run_writer(output1, output2, spec_path)
            self.assertEqual(second_result["registry_repairs"], [])
            self.assertEqual(output1.read_bytes(), output2.read_bytes())
            self.assertEqual(hashlib.sha256(SAVE5.read_bytes()).hexdigest(), original_sha)

    def test_helper_repairs_one_damaged_entry_meta_only(self) -> None:
        root = _load_root(SAVE5)
        entries = root["10"]["5"]["3"]
        node_defs = root["10"]["2"]
        first = repair_signal_registry(entries, node_defs)
        self.assertEqual(len(first), 11)
        self.assertEqual(repair_signal_registry(entries, node_defs), [])

        target_name = "跳一跳_请求重开"
        target_entry = next(entry for entry in entries if entry.get("3") == target_name)
        target_entry["9"] = "<binary_data> "
        repairs = repair_signal_registry(entries, node_defs)
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0]["signal_name"], target_name)
        self.assertTrue(repairs[0]["entry_client_meta"])
        self.assertEqual(set(repairs[0]), {"signal_name", "canonical_client_node_def_id", "entry_client_meta"})
        self.assertEqual(repair_signal_registry(entries, node_defs), [])

    def test_helper_rejects_parameter_mismatch_without_guessing(self) -> None:
        root = _load_root(SAVE5)
        entry = next(entry for entry in root["10"]["5"]["3"] if entry.get("3") == SIGNAL_NAME)
        isolated_entries = [copy.deepcopy(entry)]
        node_defs = copy.deepcopy(root["10"]["2"])
        param_map = binary_data_text_to_decoded_field_map(isolated_entries[0]["4"][0])
        param_map["field_1"]["utf8"] = "不匹配的参数名"
        isolated_entries[0]["4"][0] = decoded_field_map_to_binary_data_text(param_map)
        with self.assertRaisesRegex(ValueError, "parameters do not match canonical client"):
            repair_signal_registry(isolated_entries, node_defs)


if __name__ == "__main__":
    unittest.main()
