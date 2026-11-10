from __future__ import annotations

import copy
import json
from collections import defaultdict
from collections.abc import Mapping, MutableMapping, MutableSequence
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ugc_file_tools.gil_dump_codec.protobuf_like_bridge import (
    binary_data_text_to_numeric_message,
    numeric_message_to_binary_data_text,
)
from ugc_file_tools.signal_writeback.registry_repair import repair_signal_registry


_BINARY_PREFIX = "<binary_data>"
_CLIENT_TITLE = "发送客户端脚本信号"
_CLIENT_META = {"1": 10001, "2": 20000, "3": 22001}
_PORT_FIELDS = (100, 101, 102, 103, 106)
_ROLE_BINDINGS = ((1, 101), (2, 102), (7, 101))


def _field_key(value: Mapping[str, Any], number: int) -> Optional[str]:
    key = str(int(number))
    if key in value:
        return key
    field_key = f"field_{key}"
    if field_key in value:
        return field_key
    return None


def _get(value: Mapping[str, Any], number: int) -> Any:
    key = _field_key(value, number)
    return value.get(key) if key is not None else None


def _set(value: MutableMapping[str, Any], number: int, item: Any) -> None:
    key = _field_key(value, number) or str(int(number))
    value[key] = item


def _message(value: Any, context: str, *, optional: bool = False) -> Optional[Dict[str, Any]]:
    if value is None and optional:
        return None
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str) and value.startswith(_BINARY_PREFIX):
        try:
            decoded = binary_data_text_to_numeric_message(value)
        except Exception as exc:
            raise ValueError(f"client identity normalization: cannot decode {context}: {exc}") from exc
        if isinstance(decoded, Mapping):
            return {str(key): item for key, item in decoded.items()}
    if optional:
        return None
    raise ValueError(f"client identity normalization: {context} must be a message or {_BINARY_PREFIX} text")


def _encode_like(original: Any, message: Mapping[str, Any]) -> Any:
    if isinstance(original, str) and original.startswith(_BINARY_PREFIX):
        return numeric_message_to_binary_data_text(message)
    return copy.deepcopy(dict(message))


def _int_value(value: Any) -> Optional[int]:
    if type(value) is int:
        return int(value)
    if isinstance(value, Mapping):
        for key in ("int", "field_1", "1"):
            child = value.get(key)
            if type(child) is int:
                return int(child)
            if isinstance(child, Mapping):
                nested = _int_value(child)
                if nested is not None:
                    return nested
        for key in ("message",):
            child = value.get(key)
            if isinstance(child, Mapping):
                found = [_int_value(item) for item in child.values()]
                found = [item for item in found if item is not None]
                if len(found) == 1:
                    return int(found[0])
    return None


def _replace_int(value: MutableMapping[str, Any], number: int, expected: int, context: str) -> bool:
    key = _field_key(value, number) or str(int(number))
    current = value.get(key)
    if type(current) is int:
        if current == int(expected):
            return False
        value[key] = int(expected)
        return True
    if isinstance(current, Mapping):
        replacement = copy.deepcopy(dict(current))
        changed = _replace_nested_scalar(replacement, int(current.get("int", -1)), int(expected))
        if changed:
            value[key] = replacement
            return True
    if current is None and key not in value:
        value[key] = int(expected)
        return True
    raise ValueError(f"client identity normalization: {context} is not a writable integer")


def _replace_nested_scalar(value: MutableMapping[str, Any], old: int, new: int) -> bool:
    if type(value.get("int")) is int:
        if value["int"] == int(new):
            return False
        value["int"] = int(new)
        return True
    for key in ("field_1", "1"):
        child = value.get(key)
        if type(child) is int:
            if child == int(new):
                return False
            value[key] = int(new)
            return True
        if isinstance(child, MutableMapping) and _replace_nested_scalar(child, old, new):
            return True
    return False


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


def _normalized_field_name(key: Any) -> str:
    text = str(key)
    if text.startswith("field_") and text[6:].isdigit():
        return text[6:]
    return text


def _deep_normalized(value: Any, context: str) -> Any:
    if isinstance(value, str) and value.startswith(_BINARY_PREFIX):
        decoded = _message(value, context)
        return _deep_normalized(decoded, context)
    if isinstance(value, Mapping):
        return {
            _normalized_field_name(key): _deep_normalized(item, context)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_deep_normalized(item, context) for item in value]
    return value


def _message_id(value: Any, context: str) -> Optional[int]:
    if value is None:
        return None
    metadata = _message(value, context, optional=True)
    return _int_value(_get(metadata, 5)) if metadata is not None else None


