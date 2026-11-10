from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import binary_data_text_to_decoded_field_map
from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import binary_data_text_to_numeric_message
from ugc_file_tools.gil_dump_codec.protobuf_like import parse_binary_data_hex_text


def _extract_int_from_maybe_int_node(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return int(value)
    if isinstance(value, dict):
        inner = value.get("int")
        if isinstance(inner, int):
            return int(inner)
    return None


def _extract_signal_node_def_id_from_signal_meta(meta_value: Any) -> Optional[int]:
    if isinstance(meta_value, dict):
        node_def_id = _extract_int_from_maybe_int_node(meta_value.get("5"))
        if node_def_id is None:
            # 兼容 decoded_field_map 形态（field_* keys）
            node_def_id = _extract_int_from_maybe_int_node(meta_value.get("field_5"))
        if isinstance(node_def_id, int) and int(node_def_id) > 0:
            return int(node_def_id)
    if isinstance(meta_value, str) and meta_value.startswith("<binary_data>"):
        decoded = binary_data_text_to_decoded_field_map(str(meta_value))
        if isinstance(decoded, dict):
            node_def_id = _extract_int_from_maybe_int_node(decoded.get("field_5"))
            if isinstance(node_def_id, int) and int(node_def_id) > 0:
                return int(node_def_id)
    return None


def _extract_signal_param_role_indices(param_value: Any) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    def _extract_from_numeric_dict(value_obj: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        send_idx = _extract_int_from_maybe_int_node(value_obj.get("4"))
        listen_idx = _extract_int_from_maybe_int_node(value_obj.get("5"))
        server_idx = _extract_int_from_maybe_int_node(value_obj.get("6"))
        return send_idx, listen_idx, server_idx

    if isinstance(param_value, dict):
        send_idx, listen_idx, server_idx = _extract_from_numeric_dict(param_value)
        # 兼容 field_* 形态（例如通过 binary_data bridge 解码得到的 dict）
        if send_idx is None:
            send_idx = _extract_int_from_maybe_int_node(param_value.get("field_4"))
        if listen_idx is None:
            listen_idx = _extract_int_from_maybe_int_node(param_value.get("field_5"))
        if server_idx is None:
            server_idx = _extract_int_from_maybe_int_node(param_value.get("field_6"))
        return send_idx, listen_idx, server_idx

    if isinstance(param_value, str) and param_value.startswith("<binary_data>"):
        decoded = binary_data_text_to_decoded_field_map(str(param_value))
        if isinstance(decoded, dict):
            send_idx = _extract_int_from_maybe_int_node(decoded.get("field_4"))
            listen_idx = _extract_int_from_maybe_int_node(decoded.get("field_5"))
            server_idx = _extract_int_from_maybe_int_node(decoded.get("field_6"))
            return send_idx, listen_idx, server_idx
    return None, None, None


def _extract_utf8_text_from_maybe_binary_data(value: Any) -> str:
    """
    兼容 dump-json 的两种常见形态：
    - 直接字符串（prefer_raw_hex_for_utf8=False）
    - `<binary_data> ...` 或 `{raw_hex, utf8}`（prefer_raw_hex_for_utf8=True 或 bridge 输出）
    """
    if isinstance(value, str):
        if value.startswith("<binary_data>"):
            return parse_binary_data_hex_text(str(value)).decode("utf-8")
        return str(value)
    if isinstance(value, dict):
        utf8 = value.get("utf8")
        if isinstance(utf8, str):
            return str(utf8)
        raw_hex = value.get("raw_hex")
        if isinstance(raw_hex, str) and raw_hex.startswith("<binary_data>"):
            return parse_binary_data_hex_text(str(raw_hex)).decode("utf-8")
    return ""


def _infer_signal_name_port_index_from_param_port_indices(*, role: str, param_port_indices: List[int]) -> Optional[int]:
    """
    当 base `.gil` 缺失 node_def（或 node_def['106'] 缺失）时，退化为仅用“参数端口 index”反推信号名端口 index。

    依据真源端口块布局（与 signal_writeback 的块分配一致）：
    - send:        signal_name=+2, first_send_param=+12  => diff=10
    - listen:      signal_name=+4, first_listen_param=+13 => diff=9
    - server_send: signal_name=+11, first_server_param=+14 => diff=3
    """
    if not (isinstance(param_port_indices, list) and param_port_indices):
        return None
    first = min(int(x) for x in list(param_port_indices) if isinstance(x, int))
    role_key = str(role).strip()
    if role_key == "send":
        inferred = int(first) - 10
    elif role_key == "listen":
        inferred = int(first) - 9
    elif role_key == "server_send":
        inferred = int(first) - 3
    else:
        return None
    return int(inferred) if int(inferred) >= 0 else None


def _extract_signal_name_port_index_from_node_def(
    *,
    node_def_object: Optional[Dict[str, Any]],
    role: str,
) -> Optional[int]:
    if not isinstance(node_def_object, dict):
        return None
    ports_value = node_def_object.get("106")
    ports: List[Dict[str, Any]] = []
    if isinstance(ports_value, list):
        ports = [x for x in ports_value if isinstance(x, dict)]
    elif isinstance(ports_value, dict):
        ports = [ports_value]
    if not ports:
        return None

    target_port_position = 0
    role_key = str(role).strip()
    if role_key == "server_send":
        target_port_position = 1
    if target_port_position >= len(ports):
        return None
    port_obj = ports[target_port_position]
    port_index = _extract_int_from_maybe_int_node(port_obj.get("8"))
    if isinstance(port_index, int) and int(port_index) >= 0:
        return int(port_index)
    return None


@dataclass(frozen=True)
class _SignalWritebackMaps:
    send_node_def_id_by_signal_name: Dict[str, int]
    listen_node_def_id_by_signal_name: Dict[str, int]
    server_send_node_def_id_by_signal_name: Dict[str, int]
    send_signal_name_port_index_by_signal_name: Dict[str, int]
    send_param_port_indices_by_signal_name: Dict[str, List[int]]
    listen_signal_name_port_index_by_signal_name: Dict[str, int]
    listen_param_port_indices_by_signal_name: Dict[str, List[int]]
    server_send_signal_name_port_index_by_signal_name: Dict[str, int]
    server_send_param_port_indices_by_signal_name: Dict[str, List[int]]
    # signal_name -> signal_index_int（signal_entry.field_6；用于信号节点实例写回 field_9）
    signal_index_by_signal_name: Dict[str, int]
    # signal_name -> [param_var_type_id]（按信号定义参数顺序；用于写回侧覆写发送信号参数端口的 VarType）
    param_var_type_ids_by_signal_name: Dict[str, List[int]]
    # 发送客户端脚本信号（300005）：signal_name -> 该信号专属 node_def_id / 端口索引
    client_send_node_def_id_by_signal_name: Dict[str, int]
    client_send_signal_name_port_index_by_signal_name: Dict[str, int]
    client_send_target_player_port_index_by_signal_name: Dict[str, int]
    client_send_param_port_indices_by_signal_name: Dict[str, List[int]]
    client_send_signal_index_by_signal_name: Dict[str, int]


def _build_empty_signal_writeback_maps() -> _SignalWritebackMaps:
    return _SignalWritebackMaps(
        send_node_def_id_by_signal_name={},
        listen_node_def_id_by_signal_name={},
        server_send_node_def_id_by_signal_name={},
        send_signal_name_port_index_by_signal_name={},
        send_param_port_indices_by_signal_name={},
        listen_signal_name_port_index_by_signal_name={},
        listen_param_port_indices_by_signal_name={},
        server_send_signal_name_port_index_by_signal_name={},
        server_send_param_port_indices_by_signal_name={},
        signal_index_by_signal_name={},
        param_var_type_ids_by_signal_name={},
        client_send_node_def_id_by_signal_name={},
        client_send_signal_name_port_index_by_signal_name={},
        client_send_target_player_port_index_by_signal_name={},
        client_send_param_port_indices_by_signal_name={},
        client_send_signal_index_by_signal_name={},
    )


def _extract_signal_node_def_id_maps_from_payload_root(
    *,
    payload_root: Dict[str, Any],
) -> _SignalWritebackMaps:
    """
    从当前 base payload(section10/5/3) 提取信号映射：
    - signal_name -> send_node_def_id
    - signal_name -> listen_node_def_id
    - signal_name -> send_to_server_node_def_id
    - signal_name -> (send/listen/send_to_server) 参数端口索引列表
    - signal_name -> (send/listen/send_to_server) 信号名端口索引（优先从 node_def['106'] 提取，缺失时回退参数推断）
    """
    send_node_def_id_by_signal_name: Dict[str, int] = {}
    listen_node_def_id_by_signal_name: Dict[str, int] = {}
    server_send_node_def_id_by_signal_name: Dict[str, int] = {}
    send_signal_name_port_index_by_signal_name: Dict[str, int] = {}
    send_param_port_indices_by_signal_name: Dict[str, List[int]] = {}
    listen_signal_name_port_index_by_signal_name: Dict[str, int] = {}
    listen_param_port_indices_by_signal_name: Dict[str, List[int]] = {}
    server_send_signal_name_port_index_by_signal_name: Dict[str, int] = {}
    server_send_param_port_indices_by_signal_name: Dict[str, List[int]] = {}
    signal_index_by_signal_name: Dict[str, int] = {}
    param_var_type_ids_by_signal_name: Dict[str, List[int]] = {}
    client_send_node_def_id_by_signal_name: Dict[str, int] = {}
    client_send_signal_name_port_index_by_signal_name: Dict[str, int] = {}
    client_send_target_player_port_index_by_signal_name: Dict[str, int] = {}
    client_send_param_port_indices_by_signal_name: Dict[str, List[int]] = {}
    client_send_signal_index_by_signal_name: Dict[str, int] = {}

    section10 = payload_root.get("10")
    if not isinstance(section10, dict):
        return _build_empty_signal_writeback_maps()
    section5 = section10.get("5")
    if not isinstance(section5, dict):
        return _build_empty_signal_writeback_maps()

    node_def_by_id: Dict[int, Dict[str, Any]] = {}
    node_def_wrappers_raw = section10.get("2")
    node_def_wrappers: List[Dict[str, Any]] = []
    if isinstance(node_def_wrappers_raw, list):
        node_def_wrappers = [x for x in node_def_wrappers_raw if isinstance(x, dict)]
    elif isinstance(node_def_wrappers_raw, dict):
        node_def_wrappers = [node_def_wrappers_raw]

    # 浅层 dump 下 node_def['4']['1'] 可能是 <binary_data>；section5/2 的 meta 索引与 wrappers
    # 一一对应时优先按位置取 node_def_id。
    meta_list_raw = section5.get("2")
    node_def_meta_list: List[Any] = []
    if isinstance(meta_list_raw, list):
        node_def_meta_list = list(meta_list_raw)
    elif isinstance(meta_list_raw, (dict, str)):
        node_def_meta_list = [meta_list_raw]

    # 同名客户端定义可能有多个；已注册 entry9 的 ID 才是图写回应使用的
    # 定义，不能由 node_defs 的排列顺序决定端口和独立索引。
    registered_clients: Dict[str, int] = {}
    raw_entries = section5.get("3")
    for raw_entry in (raw_entries if isinstance(raw_entries, list) else [raw_entries]):
        entry_value = binary_data_text_to_numeric_message(raw_entry) if isinstance(raw_entry, str) and raw_entry.startswith("<binary_data>") else raw_entry
        if not isinstance(entry_value, dict):
            continue
        name = _extract_utf8_text_from_maybe_binary_data(entry_value.get("3"))
        client_id = _extract_signal_node_def_id_from_signal_meta(entry_value.get("9"))
        if name and isinstance(client_id, int):
            registered_clients[name] = client_id

    for wrapper_index, wrapper in enumerate(node_def_wrappers):
        node_def_obj = wrapper.get("1")
        if not isinstance(node_def_obj, dict):
            continue
        node_def_id: Optional[int] = None
        if len(node_def_meta_list) == len(node_def_wrappers) and wrapper_index < len(node_def_meta_list):
            meta_item = node_def_meta_list[wrapper_index]
            if isinstance(meta_item, dict):
                node_def_id = _extract_int_from_maybe_int_node(meta_item.get("5"))
                if node_def_id is None:
                    node_def_id = _extract_int_from_maybe_int_node(meta_item.get("field_5"))
        if node_def_id is None:
            meta_outer = node_def_obj.get("4")
            meta_inner = meta_outer.get("1") if isinstance(meta_outer, dict) else None
            if isinstance(meta_inner, str) and meta_inner.startswith("<binary_data>"):
                decoded_inner = binary_data_text_to_numeric_message(meta_inner)
                if isinstance(decoded_inner, dict):
                    meta_inner = decoded_inner
            if isinstance(meta_inner, dict):
                node_def_id = _extract_int_from_maybe_int_node(meta_inner.get("5"))
        if isinstance(node_def_id, int) and int(node_def_id) > 0 and int(node_def_id) not in node_def_by_id:
            node_def_by_id[int(node_def_id)] = node_def_obj

        if str(node_def_obj.get("200") or "").strip() == "发送客户端脚本信号":
            sig_node = node_def_obj.get("107")
            sig_binding = sig_node.get("108") if isinstance(sig_node, dict) else None
            if isinstance(sig_binding, str) and sig_binding.startswith("<binary_data>"):
                decoded_binding = binary_data_text_to_numeric_message(sig_binding)
                if isinstance(decoded_binding, dict):
                    sig_binding = decoded_binding
            sig_name_raw = sig_binding.get("1") if isinstance(sig_binding, dict) else None
            sig_name = str(_extract_utf8_text_from_maybe_binary_data(sig_name_raw)).strip()
            if (sig_name != "" and isinstance(node_def_id, int) and int(node_def_id) > 0
                    and registered_clients.get(sig_name, node_def_id) == node_def_id):
                client_send_node_def_id_by_signal_name[str(sig_name)] = int(node_def_id)
                # 客户端定义的独立索引不是 signal_entry.field6。存档5中前者
                # 为6，而三类客户端信号对应的 entry index 为8/9/12。
                meta = node_def_obj.get("4")
                client_index = _extract_int_from_maybe_int_node(meta.get("5")) if isinstance(meta, dict) else None
                if isinstance(client_index, int):
                    client_send_signal_index_by_signal_name[str(sig_name)] = int(client_index)

                port_obj = node_def_obj.get("106")
                if isinstance(port_obj, dict):
                    port_index = _extract_int_from_maybe_int_node(port_obj.get("8"))
                    if isinstance(port_index, int) and int(port_index) >= 0:
                        client_send_signal_name_port_index_by_signal_name[str(sig_name)] = int(port_index)

                params_raw = node_def_obj.get("102")
                param_items: List[Dict[str, Any]] = []
                raw_items: List[Any] = []
                if isinstance(params_raw, list):
                    raw_items = list(params_raw)
                elif isinstance(params_raw, (dict, str)):
                    raw_items = [params_raw]
                for raw_item in raw_items:
                    if isinstance(raw_item, dict):
                        param_items.append(raw_item)
                    elif isinstance(raw_item, str) and raw_item.startswith("<binary_data>"):
                        decoded_item = binary_data_text_to_numeric_message(raw_item)
                        if isinstance(decoded_item, dict):
                            param_items.append(decoded_item)
                if param_items:
                    target_port = _extract_int_from_maybe_int_node(param_items[0].get("8"))
                    if target_port is None:
                        target_port = _extract_int_from_maybe_int_node(param_items[0].get("field_8"))
                    if isinstance(target_port, int) and int(target_port) >= 0:
                        client_send_target_player_port_index_by_signal_name[str(sig_name)] = int(target_port)
                    param_ports: List[int] = []
                    for item in param_items[1:]:
                        port_value = _extract_int_from_maybe_int_node(item.get("8"))
                        if port_value is None:
                            port_value = _extract_int_from_maybe_int_node(item.get("field_8"))
                        if isinstance(port_value, int) and int(port_value) >= 0:
                            param_ports.append(int(port_value))
                    client_send_param_port_indices_by_signal_name[str(sig_name)] = list(param_ports)

    signal_entries_value = section5.get("3")
    # 兼容两种 dump 形态：
    # - prefer_raw_hex_for_utf8=False：entry 通常为 dict
    # - prefer_raw_hex_for_utf8=True：entry 可能被保留为 "<binary_data>..."（lossless），需在此解码
    signal_entries: List[Any] = []
    if isinstance(signal_entries_value, list):
        signal_entries = [x for x in signal_entries_value if isinstance(x, (dict, str))]
    elif isinstance(signal_entries_value, (dict, str)):
        signal_entries = [signal_entries_value]
    else:
        return _build_empty_signal_writeback_maps()

    for entry in signal_entries:
        entry_obj: Any = entry
        if isinstance(entry, str) and entry.startswith("<binary_data>"):
            decoded_entry = binary_data_text_to_decoded_field_map(str(entry))
            if not isinstance(decoded_entry, dict):
                continue
            entry_obj = decoded_entry
        if not isinstance(entry_obj, dict):
            continue

        signal_name = str(_extract_utf8_text_from_maybe_binary_data(entry_obj.get("3") or entry_obj.get("field_3"))).strip()
        if signal_name == "":
            continue

        # signal_index（signal_entry.field_6）
        raw_signal_index = entry_obj.get("6") if "6" in entry_obj else entry_obj.get("field_6")
        signal_index_int = _extract_int_from_maybe_int_node(raw_signal_index)
        if isinstance(signal_index_int, int) and int(signal_index_int) >= 0:
            signal_index_by_signal_name[str(signal_name)] = int(signal_index_int)

        # 默认补齐空列表映射（无参数信号也应有 key → []，便于上游统一处理）
        if str(signal_name) not in send_param_port_indices_by_signal_name:
            send_param_port_indices_by_signal_name[str(signal_name)] = []
        if str(signal_name) not in listen_param_port_indices_by_signal_name:
            listen_param_port_indices_by_signal_name[str(signal_name)] = []
        if str(signal_name) not in server_send_param_port_indices_by_signal_name:
            server_send_param_port_indices_by_signal_name[str(signal_name)] = []

        send_id = _extract_signal_node_def_id_from_signal_meta(entry_obj.get("1") if "1" in entry_obj else entry_obj.get("field_1"))
        if isinstance(send_id, int) and int(send_id) > 0:
            send_node_def_id_by_signal_name[str(signal_name)] = int(send_id)
            send_name_port_index = _extract_signal_name_port_index_from_node_def(
                node_def_object=node_def_by_id.get(int(send_id)),
                role="send",
            )
            if isinstance(send_name_port_index, int) and int(send_name_port_index) >= 0:
                send_signal_name_port_index_by_signal_name[str(signal_name)] = int(send_name_port_index)

        listen_id = _extract_signal_node_def_id_from_signal_meta(entry_obj.get("2") if "2" in entry_obj else entry_obj.get("field_2"))
        if isinstance(listen_id, int) and int(listen_id) > 0:
            listen_node_def_id_by_signal_name[str(signal_name)] = int(listen_id)
            listen_name_port_index = _extract_signal_name_port_index_from_node_def(
                node_def_object=node_def_by_id.get(int(listen_id)),
                role="listen",
            )
            if isinstance(listen_name_port_index, int) and int(listen_name_port_index) >= 0:
                listen_signal_name_port_index_by_signal_name[str(signal_name)] = int(listen_name_port_index)

        server_id = _extract_signal_node_def_id_from_signal_meta(entry_obj.get("7") if "7" in entry_obj else entry_obj.get("field_7"))
        if isinstance(server_id, int) and int(server_id) > 0:
            server_send_node_def_id_by_signal_name[str(signal_name)] = int(server_id)
            server_name_port_index = _extract_signal_name_port_index_from_node_def(
                node_def_object=node_def_by_id.get(int(server_id)),
                role="server_send",
            )
            if isinstance(server_name_port_index, int) and int(server_name_port_index) >= 0:
                server_send_signal_name_port_index_by_signal_name[str(signal_name)] = int(server_name_port_index)

        params_raw = entry_obj.get("4")
        if params_raw is None:
            params_raw = entry_obj.get("field_4")
        params_list: List[Any] = []
        if isinstance(params_raw, list):
            params_list = [x for x in params_raw if isinstance(x, (dict, str))]
        elif isinstance(params_raw, (dict, str)):
            params_list = [params_raw]

        if params_list:
            send_param_indices: List[int] = []
            listen_param_indices: List[int] = []
            server_param_indices: List[int] = []
            param_var_type_ids: List[int] = []
            for param in params_list:
                # param.type_id（信号规格 VarType）
                type_id: Optional[int] = None
                if isinstance(param, dict):
                    type_id = _extract_int_from_maybe_int_node(param.get("2"))
                    if type_id is None:
                        type_id = _extract_int_from_maybe_int_node(param.get("field_2"))
                elif isinstance(param, str) and param.startswith("<binary_data>"):
                    decoded = binary_data_text_to_decoded_field_map(str(param))
                    if isinstance(decoded, dict):
                        type_id = _extract_int_from_maybe_int_node(decoded.get("field_2"))
                param_var_type_ids.append(int(type_id) if isinstance(type_id, int) and int(type_id) > 0 else 0)

                send_param, listen_param, server_param = _extract_signal_param_role_indices(param)
                if isinstance(send_param, int) and int(send_param) >= 0:
                    send_param_indices.append(int(send_param))
                if isinstance(listen_param, int) and int(listen_param) >= 0:
                    listen_param_indices.append(int(listen_param))
                if isinstance(server_param, int) and int(server_param) >= 0:
                    server_param_indices.append(int(server_param))

            if send_param_indices:
                send_param_port_indices_by_signal_name[str(signal_name)] = list(send_param_indices)
                if str(signal_name) not in send_signal_name_port_index_by_signal_name:
                    inferred = _infer_signal_name_port_index_from_param_port_indices(
                        role="send",
                        param_port_indices=list(send_param_indices),
                    )
                    if isinstance(inferred, int):
                        send_signal_name_port_index_by_signal_name[str(signal_name)] = int(inferred)
            if listen_param_indices:
                listen_param_port_indices_by_signal_name[str(signal_name)] = list(listen_param_indices)
                if str(signal_name) not in listen_signal_name_port_index_by_signal_name:
                    inferred = _infer_signal_name_port_index_from_param_port_indices(
                        role="listen",
                        param_port_indices=list(listen_param_indices),
                    )
                    if isinstance(inferred, int):
                        listen_signal_name_port_index_by_signal_name[str(signal_name)] = int(inferred)
            if server_param_indices:
                server_send_param_port_indices_by_signal_name[str(signal_name)] = list(server_param_indices)
                if str(signal_name) not in server_send_signal_name_port_index_by_signal_name:
                    inferred = _infer_signal_name_port_index_from_param_port_indices(
                        role="server_send",
                        param_port_indices=list(server_param_indices),
                    )
                    if isinstance(inferred, int):
                        server_send_signal_name_port_index_by_signal_name[str(signal_name)] = int(inferred)
            if any(int(x) > 0 for x in list(param_var_type_ids)):
                param_var_type_ids_by_signal_name[str(signal_name)] = [int(x) for x in list(param_var_type_ids)]

    return _SignalWritebackMaps(
        send_node_def_id_by_signal_name=dict(send_node_def_id_by_signal_name),
        listen_node_def_id_by_signal_name=dict(listen_node_def_id_by_signal_name),
        server_send_node_def_id_by_signal_name=dict(server_send_node_def_id_by_signal_name),
        send_signal_name_port_index_by_signal_name=dict(send_signal_name_port_index_by_signal_name),
        send_param_port_indices_by_signal_name=dict(send_param_port_indices_by_signal_name),
        listen_signal_name_port_index_by_signal_name=dict(listen_signal_name_port_index_by_signal_name),
        listen_param_port_indices_by_signal_name=dict(listen_param_port_indices_by_signal_name),
        server_send_signal_name_port_index_by_signal_name=dict(server_send_signal_name_port_index_by_signal_name),
        server_send_param_port_indices_by_signal_name=dict(server_send_param_port_indices_by_signal_name),
        signal_index_by_signal_name=dict(signal_index_by_signal_name),
        param_var_type_ids_by_signal_name=dict(param_var_type_ids_by_signal_name),
        client_send_node_def_id_by_signal_name=dict(client_send_node_def_id_by_signal_name),
        client_send_signal_name_port_index_by_signal_name=dict(client_send_signal_name_port_index_by_signal_name),
        client_send_target_player_port_index_by_signal_name=dict(client_send_target_player_port_index_by_signal_name),
        client_send_param_port_indices_by_signal_name=dict(client_send_param_port_indices_by_signal_name),
        client_send_signal_index_by_signal_name=dict(client_send_signal_index_by_signal_name),
    )

