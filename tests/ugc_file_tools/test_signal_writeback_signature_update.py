from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch


EDITOR_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = EDITOR_ROOT.parents[1]
PRIVATE_EXTENSIONS = EDITOR_ROOT / "private_extensions"
sys.path.insert(0, str(PRIVATE_EXTENSIONS))

from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import binary_data_text_to_numeric_message  # noqa: E402
from ugc_file_tools.signal_writeback import helpers as h  # noqa: E402
from ugc_file_tools.signal_writeback import writer  # noqa: E402


SAVE4 = PROJECT_ROOT / "项目" / "跳一跳" / "存档4.gil"
SIGNAL_NAME = "跳一跳_跳跃上报"
SETTLEMENT_NAME = "结算"
FOUR_PARAMS = [
    {"param_name": "座位号", "type_id": 3},
    {"param_name": "平台序号", "type_id": 3},
    {"param_name": "落点X", "type_id": 5},
    {"param_name": "成功", "type_id": 3},
]


def _write_spec(path: Path, params: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"signals": [{"signal_name": SIGNAL_NAME, "params": params}]}, ensure_ascii=False),
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


def _signal_entry(root: Mapping[str, Any], signal_name: str) -> dict[str, Any]:
    for entry in h._extract_signal_entries_from_payload_root(root):
        if str(entry.get("3") or "").strip() == signal_name:
            return entry
    raise AssertionError(f"missing signal entry: {signal_name}")


def _entry_signature(entry: Mapping[str, Any]) -> list[tuple[str, int]]:
    values = entry.get("4")
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    signature: list[tuple[str, int]] = []
    for value in values:
        decoded = h.binary_data_text_to_decoded_field_map(str(value))
        name = decoded.get("field_1", {}).get("utf8")
        type_id = h._extract_nested_int(decoded, ["field_2", "int"])
        if not isinstance(name, str) or not isinstance(type_id, int):
            raise AssertionError(f"cannot decode signal parameter: {decoded!r}")
        signature.append((name, int(type_id)))
    return signature


def _signal_role_ids(entry: Mapping[str, Any]) -> tuple[int, int, int]:
    send_id = h._extract_nested_int(entry, ["1", "5"])
    listen_id = h._extract_nested_int(entry, ["2", "5"])
    server_meta = entry.get("7")
    if isinstance(server_meta, str):
        server_meta = binary_data_text_to_numeric_message(server_meta)
    server_id = h._extract_nested_int(server_meta, ["5"]) if isinstance(server_meta, Mapping) else None
    if not all(isinstance(value, int) for value in (send_id, listen_id, server_id)):
        raise AssertionError("signal entry has incomplete role IDs")
    return int(send_id), int(listen_id), int(server_id)


def _node_param_signatures(node_def: Mapping[str, Any], field_name: str) -> list[tuple[str, int]]:
    value = node_def.get(field_name)
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    result: list[tuple[str, int]] = []
    for item in items:
        if isinstance(item, str):
            decoded = h.binary_data_text_to_decoded_field_map(item)
            name = decoded.get("field_1", {}).get("utf8")
            type_id = h._extract_nested_int(decoded, ["field_4", "message", "field_3", "int"])
            if not isinstance(type_id, int):
                type_id = h._extract_nested_int(decoded, ["field_4", "message", "field_3"])
        elif isinstance(item, Mapping):
            name = item.get("1")
            type_value = item.get("4")
            if isinstance(type_value, str):
                type_value = binary_data_text_to_numeric_message(type_value)
            type_id = h._extract_nested_int(type_value, ["3"]) if isinstance(type_value, Mapping) else None
        else:
            continue
        if isinstance(name, str) and isinstance(type_id, int):
            result.append((name, int(type_id)))
    return result


def _client_signal_name(node_def: Mapping[str, Any]) -> str:
    signal = node_def.get("107")
    binding = signal.get("108") if isinstance(signal, Mapping) else None
    if isinstance(binding, str):
        binding = binary_data_text_to_numeric_message(binding)
    name = binding.get("1") if isinstance(binding, Mapping) else None
    return str(name or "").strip()


def _client_defs(root: Mapping[str, Any], signal_name: str) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for node_def_id, node_def in h._index_node_defs_by_id(root).items():
        if str(node_def.get("200") or "").strip() == "发送客户端脚本信号" and _client_signal_name(node_def) == signal_name:
            result[int(node_def_id)] = node_def
    return result


def _client_index(node_def: Mapping[str, Any]) -> int:
    meta = node_def.get("4")
    value = h._extract_nested_int(meta, ["5"]) if isinstance(meta, Mapping) else None
    if not isinstance(value, int):
        raise AssertionError("client-send definition has no signal index")
    return int(value)


def _client_signature(node_def: Mapping[str, Any]) -> list[tuple[str, int]]:
    values = _node_param_signatures(node_def, "102")
    if not values or values[0][0] != "目标玩家":
        raise AssertionError(f"client-send target player parameter missing: {values!r}")
    return values[1:]