def _set_message_field(parent: MutableMapping[str, Any], number: int, message: Mapping[str, Any]) -> bool:
    key = _field_key(parent, number) or str(int(number))
    original = parent.get(key)
    updated = _encode_like(original, message)
    if original == updated:
        return False
    parent[key] = updated
    return True


@dataclass
class _NodeDefRef:
    node_id: int
    title: str
    node_def: Dict[str, Any]
    wrapper: MutableMapping[str, Any]
    wrapper_key: str
    outer_binary: bool

    def commit(self) -> None:
        if self.outer_binary:
            self.wrapper[self.wrapper_key] = numeric_message_to_binary_data_text(self.node_def)


def _node_def_id(node_def: Mapping[str, Any], context: str) -> Optional[int]:
    metadata = _message(_get(node_def, 4), f"{context} metadata", optional=True)
    if metadata is None:
        return None
    identity = _message(_get(metadata, 1), f"{context} identity metadata", optional=True)
    return _int_value(_get(identity, 5)) if identity is not None else None


def _node_defs(section10: Mapping[str, Any]) -> Tuple[MutableSequence[Any], Dict[int, _NodeDefRef]]:
    values = _get(section10, 2)
    if not isinstance(values, MutableSequence):
        raise ValueError("client identity normalization: section 10/2 node definitions must be a list")
    refs: Dict[int, _NodeDefRef] = {}
    for index, wrapper_value in enumerate(values):
        if not isinstance(wrapper_value, MutableMapping):
            continue
        if _field_key(wrapper_value, 1) is None:
            wrapper = wrapper_value
            wrapper_key = ""
            raw_node_def = wrapper_value
        else:
            wrapper = wrapper_value
            wrapper_key = _field_key(wrapper_value, 1) or "1"
            raw_node_def = wrapper_value.get(wrapper_key)
        outer_binary = isinstance(raw_node_def, str) and raw_node_def.startswith(_BINARY_PREFIX)
        if outer_binary:
            node_def = _message(raw_node_def, f"node definition {index}") or {}
        elif isinstance(raw_node_def, MutableMapping):
            node_def = raw_node_def
        else:
            continue
        node_id = _node_def_id(node_def, f"node definition {index}")
        if not isinstance(node_id, int):
            continue
        if int(node_id) in refs:
            raise ValueError(f"client identity normalization: duplicate node definition ID {node_id}")
        title = str(_text_value(_get(node_def, 200)) or "").strip()
        refs[int(node_id)] = _NodeDefRef(
            node_id=int(node_id),
            title=title,
            node_def=node_def,
            wrapper=wrapper,
            wrapper_key=wrapper_key,
            outer_binary=outer_binary,
        )
    return values, refs


def _client_name(node_def: Mapping[str, Any], context: str) -> str:
    outer = _message(_get(node_def, 107), f"{context} binding metadata", optional=True)
    binding = _message(_get(outer, 108), f"{context} client binding", optional=True) if outer else None
    return str(_text_value(_get(binding, 1)) or "").strip() if binding else ""


def _binding_client_id(node_def: Mapping[str, Any], binding_field: int, context: str) -> Optional[int]:
    outer = _message(_get(node_def, 107), f"{context} binding metadata", optional=True)
    binding = _message(_get(outer, binding_field), f"{context} role binding", optional=True) if outer else None
    return _message_id(_get(binding, 4), f"{context} client peer") if binding else None


def _entry_signal_name(entry: Mapping[str, Any]) -> str:
    return str(_text_value(_get(entry, 3)) or "").strip()


def _entry_role_ids(entry: Mapping[str, Any], context: str) -> Dict[str, Optional[int]]:
    return {
        "send": _message_id(_get(entry, 1), f"{context} send metadata"),
        "listen": _message_id(_get(entry, 2), f"{context} listen metadata"),
        "server": _message_id(_get(entry, 7), f"{context} server metadata"),
    }


def _valid_entry_client_id(
    entry: Mapping[str, Any],
    clients_by_id: Mapping[int, _NodeDefRef],
    signal_name: str,
) -> Optional[int]:
    raw = _get(entry, 9)
    if raw is None:
        return None
    metadata = _message(raw, f"signal {signal_name!r} entry field 9", optional=True)
    if metadata is None:
        return None
    client_id = _message_id(metadata, f"signal {signal_name!r} entry field 9")
    if client_id is None or any(_int_value(_get(metadata, number)) != expected for number, expected in _CLIENT_META.items()):
        return None
    client = clients_by_id.get(int(client_id))
    return int(client_id) if client is not None and client.title == _CLIENT_TITLE and _client_name(client.node_def, f"client {client_id}") == signal_name else None


