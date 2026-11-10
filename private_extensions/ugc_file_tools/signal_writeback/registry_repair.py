from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import (
    binary_data_text_to_decoded_field_map,
    binary_data_text_to_numeric_message,
    decoded_field_map_to_binary_data_text,
    numeric_message_to_binary_data_text,
)


_BINARY_PREFIX = "<binary_data>"
_CLIENT_META = {"1": 10001, "2": 20000, "3": 22001}
_SERVER_META = {"1": 10001, "2": 20002, "3": 22001}
_SERVER_BUILTIN_META = {"1": 10001, "2": 20002, "3": 22000, "5": 2000}
_ROLE_TITLES = {
    "send": "发送信号",
    "listen": "监听信号",
    "server": "向服务器节点图发送信号",
}


def _binary_map(value: Any, context: str, *, optional: bool = False) -> Optional[Dict[str, Any]]:
    if isinstance(value, Mapping):
        return copy.deepcopy({str(key): item for key, item in value.items()})
    if isinstance(value, str) and value.startswith(_BINARY_PREFIX):
        try:
            decoded = binary_data_text_to_numeric_message(value)
        except Exception as exc:
            raise ValueError(f"signal registry repair: cannot decode {context}: {exc}") from exc
        return dict(decoded)
    if optional and value is None:
        return None
    raise ValueError(f"signal registry repair: {context} must be a message or {_BINARY_PREFIX} text")


def _field(mapping: Mapping[str, Any], number: int) -> Any:
    key = str(int(number))
    if key in mapping:
        return mapping[key]
    return mapping.get(f"field_{key}")


def _int_value(value: Any) -> Optional[int]:
    if type(value) is int:
        return int(value)
    if isinstance(value, Mapping):
        for key in ("int", "field_1"):
            child = value.get(key)
            if type(child) is int:
                return int(child)
            if isinstance(child, Mapping):
                nested = _int_value(child)
                if nested is not None:
                    return nested
        message = value.get("message")
        if isinstance(message, Mapping):
            for child in message.values():
                nested = _int_value(child)
                if nested is not None:
                    return nested
    return None


def _text_value(value: Any) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("utf8", "text"):
            child = value.get(key)
            if isinstance(child, str):
                return child
        raw_hex = value.get("raw_hex")
        if isinstance(raw_hex, str):
            try:
                return bytes.fromhex(raw_hex).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return None
    return None


def _get_message_field(parent: Mapping[str, Any], number: int, context: str, *, optional: bool = False) -> Optional[Dict[str, Any]]:
    return _binary_map(_field(parent, number), context, optional=optional)


def _normalized(value: Any, context: str = "message") -> Any:
    if isinstance(value, str) and value.startswith(_BINARY_PREFIX):
        return _normalized(_binary_map(value, context), context)
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.startswith("field_") and key_text[6:].isdigit():
                key_text = key_text[6:]
            result[key_text] = _normalized(item, context)
        return result
    if isinstance(value, list):
        return [_normalized(item, context) for item in value]
    return value


def _encode_like(original: Any, message: Mapping[str, Any]) -> Any:
    if isinstance(original, str) and original.startswith(_BINARY_PREFIX):
        return numeric_message_to_binary_data_text(message)
    return copy.deepcopy(dict(message))


def _set_message_field(parent: MutableMapping[str, Any], number: int, expected: Mapping[str, Any]) -> bool:
    key = str(int(number))
    original = parent.get(key)
    if key not in parent and f"field_{key}" in parent:
        key = f"field_{key}"
        original = parent.get(key)
    expected_copy = copy.deepcopy({str(k): v for k, v in expected.items()})
    if original is not None:
        current = _binary_map(original, f"field {number}")
        if _normalized(current) == _normalized(expected_copy):
            return False
        parent[key] = _encode_like(original, expected_copy)
        return True
    parent[key] = expected_copy
    return True


def _set_scalar_field(parent: MutableMapping[str, Any], number: int, expected: Any) -> bool:
    key = str(int(number))
    if key not in parent and f"field_{key}" in parent:
        key = f"field_{key}"
    if parent.get(key) == expected and key in parent:
        return False
    parent[key] = expected
    return True