def _server_expected_signature(params: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return [
        (
            str(item["param_name"]),
            h._map_server_signal_descriptor_type_id(var_type_id=int(item["type_id"])),
        )
        for item in params
    ]


class SignalWritebackSignatureUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        if not SAVE4.is_file():
            self.skipTest(f"real save fixture unavailable: {SAVE4}")

    def test_real_save_four_param_update_and_idempotence(self) -> None:
        before_root = _load_root(SAVE4)
        before_entry = _signal_entry(before_root, SIGNAL_NAME)
        self.assertEqual(_entry_signature(before_entry), [("座位号", 3), ("蓄力时长", 5)])
        before_ids = _signal_role_ids(before_entry)
        before_index = int(before_entry["6"])
        before_target_clients = _client_defs(before_root, SIGNAL_NAME)
        before_target_identity = {
            node_def_id: _client_index(node_def)
            for node_def_id, node_def in before_target_clients.items()
        }
        before_settlement_clients = _client_defs(before_root, SETTLEMENT_NAME)
        self.assertEqual(len(before_settlement_clients), 2)
        before_settlement_identity = {
            node_def_id: _client_index(node_def)
            for node_def_id, node_def in before_settlement_clients.items()
        }

        with tempfile.TemporaryDirectory(prefix="signal-writeback-signature-") as temp:
            temp_path = Path(temp)
            spec_path = temp_path / "signals.json"
            output1 = temp_path / "updated.gil"
            output2 = temp_path / "idempotent.gil"
            _write_spec(spec_path, FOUR_PARAMS)

            first_result = _run_writer(SAVE4, output1, spec_path)
            self.assertEqual(len(first_result["updated_signals"]), 1)
            self.assertEqual(first_result["added_signals"], [])
            self.assertEqual(first_result["updated_signals"][0]["old_params"], [
                {"param_name": "座位号", "type_id": 3},
                {"param_name": "蓄力时长", "type_id": 5},
            ])

            after_root = _load_root(output1)
            after_entry = _signal_entry(after_root, SIGNAL_NAME)
            expected_signature = [(item["param_name"], item["type_id"]) for item in FOUR_PARAMS]
            self.assertEqual(_entry_signature(after_entry), expected_signature)
            self.assertEqual(_signal_role_ids(after_entry), before_ids)
            self.assertEqual(int(after_entry["6"]), before_index)

            node_defs = h._index_node_defs_by_id(after_root)
            send_id, listen_id, server_id = _signal_role_ids(after_entry)
            self.assertEqual(_node_param_signatures(node_defs[send_id], "102"), expected_signature)
            self.assertEqual(
                _node_param_signatures(node_defs[listen_id], "103")[3:],
                expected_signature,
            )
            self.assertEqual(
                _node_param_signatures(node_defs[server_id], "102"),
                _server_expected_signature(FOUR_PARAMS),
            )

            after_target_clients = _client_defs(after_root, SIGNAL_NAME)
            self.assertEqual(set(after_target_clients), set(before_target_clients))
            self.assertEqual(len(after_target_clients), 1)
            self.assertEqual(
                {node_def_id: _client_index(node_def) for node_def_id, node_def in after_target_clients.items()},
                before_target_identity,
            )
            for client_def in after_target_clients.values():
                self.assertEqual(_client_signature(client_def), expected_signature)

            after_settlement_clients = _client_defs(after_root, SETTLEMENT_NAME)
            self.assertEqual(
                {node_def_id: _client_index(node_def) for node_def_id, node_def in after_settlement_clients.items()},
                before_settlement_identity,
            )
            self.assertEqual(_entry_signature(_signal_entry(after_root, SETTLEMENT_NAME)), [])
            self.assertEqual(len(after_settlement_clients), 2)
            for client_def in after_settlement_clients.values():
                self.assertEqual(_client_signature(client_def), [])

            second_result = _run_writer(output1, output2, spec_path)
            self.assertEqual(second_result["updated_signals"], [])
            self.assertEqual(second_result["added_signals"], [])
            self.assertEqual(output1.read_bytes(), output2.read_bytes())

    def test_name_type_and_order_changes_trigger_rebuild(self) -> None:
        variants = [
            [
                {"param_name": "座位编号", "type_id": 3},
                *FOUR_PARAMS[1:],
            ],
            [
                FOUR_PARAMS[0],
                {"param_name": "平台序号", "type_id": 5},
                *FOUR_PARAMS[2:],
            ],
            [FOUR_PARAMS[1], FOUR_PARAMS[0], *FOUR_PARAMS[2:]],
        ]

        with tempfile.TemporaryDirectory(prefix="signal-writeback-signature-variants-") as temp:
            temp_path = Path(temp)
            spec_path = temp_path / "signals.json"
            current_input = SAVE4
            for index, params in enumerate(variants):
                if index == 0:
                    first_path = temp_path / "four-param.gil"
                    _write_spec(spec_path, FOUR_PARAMS)
                    result = _run_writer(current_input, first_path, spec_path)
                    self.assertEqual(len(result["updated_signals"]), 1)
                    current_input = first_path
                output_path = temp_path / f"variant-{index}.gil"
                _write_spec(spec_path, params)
                result = _run_writer(current_input, output_path, spec_path)
                self.assertEqual(len(result["updated_signals"]), 1, f"variant {index} was not recognized as a change")
                root = _load_root(output_path)
                entry = _signal_entry(root, SIGNAL_NAME)
                expected = [(item["param_name"], item["type_id"]) for item in params]
                self.assertEqual(_entry_signature(entry), expected)
                node_defs = h._index_node_defs_by_id(root)
                send_id, listen_id, server_id = _signal_role_ids(entry)
                self.assertEqual(_node_param_signatures(node_defs[send_id], "102"), expected)
                self.assertEqual(_node_param_signatures(node_defs[listen_id], "103")[3:], expected)
                self.assertEqual(
                    _node_param_signatures(node_defs[server_id], "102"),
                    _server_expected_signature(params),
                )
                for client_def in _client_defs(root, SIGNAL_NAME).values():
                    self.assertEqual(_client_signature(client_def), expected)
                current_input = output_path


if __name__ == "__main__":
    unittest.main()