def _canonical_client_id(
    entry: Mapping[str, Any],
    signal_name: str,
    clients: Sequence[_NodeDefRef],
    all_refs: Mapping[int, _NodeDefRef],
) -> int:
    clients_by_id = {ref.node_id: ref for ref in clients}
    entry9_id = _valid_entry_client_id(entry, clients_by_id, signal_name)
    if entry9_id is not None:
        return int(entry9_id)

    role_ids = _entry_role_ids(entry, f"signal {signal_name!r}")
    peer_candidates: set[int] = set()
    for role_name, binding_field in _ROLE_BINDINGS:
        role_id = role_ids[{1: "send", 2: "listen", 7: "server"}[role_name]]
        role = all_refs.get(int(role_id)) if isinstance(role_id, int) else None
        if role is None:
            continue
        peer_id = _binding_client_id(role.node_def, binding_field, f"signal {signal_name!r} {role_name} role")
        peer = clients_by_id.get(int(peer_id)) if isinstance(peer_id, int) else None
        if peer is not None and _client_name(peer.node_def, f"client {peer.node_id}") == signal_name:
            peer_candidates.add(int(peer.node_id))
    if peer_candidates:
        return min(peer_candidates)
    if not clients:
        raise ValueError(f"client identity normalization: signal {signal_name!r} has no client-send definition")
    return min(int(ref.node_id) for ref in clients)


def _port_items(value: Any, context: str) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, (Mapping, str)):
        if isinstance(value, str) and not value.startswith(_BINARY_PREFIX):
            raise ValueError(f"client identity normalization: {context} is not a port declaration")
        return [value]
    raise ValueError(f"client identity normalization: {context} is not a port declaration list")


def _port_key(record: Any, context: str) -> str:
    message = _message(record, context)
    semantic = {
        _normalized_field_name(key): _deep_normalized(item, f"{context}.{key}")
        for key, item in message.items()
        if _normalized_field_name(key) != "8"
    }
    return json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _port_number(record: Any, context: str) -> Optional[int]:
    message = _message(record, context)
    value = _get(message, 8)
    return _int_value(value)


def _alias_port_map(alias: _NodeDefRef, canonical: _NodeDefRef, signal_name: str) -> Dict[int, int]:
    result: Dict[int, int] = {}
    for group in _PORT_FIELDS:
        alias_items = _port_items(_get(alias.node_def, group), f"client {alias.node_id} field {group}")
        canonical_items = _port_items(_get(canonical.node_def, group), f"client {canonical.node_id} field {group}")
        if len(alias_items) != len(canonical_items):
            raise ValueError(
                f"client identity normalization: duplicate clients for {signal_name!r} have incompatible field {group} declaration counts"
            )
        alias_by_key: Dict[str, List[Any]] = defaultdict(list)
        canonical_by_key: Dict[str, List[Any]] = defaultdict(list)
        for index, record in enumerate(alias_items):
            alias_by_key[_port_key(record, f"client {alias.node_id} field {group}[{index}]")].append(record)
        for index, record in enumerate(canonical_items):
            canonical_by_key[_port_key(record, f"client {canonical.node_id} field {group}[{index}]")].append(record)
        if set(alias_by_key) != set(canonical_by_key):
            raise ValueError(
                f"client identity normalization: duplicate clients for {signal_name!r} have unmatched field {group} kind/index declarations"
            )
        for key, old_records in alias_by_key.items():
            new_records = canonical_by_key[key]
            if len(old_records) != len(new_records):
                raise ValueError(
                    f"client identity normalization: duplicate clients for {signal_name!r} have ambiguous field {group} declarations"
                )
            for ordinal, (old_record, new_record) in enumerate(zip(old_records, new_records)):
                old_port = _port_number(old_record, f"client {alias.node_id} field {group} item {ordinal + 1}")
                new_port = _port_number(new_record, f"client {canonical.node_id} field {group} item {ordinal + 1}")
                if old_port is None or new_port is None:
                    raise ValueError(
                        f"client identity normalization: duplicate clients for {signal_name!r} have a field {group} declaration without a port index"
                    )
                previous = result.get(int(old_port))
                if previous is not None and previous != int(new_port):
                    raise ValueError(
                        f"client identity normalization: alias client {alias.node_id} port {old_port} maps ambiguously"
                    )
                result[int(old_port)] = int(new_port)
    return result