def _metadata_id(value: Any, context: str) -> Optional[int]:
    message = _binary_map(value, context, optional=True)
    if message is None:
        return None
    return _int_value(_field(message, 5))


def _canonical_meta(value: Any, fields: Mapping[str, int], context: str) -> Dict[str, Any]:
    source = _binary_map(value, context, optional=True) or {}
    result = source
    for key, item in fields.items():
        result[str(key)] = int(item)
    return result


def _param_items(value: Any, context: str) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _decoded_param(raw: Any, context: str) -> tuple[str, int, Optional[int]]:
    if isinstance(raw, str) and raw.startswith(_BINARY_PREFIX):
        try:
            decoded = binary_data_text_to_decoded_field_map(raw)
        except Exception as exc:
            raise ValueError(f"signal registry repair: cannot decode {context}: {exc}") from exc
        name = _text_value(decoded.get("field_1"))
        type_node = decoded.get("field_4")
        type_message = type_node.get("message") if isinstance(type_node, Mapping) else None
        type_value = _int_value(type_message.get("field_3")) if isinstance(type_message, Mapping) else None
        port_value = _int_value(decoded.get("field_8"))
    elif isinstance(raw, Mapping):
        name = _text_value(_field(raw, 1))
        type_descriptor = _field(raw, 4)
        if isinstance(type_descriptor, str) and type_descriptor.startswith(_BINARY_PREFIX):
            type_descriptor = _binary_map(type_descriptor, f"{context} type")
        type_value = _int_value(_field(type_descriptor, 3)) if isinstance(type_descriptor, Mapping) else None
        port_value = _int_value(_field(raw, 8))
    else:
        raise ValueError(f"signal registry repair: {context} is not a parameter message")
    if not isinstance(name, str) or name.strip() == "" or type(type_value) is not int:
        raise ValueError(f"signal registry repair: {context} is missing parameter name/type")
    return name.strip(), int(type_value), port_value


def _entry_param(raw: Any, context: str) -> tuple[str, int, Optional[int]]:
    if isinstance(raw, str) and raw.startswith(_BINARY_PREFIX):
        try:
            decoded = binary_data_text_to_decoded_field_map(raw)
        except Exception as exc:
            raise ValueError(f"signal registry repair: cannot decode {context}: {exc}") from exc
        name = _text_value(decoded.get("field_1"))
        type_value = _int_value(decoded.get("field_2"))
        port_value = _int_value(decoded.get("field_7"))
    elif isinstance(raw, Mapping):
        name = _text_value(_field(raw, 1))
        type_value = _int_value(_field(raw, 2))
        port_value = _int_value(_field(raw, 7))
    else:
        raise ValueError(f"signal registry repair: {context} is not a parameter message")
    if not isinstance(name, str) or name.strip() == "" or type(type_value) is not int:
        raise ValueError(f"signal registry repair: {context} is missing parameter name/type")
    return name.strip(), int(type_value), port_value


def _entry_role_ids(entry: Mapping[str, Any], signal_name: str) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for role, field_number in (("send", 1), ("listen", 2), ("server", 7)):
        raw = _field(entry, field_number)
        node_id = _metadata_id(raw, f"signal {signal_name!r} field {field_number}")
        if not isinstance(node_id, int):
            raise ValueError(f"signal registry repair: signal {signal_name!r} has no node_def ID in field {field_number}")
        result[role] = int(node_id)
    return result


def _node_binding(node_def: Mapping[str, Any], binding_field: int, context: str) -> Optional[Dict[str, Any]]:
    metadata = _get_message_field(node_def, 107, f"{context} field 107", optional=True)
    if metadata is None:
        return None
    return _get_message_field(metadata, binding_field, f"{context} field 107/{binding_field}", optional=True)


def _binding_client_id(binding: Optional[Mapping[str, Any]], context: str) -> Optional[int]:
    if not isinstance(binding, Mapping):
        return None
    return _metadata_id(_field(binding, 4), f"{context} binding field 4")


def _binding_name(binding: Optional[Mapping[str, Any]]) -> str:
    if not isinstance(binding, Mapping):
        return ""
    return str(_text_value(_field(binding, 1)) or "").strip()


@dataclass
class _NodeRef:
    node_id: Optional[int]
    title: str
    node_def: Dict[str, Any]
    wrapper: MutableMapping[str, Any]
    wrapper_key: str
    outer_binary: bool

    def commit(self) -> None:
        if self.outer_binary:
            self.wrapper[self.wrapper_key] = numeric_message_to_binary_data_text(self.node_def)


def _node_refs(node_defs: List[Any]) -> List[_NodeRef]:
    result: List[_NodeRef] = []
    for index, wrapper_value in enumerate(node_defs):
        if not isinstance(wrapper_value, MutableMapping):
            continue
        if "1" in wrapper_value:
            wrapper = wrapper_value
            wrapper_key = "1"
            raw_node_def = wrapper.get(wrapper_key)
        else:
            wrapper = wrapper_value
            wrapper_key = ""
            raw_node_def = wrapper
        outer_binary = isinstance(raw_node_def, str) and raw_node_def.startswith(_BINARY_PREFIX)
        if outer_binary:
            node_def = _binary_map(raw_node_def, f"node_defs[{index}]") or {}
        elif isinstance(raw_node_def, MutableMapping):
            node_def = raw_node_def
        else:
            continue
        title = str(_field(node_def, 200) or "").strip()
        node_id: Optional[int] = None
        try:
            meta = _get_message_field(node_def, 4, f"node_defs[{index}] metadata", optional=True)
            identity = _get_message_field(meta, 1, f"node_defs[{index}] identity metadata", optional=True) if meta else None
            if identity is not None:
                node_id = _int_value(_field(identity, 5))
        except ValueError:
            # Unrelated definitions may have metadata shapes outside the signal registry schema.
            node_id = None
        result.append(
            _NodeRef(
                node_id=int(node_id) if isinstance(node_id, int) else None,
                title=title,
                node_def=node_def,
                wrapper=wrapper,
                wrapper_key=wrapper_key,
                outer_binary=outer_binary,
            )
        )
    return result


def _client_name(node_ref: _NodeRef) -> str:
    if node_ref.title != "发送客户端脚本信号":
        return ""
    binding = _node_binding(node_ref.node_def, 108, f"client node_def {node_ref.node_id}")
    return _binding_name(binding)


def _client_meta(node_ref: _NodeRef, context: str) -> Dict[str, Any]:
    root_meta = _get_message_field(node_ref.node_def, 4, f"{context} metadata", optional=True) or {}
    source = _get_message_field(root_meta, 1, f"{context} client metadata", optional=True)
    if source is None:
        source = _get_message_field(root_meta, 2, f"{context} alternate client metadata", optional=True)
    fields = dict(_CLIENT_META)
    if not isinstance(node_ref.node_id, int):
        raise ValueError(f"signal registry repair: {context} has no node_def ID")
    fields["5"] = int(node_ref.node_id)
    return _canonical_meta(source, fields, f"{context} client metadata")


def _write_binding(
    node_ref: _NodeRef,
    binding_field: int,
    *,
    signal_name: str,
    role_metas: Mapping[int, Mapping[str, Any]],
    context: str,
) -> bool:
    changed = False

    def update_outer(outer: Dict[str, Any]) -> bool:
        nonlocal changed

        def update_binding(binding: Dict[str, Any]) -> bool:
            local_changed = _set_scalar_field(binding, 1, signal_name)
            for number, metadata in role_metas.items():
                local_changed = _set_message_field(binding, int(number), metadata) or local_changed
            return local_changed

        nested_changed = _update_message_field(outer, binding_field, context, update_binding)
        changed = nested_changed or changed
        return nested_changed

    _update_message_field(node_ref.node_def, 107, f"{context} field 107", update_outer)
    if changed:
        node_ref.commit()
    return changed


def _update_message_field(
    parent: MutableMapping[str, Any],
    number: int,
    context: str,
    update: Callable[[Dict[str, Any]], bool],
) -> bool:
    key = str(int(number))
    if key not in parent and f"field_{key}" in parent:
        key = f"field_{key}"
    original = parent.get(key)
    if original is None:
        raise ValueError(f"signal registry repair: {context} is missing")
    current = _binary_map(original, context)
    updated = copy.deepcopy(current)
    did_change = bool(update(updated))
    if not did_change:
        return False
    parent[key] = _encode_like(original, updated)
    return True