def _set_entry_client_meta(entry: MutableMapping[str, Any], canonical_id: int) -> bool:
    key = _field_key(entry, 9) or "9"
    original = entry.get(key)
    metadata = _message(original, "signal entry field 9", optional=True) or {}
    updated = dict(metadata)
    for field, value in {**_CLIENT_META, "5": int(canonical_id)}.items():
        _set(updated, int(field), int(value))
    encoded = _encode_like(original, updated)
    if original == encoded:
        return False
    entry[key] = encoded
    return True


def _set_client_index(ref: _NodeDefRef, index: int) -> bool:
    metadata_raw = _get(ref.node_def, 4)
    metadata = _message(metadata_raw, f"client {ref.node_id} field 4 metadata")
    if metadata is None:
        raise ValueError(f"client identity normalization: client {ref.node_id} lacks metadata")
    changed = _replace_int(metadata, 5, int(index), f"client {ref.node_id} metadata field 4/5")
    if changed:
        _set_message_field(ref.node_def, 4, metadata)
    return changed


def _clear_index_slot(value: Any, context: str) -> Any:
    if isinstance(value, str) and value.startswith(_BINARY_PREFIX):
        return numeric_message_to_binary_data_text({})
    if isinstance(value, Mapping):
        return {}
    raise ValueError(f"client identity normalization: {context} cannot be replaced with an empty metadata message")


def _update_node_metadata(node: MutableMapping[str, Any], field_number: int, node_id: int, context: str) -> bool:
    key = _field_key(node, field_number)
    if key is None:
        return False
    original = node.get(key)
    metadata = _message(original, f"{context} field {field_number}")
    current_id = _int_value(_get(metadata, 5))
    if current_id == int(node_id):
        return False
    if current_id is None:
        raise ValueError(f"client identity normalization: {context} field {field_number} has no definition ID")
    _replace_int(metadata, 5, int(node_id), f"{context} field {field_number}/5")
    node[key] = _encode_like(original, metadata)
    return True


def _set_pin_cpi(pin: MutableMapping[str, Any], expected: int, context: str) -> bool:
    current = _int_value(_get(pin, 7))
    if current == int(expected):
        return False
    if current is None:
        raise ValueError(f"client identity normalization: {context} compositePinIndex is not an integer")
    _replace_int(pin, 7, int(expected), f"{context} compositePinIndex")
    return True


def _pin_source_ref_id(pin: Mapping[str, Any], index_field: int, context: str) -> Optional[int]:
    index = _message(_get(pin, index_field), f"{context} Index field {index_field}", optional=True)
    if index is None:
        return None
    source_ref = _message(_get(index, 100), f"{context} Index field {index_field}/100", optional=True)
    return _int_value(_get(source_ref, 1)) if source_ref is not None else None


def _update_pin_source_ref(
    pin: MutableMapping[str, Any],
    index_field: int,
    old_id: int,
    new_id: int,
    context: str,
) -> bool:
    index_key = _field_key(pin, index_field)
    if index_key is None:
        return False
    original_index = pin.get(index_key)
    index = _message(original_index, f"{context} Index field {index_field}", optional=True)
    if index is None:
        return False
    source_key = _field_key(index, 100)
    if source_key is None:
        return False
    original_source = index.get(source_key)
    source_ref = _message(original_source, f"{context} Index field {index_field}/100", optional=True)
    if source_ref is None:
        return False
    current_id = _int_value(_get(source_ref, 1))
    if current_id != int(old_id):
        return False
    _replace_int(source_ref, 1, int(new_id), f"{context} Index field {index_field}/100/1")
    _set_message_field(index, 100, source_ref)
    pin[index_key] = _encode_like(original_index, index)
    return True


def _pin_alias_refs(pin: Mapping[str, Any], alias_ids: set[int], context: str) -> Dict[int, int]:
    result: Dict[int, int] = {}
    for index_field in (1, 2):
        ref_id = _pin_source_ref_id(pin, index_field, context)
        if isinstance(ref_id, int) and ref_id in alias_ids:
            result[index_field] = int(ref_id)
    return result


def _graph_nodes(graph: Mapping[str, Any], context: str) -> Optional[Tuple[List[Any], bool]]:
    nodes = _get(graph, 3)
    if nodes is None:
        return None
    if isinstance(nodes, list):
        return list(nodes), True
    if isinstance(nodes, Mapping) or (isinstance(nodes, str) and nodes.startswith(_BINARY_PREFIX)):
        return [nodes], False
    raise ValueError(f"client identity normalization: {context} node field 3 is not a node or node list")


def _node_pin_values(node: Mapping[str, Any], context: str) -> Tuple[List[Any], bool]:
    pins = _get(node, 4)
    if pins is None:
        return [], True
    if isinstance(pins, list):
        return list(pins), True
    if isinstance(pins, Mapping) or (isinstance(pins, str) and pins.startswith(_BINARY_PREFIX)):
        return [pins], False
    raise ValueError(f"client identity normalization: {context} field 4 is not a pin collection")


def _normalize_graph_instances(
    section10: MutableMapping[str, Any],
    alias_to_canonical: Mapping[int, int],
    alias_port_maps: Mapping[int, Mapping[int, int]],
    client_indices: Mapping[int, int],
) -> Tuple[int, int, int, int]:
    graphs = _get(section10, 1)
    if not isinstance(graphs, MutableSequence):
        raise ValueError("client identity normalization: section 10/1 graphs must be a list")
    changed_node_count = 0
    rewritten_refs = 0
    rewritten_pin_refs = 0
    rewritten_cpis = 0

    for group_index, wrapper in enumerate(graphs):
        if not isinstance(wrapper, MutableMapping):
            continue
        graph_key = _field_key(wrapper, 1)
        if graph_key is None:
            continue
        original_group_value = wrapper.get(graph_key)
        group_is_list = isinstance(original_group_value, list)
        graph_values = list(original_group_value) if group_is_list else [original_group_value]
        group_changed = False

        for graph_index, original_graph in enumerate(graph_values):
            graph_binary = isinstance(original_graph, str) and original_graph.startswith(_BINARY_PREFIX)
            graph = _message(original_graph, f"graph group {group_index} item {graph_index}") if graph_binary else original_graph
            if not isinstance(graph, MutableMapping):
                continue
            node_values = _graph_nodes(graph, f"graph group {group_index} item {graph_index}")
            if node_values is None:
                continue
            nodes, nodes_are_list = node_values
            graph_changed = False
            graph_name = str(_text_value(_get(graph, 2)) or f"graph[{group_index}][{graph_index}]")

            for node_index, raw_node in enumerate(nodes):
                node_binary = isinstance(raw_node, str) and raw_node.startswith(_BINARY_PREFIX)
                node = _message(raw_node, f"graph {graph_name!r} node {node_index}") if node_binary else raw_node
                if not isinstance(node, MutableMapping):
                    continue
                context = f"graph {graph_name!r} node {node_index}"
                ref_ids: Dict[int, int] = {}
                for field_number in (2, 3):
                    raw = _get(node, field_number)
                    if raw is None:
                        continue
                    metadata = _message(raw, f"{context} field {field_number}")
                    node_id = _int_value(_get(metadata, 5))
                    if isinstance(node_id, int):
                        ref_ids[int(field_number)] = int(node_id)

                pins_list, pins_is_list = _node_pin_values(node, context)
                pin_messages: List[Optional[Dict[str, Any]]] = []
                pin_aliases: List[Dict[int, int]] = []
                for pin_index, raw_pin in enumerate(pins_list):
                    if not isinstance(raw_pin, (Mapping, str)):
                        pin_messages.append(None)
                        pin_aliases.append({})
                        continue
                    pin = _message(raw_pin, f"{context} pin {pin_index}", optional=True)
                    pin_messages.append(pin)
                    pin_aliases.append(
                        _pin_alias_refs(pin, set(alias_to_canonical), f"{context} pin {pin_index}")
                        if pin is not None
                        else {}
                    )

                node_aliases = {node_id for node_id in ref_ids.values() if node_id in alias_to_canonical}
                aliases_here = set(node_aliases)
                aliases_here.update(alias_id for refs in pin_aliases for alias_id in refs.values())
                if len(aliases_here) > 1:
                    raise ValueError(f"client identity normalization: {context} references multiple client aliases")
                current_client_ids = {
                    alias_to_canonical.get(node_id, node_id)
                    for node_id in ref_ids.values()
                    if alias_to_canonical.get(node_id, node_id) in client_indices
                }
                if len(current_client_ids) > 1:
                    raise ValueError(f"client identity normalization: {context} has conflicting client definition references")

                node_changed = False
                used_alias: Optional[int] = next(iter(aliases_here)) if aliases_here else None
                if node_aliases:
                    canonical_id = int(alias_to_canonical[next(iter(node_aliases))])
                    for field_number, node_id in ref_ids.items():
                        if node_id in node_aliases and _update_node_metadata(node, field_number, canonical_id, context):
                            node_changed = True
                            rewritten_refs += 1

                if current_client_ids:
                    client_id = next(iter(current_client_ids))
                    desired_index = int(client_indices[client_id])
                    field9_key = _field_key(node, 9)
                    current_index = _int_value(node.get(field9_key)) if field9_key is not None else None
                    if current_index != desired_index:
                        if field9_key is not None and type(node.get(field9_key)) is not int:
                            raise ValueError(f"client identity normalization: {context} field 9 is not an integer")
                        _set(node, 9, desired_index)
                        node_changed = True

                if used_alias is not None:
                    port_map = alias_port_maps.get(used_alias)
                    if not isinstance(port_map, Mapping) or not port_map:
                        raise ValueError(f"client identity normalization: no declaration-slot map for alias client {used_alias}")
                    migrated_pins: List[Any] = []
                    for pin_index, raw_pin in enumerate(pins_list):
                        pin = pin_messages[pin_index]
                        if pin is None:
                            migrated_pins.append(raw_pin)
                            continue
                        pin_changed = False
                        refs_for_pin = pin_aliases[pin_index]
                        for index_field, alias_id in refs_for_pin.items():
                            if alias_id == used_alias and _update_pin_source_ref(
                                pin,
                                index_field,
                                used_alias,
                                int(alias_to_canonical[used_alias]),
                                f"{context} pin {pin_index}",
                            ):
                                pin_changed = True
                                rewritten_pin_refs += 1

                        should_map_cpi = bool(node_aliases) or used_alias in refs_for_pin.values()
                        if should_map_cpi:
                            old_cpi = _int_value(_get(pin, 7))
                            if old_cpi is not None:
                                if int(old_cpi) not in port_map:
                                    raise ValueError(
                                        f"client identity normalization: {context} pin {pin_index} CPI {old_cpi} has no alias declaration match"
                                    )
                                new_cpi = int(port_map[int(old_cpi)])
                                if new_cpi != int(old_cpi):
                                    if _set_pin_cpi(pin, new_cpi, f"{context} pin {pin_index}"):
                                        pin_changed = True
                                        rewritten_cpis += 1

                        if pin_changed:
                            migrated_pins.append(_encode_like(raw_pin, pin))
                            node_changed = True
                        else:
                            migrated_pins.append(raw_pin)
                    if pins_is_list:
                        if migrated_pins != pins_list:
                            _set(node, 4, migrated_pins)
                    elif migrated_pins:
                        _set(node, 4, migrated_pins[0])

                if node_changed:
                    if node_binary:
                        nodes[node_index] = numeric_message_to_binary_data_text(node)
                    if nodes_are_list:
                        _set(graph, 3, nodes)
                    else:
                        _set(graph, 3, nodes[0])
                    changed_node_count += 1
                    graph_changed = True

            if graph_binary and graph_changed:
                graph_values[graph_index] = numeric_message_to_binary_data_text(graph)
                group_changed = True

        if group_changed:
            wrapper[graph_key] = graph_values if group_is_list else graph_values[0]

    return changed_node_count, rewritten_refs, rewritten_pin_refs, rewritten_cpis