def _set_client_entry_meta(entry: MutableMapping[str, Any], metadata: Mapping[str, Any], context: str) -> bool:
    key = "9" if "9" in entry or "field_9" not in entry else "field_9"
    original = entry.get(key)
    source = _binary_map(original, context, optional=True) or {}
    expected = copy.deepcopy(dict(metadata))
    for unknown_key, unknown_value in source.items():
        if str(unknown_key) not in {"1", "2", "3", "5"}:
            expected.setdefault(str(unknown_key), copy.deepcopy(unknown_value))
    if original is not None and _normalized(source) == _normalized(expected):
        return False
    if isinstance(original, Mapping):
        entry[key] = expected
    else:
        entry[key] = numeric_message_to_binary_data_text(expected)
    return True


def _set_entry_param_port(entry: MutableMapping[str, Any], index: int, port: int, context: str) -> bool:
    key = "4" if "4" in entry or "field_4" not in entry else "field_4"
    value = entry.get(key)
    if value is None:
        raise ValueError(f"signal registry repair: {context} has entry parameters but field 4 is missing")
    is_list = isinstance(value, list)
    items = list(value) if is_list else [value]
    raw = items[int(index)]
    if isinstance(raw, str) and raw.startswith(_BINARY_PREFIX):
        try:
            field_map = binary_data_text_to_decoded_field_map(raw)
        except Exception as exc:
            raise ValueError(f"signal registry repair: cannot decode {context} parameter {index + 1}: {exc}") from exc
        current_port = _int_value(field_map.get("field_7"))
        if current_port == int(port):
            return False
        high16, low16 = divmod(int(port), 0x10000)
        field_map["field_7"] = {"int": int(port), "int32_high16": int(high16), "int32_low16": int(low16)}
        items[int(index)] = decoded_field_map_to_binary_data_text(field_map)
    elif isinstance(raw, MutableMapping):
        current_port = _int_value(_field(raw, 7))
        if current_port == int(port):
            return False
        _set_scalar_field(raw, 7, int(port))
    else:
        raise ValueError(f"signal registry repair: {context} parameter {index + 1} is not writable")
    entry[key] = items if is_list else items[0]
    return True


def _reverse_binding_candidates(
    roles: Mapping[str, _NodeRef],
    clients_by_id: Mapping[int, _NodeRef],
    signal_name: str,
) -> set[int]:
    candidates: set[int] = set()
    binding_fields = {"send": 101, "listen": 102, "server": 101}
    for role, node_ref in roles.items():
        binding = _node_binding(node_ref.node_def, binding_fields[role], f"{role} node_def {node_ref.node_id}")
        client_id = _binding_client_id(binding, f"{role} node_def {node_ref.node_id}")
        candidate = clients_by_id.get(int(client_id)) if isinstance(client_id, int) else None
        if candidate is not None and _client_name(candidate) == signal_name:
            candidates.add(int(client_id))
    return candidates


def _valid_entry9_client_id(entry: Mapping[str, Any], clients_by_id: Mapping[int, _NodeRef], signal_name: str) -> Optional[int]:
    raw = _field(entry, 9)
    if raw is None:
        return None
    metadata = _binary_map(raw, f"signal {signal_name!r} entry field 9", optional=True)
    if metadata is None:
        return None
    client_id = _metadata_id(metadata, f"signal {signal_name!r} entry field 9")
    if not isinstance(client_id, int):
        return None
    if any(_int_value(_field(metadata, number)) != expected for number, expected in ((1, 10001), (2, 20000), (3, 22001))):
        return None
    client = clients_by_id.get(int(client_id))
    if client is None or _client_name(client) != signal_name:
        return None
    return int(client_id)