def _assert_no_graph_alias_refs(section10: Mapping[str, Any], alias_ids: set[int]) -> None:
    if not alias_ids:
        return
    graphs = _get(section10, 1)
    if not isinstance(graphs, list):
        raise ValueError("client identity normalization: section 10/1 graphs must be a list")
    for group_index, wrapper in enumerate(graphs):
        if not isinstance(wrapper, Mapping):
            continue
        group_value = _get(wrapper, 1)
        graph_values = list(group_value) if isinstance(group_value, list) else [group_value]
        for graph_index, raw_graph in enumerate(graph_values):
            graph = _message(raw_graph, f"graph group {group_index} item {graph_index}", optional=True)
            if graph is None:
                continue
            node_values = _graph_nodes(graph, f"graph group {group_index} item {graph_index}")
            if node_values is None:
                continue
            nodes, _ = node_values
            for node_index, raw_node in enumerate(nodes):
                node = _message(raw_node, f"graph group {group_index} item {graph_index} node {node_index}", optional=True)
                if node is None:
                    continue
                context = f"graph group {group_index} item {graph_index} node {node_index}"
                for field_number in (2, 3):
                    metadata = _message(_get(node, field_number), f"{context} field {field_number}", optional=True)
                    node_id = _int_value(_get(metadata, 5)) if metadata is not None else None
                    if node_id in alias_ids:
                        raise ValueError(f"client identity normalization: unresolved alias {node_id} remains in {context} field {field_number}")
                pins, _ = _node_pin_values(node, context)
                for pin_index, raw_pin in enumerate(pins):
                    pin = _message(raw_pin, f"{context} pin {pin_index}", optional=True)
                    if pin is None:
                        continue
                    for index_field in (1, 2):
                        ref_id = _pin_source_ref_id(pin, index_field, f"{context} pin {pin_index}")
                        if ref_id in alias_ids:
                            raise ValueError(
                                f"client identity normalization: unresolved alias {ref_id} remains in {context} pin {pin_index} Index field {index_field}"
                            )


def normalize_client_identities(payload_root: dict) -> dict:
    """Merge same-name client signal definitions and migrate graph references in place.

    The helper deliberately runs only when its caller explicitly invokes it. It preserves
    definition IDs and graph ordinals, updates client-specific indices from signal entry
    indices, and leaves alias index-table positions as empty messages.
    """
    if not isinstance(payload_root, MutableMapping):
        raise ValueError("client identity normalization: payload_root must be a mapping")
    section10 = _get(payload_root, 10)
    if not isinstance(section10, MutableMapping):
        raise ValueError("client identity normalization: payload_root field 10 must be a mapping")
    node_defs, all_refs = _node_defs(section10)
    section5 = _message(_get(section10, 5), "section 10/5")
    if section5 is None:
        raise ValueError("client identity normalization: section 10/5 is missing")
    entries = _get(section5, 3)
    index_table = _get(section5, 2)
    if not isinstance(entries, list):
        raise ValueError("client identity normalization: section 10/5/3 signal entries must be a list")
    if not isinstance(index_table, MutableSequence):
        raise ValueError("client identity normalization: section 10/5/2 index table must be a list")

    entries_by_name: Dict[str, MutableMapping[str, Any]] = {}
    target_index_by_name: Dict[str, int] = {}
    used_target_indices: Dict[int, str] = {}
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, MutableMapping):
            continue
        name = _entry_signal_name(entry)
        if not name:
            continue
        if name in entries_by_name:
            raise ValueError(f"client identity normalization: duplicate signal entry {name!r}")
        signal_index = _int_value(_get(entry, 6))
        if signal_index is None:
            raise ValueError(f"client identity normalization: signal {name!r} entry {entry_index} has no integer field 6")
        target_index = int(signal_index) + 1
        previous_name = used_target_indices.get(target_index)
        if previous_name is not None:
            raise ValueError(
                f"client identity normalization: client index collision {target_index} between {previous_name!r} and {name!r}"
            )
        used_target_indices[target_index] = name
        entries_by_name[name] = entry
        target_index_by_name[name] = target_index

    clients_by_name: Dict[str, List[_NodeDefRef]] = defaultdict(list)
    for ref in all_refs.values():
        if ref.title != _CLIENT_TITLE:
            continue
        name = _client_name(ref.node_def, f"client {ref.node_id}")
        if name:
            clients_by_name[name].append(ref)

    canonical_by_name: Dict[str, _NodeDefRef] = {}
    alias_to_canonical: Dict[int, int] = {}
    alias_port_maps: Dict[int, Dict[int, int]] = {}
    alias_refs: Dict[int, _NodeDefRef] = {}
    index_actions: List[Dict[str, int | str]] = []
    changed_entry_count = 0

    for signal_name, entry in entries_by_name.items():
        clients = sorted(clients_by_name.get(signal_name, []), key=lambda ref: int(ref.node_id))
        if not clients:
            raise ValueError(f"client identity normalization: signal {signal_name!r} has no client-send definition")
        canonical_id = _canonical_client_id(entry, signal_name, clients, all_refs)
        canonical = next(ref for ref in clients if ref.node_id == canonical_id)
        canonical_by_name[signal_name] = canonical
        target_index = int(target_index_by_name[signal_name])

        for alias in clients:
            if alias.node_id == canonical.node_id:
                continue
            alias_to_canonical[int(alias.node_id)] = int(canonical.node_id)
            alias_refs[int(alias.node_id)] = alias
            alias_port_maps[int(alias.node_id)] = _alias_port_map(alias, canonical, signal_name)

        old_index = _int_value(_get(_message(_get(canonical.node_def, 4), f"client {canonical.node_id} metadata"), 5))
        if old_index is None:
            raise ValueError(f"client identity normalization: canonical client {canonical.node_id} has no field 4/5 index")
        if int(old_index) != target_index:
            index_actions.append(
                {
                    "signal_name": signal_name,
                    "client_node_def_id": int(canonical.node_id),
                    "old_index": int(old_index),
                    "new_index": target_index,
                }
            )

        if _set_entry_client_meta(entry, int(canonical.node_id)):
            changed_entry_count += 1
        if _set_client_index(canonical, target_index):
            canonical.commit()

    managed_client_ids = {ref.node_id for ref in canonical_by_name.values()}
    unmanaged_client_ids = {
        ref.node_id
        for name, refs in clients_by_name.items()
        if name not in entries_by_name
        for ref in refs
    }
    target_indices = {target_index: name for name, target_index in target_index_by_name.items()}
    for node_id in unmanaged_client_ids:
        ref = all_refs[node_id]
        existing_index = _int_value(_get(_message(_get(ref.node_def, 4), f"client {node_id} metadata"), 5))
        if existing_index in target_indices:
            raise ValueError(
                f"client identity normalization: target client index {existing_index} for {target_indices[existing_index]!r} collides with unmanaged client {node_id}"
            )
    client_indices = {
        ref.node_id: target_index_by_name[name]
        for name, ref in canonical_by_name.items()
    }
    for name, refs in clients_by_name.items():
        if name not in entries_by_name:
            for ref in refs:
                index = _int_value(_get(_message(_get(ref.node_def, 4), f"client {ref.node_id} metadata"), 5))
                if index is not None:
                    client_indices[ref.node_id] = int(index)
    if len(set(client_indices.values())) != len(client_indices):
        duplicate_indices: Dict[int, List[int]] = defaultdict(list)
        for node_id, index in client_indices.items():
            duplicate_indices[int(index)].append(int(node_id))
        collisions = {index: ids for index, ids in duplicate_indices.items() if len(ids) > 1}
        raise ValueError(f"client identity normalization: client index collision after normalization: {collisions}")

    node_changed_count, rewritten_refs, rewritten_pin_refs, rewritten_cpis = _normalize_graph_instances(
        section10,
        alias_to_canonical,
        alias_port_maps,
        client_indices,
    )
    _assert_no_graph_alias_refs(section10, set(alias_to_canonical))

    if alias_to_canonical:
        cleared_slots: List[int] = []
        cleared_alias_ids: set[int] = set()
        for slot_index, item in enumerate(index_table):
            node_id = _message_id(item, f"section 10/5/2 slot {slot_index}")
            if isinstance(node_id, int) and int(node_id) in alias_to_canonical:
                index_table[slot_index] = _clear_index_slot(item, f"section 10/5/2 slot {slot_index}")
                cleared_slots.append(int(slot_index))
                cleared_alias_ids.add(int(node_id))
        for alias_id in alias_to_canonical:
            if alias_id not in cleared_alias_ids:
                raise ValueError(f"client identity normalization: alias client {alias_id} has no slot in section 10/5/2")
    else:
        cleared_slots = []

    if alias_to_canonical:
        kept_defs: List[Any] = []
        for wrapper in node_defs:
            ref_id: Optional[int] = None
            if isinstance(wrapper, Mapping):
                raw_def = _get(wrapper, 1)
                raw_node_def = _message(raw_def, "node definition wrapper", optional=True)
                if raw_node_def is not None:
                    ref_id = _node_def_id(raw_node_def, "node definition wrapper")
            if isinstance(ref_id, int) and int(ref_id) in alias_to_canonical:
                continue
            kept_defs.append(wrapper)
        node_defs[:] = kept_defs

    registry_repairs = repair_signal_registry(entries, node_defs)

    # section 10/5 was decoded only for inspection when it is itself binary.
    # The actual shallow dump uses a mutable mapping; commit field 2 if that changes.
    section5_key = _field_key(section10, 5) or "5"
    original_section5 = section10.get(section5_key)
    if isinstance(original_section5, str) and original_section5.startswith(_BINARY_PREFIX):
        updated_section5 = dict(section5)
        _set(updated_section5, 2, index_table)
        _set(updated_section5, 3, entries)
        section10[section5_key] = numeric_message_to_binary_data_text(updated_section5)
    elif isinstance(original_section5, MutableMapping):
        _set(original_section5, 2, index_table)
        _set(original_section5, 3, entries)

    return {
        "merged_client_ids": {str(alias): int(canonical) for alias, canonical in sorted(alias_to_canonical.items())},
        "client_indices": index_actions,
        "changed_entry_count": int(changed_entry_count),
        "changed_node_count": int(node_changed_count),
        "rewritten_node_definition_refs": int(rewritten_refs),
        "rewritten_pin_source_refs": int(rewritten_pin_refs),
        "rewritten_pin_composite_indices": int(rewritten_cpis),
        "removed_client_definition_count": int(len(alias_to_canonical)),
        "cleared_index_slots": cleared_slots,
        "registry_repairs": registry_repairs,
    }