def _canonical_client(
    entry: Mapping[str, Any],
    roles: Mapping[str, _NodeRef],
    clients: List[_NodeRef],
    signal_name: str,
) -> _NodeRef:
    clients_by_id = {int(ref.node_id): ref for ref in clients if isinstance(ref.node_id, int)}
    entry9_id = _valid_entry9_client_id(entry, clients_by_id, signal_name)
    if entry9_id is not None:
        return clients_by_id[entry9_id]
    reverse_candidates = _reverse_binding_candidates(roles, clients_by_id, signal_name)
    if reverse_candidates:
        return clients_by_id[min(reverse_candidates)]
    if not clients_by_id:
        raise ValueError(f"signal registry repair: signal {signal_name!r} has no matching client-send definition")
    return clients_by_id[min(clients_by_id)]


def _repair_one_entry(entry: MutableMapping[str, Any], node_refs: List[_NodeRef]) -> Optional[Dict[str, Any]]:
    signal_name = str(_text_value(_field(entry, 3)) or "").strip()
    if signal_name == "":
        return None
    role_ids = _entry_role_ids(entry, signal_name)
    refs_by_id: Dict[int, _NodeRef] = {}
    for ref in node_refs:
        if isinstance(ref.node_id, int):
            if ref.node_id in refs_by_id and refs_by_id[ref.node_id] is not ref:
                raise ValueError(f"signal registry repair: duplicate node_def ID {ref.node_id}")
            refs_by_id[int(ref.node_id)] = ref

    roles: Dict[str, _NodeRef] = {}
    for role, expected_title in _ROLE_TITLES.items():
        ref = refs_by_id.get(role_ids[role])
        if ref is None:
            raise ValueError(f"signal registry repair: signal {signal_name!r} {role} node_def {role_ids[role]} is missing")
        if ref.title != expected_title:
            raise ValueError(
                f"signal registry repair: signal {signal_name!r} field {role} points to {ref.title!r}, expected {expected_title!r}"
            )
        roles[role] = ref

    clients = [
        ref for ref in node_refs
        if ref.title == "发送客户端脚本信号" and _client_name(ref) == signal_name
    ]
    canonical = _canonical_client(entry, roles, clients, signal_name)
    if not isinstance(canonical.node_id, int):
        raise ValueError(f"signal registry repair: canonical client for {signal_name!r} has no node_def ID")

    client_meta = _client_meta(canonical, f"signal {signal_name!r} client {canonical.node_id}")
    entry_meta_changed = _set_client_entry_meta(entry, client_meta, f"signal {signal_name!r} field 9")

    send_meta = _canonical_meta(_field(entry, 1), {**_CLIENT_META, "5": role_ids["send"]}, f"signal {signal_name!r} send metadata")
    listen_meta = _canonical_meta(_field(entry, 2), {**_CLIENT_META, "5": role_ids["listen"]}, f"signal {signal_name!r} listen metadata")
    server_meta = _canonical_meta(_field(entry, 7), {**_SERVER_META, "5": role_ids["server"]}, f"signal {signal_name!r} server metadata")
    client_meta = _canonical_meta(client_meta, {**_CLIENT_META, "5": canonical.node_id}, f"signal {signal_name!r} canonical client metadata")

    role_binding_changes: List[str] = []
    role_binding_specs = (
        ("send", 101, {2: listen_meta, 3: server_meta, 4: client_meta}),
        ("listen", 102, {2: send_meta, 3: server_meta, 4: client_meta}),
        ("server", 101, {2: listen_meta, 3: send_meta, 4: client_meta}),
    )
    for role, binding_field, metas in role_binding_specs:
        if _write_binding(
            roles[role],
            binding_field,
            signal_name=signal_name,
            role_metas=metas,
            context=f"signal {signal_name!r} {role} binding",
        ):
            role_binding_changes.append(role)

    client_binding_changes: List[int] = []
    for client in clients:
        if not isinstance(client.node_id, int):
            raise ValueError(f"signal registry repair: same-name client for {signal_name!r} has no node_def ID")
        params = _param_items(_field(client.node_def, 102), f"client {client.node_id} parameters")
        if not params:
            raise ValueError(f"signal registry repair: client {client.node_id} for {signal_name!r} lacks the target-player parameter")
        first_name, _, first_port = _decoded_param(params[0], f"client {client.node_id} parameter 1")
        if first_name != "目标玩家" or not isinstance(first_port, int):
            raise ValueError(f"signal registry repair: client {client.node_id} for {signal_name!r} has invalid target-player parameter")
        if _write_binding(
            client,
            108,
            signal_name=signal_name,
            role_metas={2: send_meta, 3: server_meta, 4: listen_meta},
            context=f"signal {signal_name!r} client {client.node_id} binding",
        ):
            client_binding_changes.append(int(client.node_id))

    entry_params = _param_items(_field(entry, 4), f"signal {signal_name!r} entry parameters")
    client_params = _param_items(_field(canonical.node_def, 102), f"canonical client {canonical.node_id} parameters")
    if not client_params:
        raise ValueError(f"signal registry repair: canonical client {canonical.node_id} for {signal_name!r} lacks the target-player parameter")
    client_signature: List[tuple[str, int, int]] = []
    for index, param in enumerate(client_params):
        name, type_id, port = _decoded_param(param, f"client {canonical.node_id} parameter {index + 1}")
        if index == 0:
            if name != "目标玩家" or not isinstance(port, int):
                raise ValueError(f"signal registry repair: canonical client {canonical.node_id} has an invalid target-player parameter")
            continue
        if not isinstance(port, int):
            raise ValueError(f"signal registry repair: canonical client {canonical.node_id} parameter {index + 1} has no client port")
        client_signature.append((name, int(type_id), int(port)))

    entry_signature = [
        _entry_param(param, f"signal {signal_name!r} parameter {index + 1}")[:2]
        for index, param in enumerate(entry_params)
    ]
    expected_signature = [(name, type_id) for name, type_id, _ in client_signature]
    if entry_signature != expected_signature:
        raise ValueError(
            f"signal registry repair: signal {signal_name!r} parameters do not match canonical client {canonical.node_id}; "
            f"entry={entry_signature!r}, client={expected_signature!r}"
        )
    port_changes: List[Dict[str, Any]] = []
    for index, (name, type_id, port) in enumerate(client_signature):
        _, _, existing_port = _entry_param(entry_params[index], f"signal {signal_name!r} parameter {index + 1}")
        if existing_port == port:
            continue
        if _set_entry_param_port(entry, index, port, f"signal {signal_name!r}"):
            port_changes.append({"param_name": name, "type_id": int(type_id), "client_port": int(port)})

    server_builtin_changed = _update_message_field(
        roles["server"].node_def,
        4,
        f"signal {signal_name!r} server definition metadata",
        lambda metadata: _set_message_field(metadata, 2, _SERVER_BUILTIN_META),
    )
    if server_builtin_changed:
        roles["server"].commit()

    actions: Dict[str, Any] = {"canonical_client_node_def_id": int(canonical.node_id)}
    changed = False
    if entry_meta_changed:
        actions["entry_client_meta"] = True
        changed = True
    if port_changes:
        actions["client_ports"] = port_changes
        changed = True
    if role_binding_changes:
        actions["role_bindings"] = role_binding_changes
        changed = True
    if client_binding_changes:
        actions["client_bindings"] = client_binding_changes
        changed = True
    if server_builtin_changed:
        actions["server_builtin"] = 2000
        changed = True
    if not changed:
        return None
    return {"signal_name": signal_name, **actions}


def repair_signal_registry(entries: List[Any], node_defs: List[Any]) -> List[Dict[str, Any]]:
    """Repair the signal entry/client/role registry in-place without allocating IDs or ports."""
    if not isinstance(entries, list):
        raise TypeError("signal registry repair: entries must be a list")
    if not isinstance(node_defs, list):
        raise TypeError("signal registry repair: node_defs must be a list")
    node_refs = _node_refs(node_defs)
    repairs: List[Dict[str, Any]] = []
    for index, raw_entry in enumerate(list(entries)):
        if isinstance(raw_entry, MutableMapping):
            entry = raw_entry
        elif isinstance(raw_entry, str) and raw_entry.startswith(_BINARY_PREFIX):
            entry = _binary_map(raw_entry, f"entries[{index}]") or {}
            entries[index] = entry
        else:
            continue
        repair = _repair_one_entry(entry, node_refs)
        if repair is not None:
            repairs.append(repair)
    return repairs
