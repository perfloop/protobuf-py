# Copyright (c) 2025-2026 Buf Technologies, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Behavior required if merge stores a lazy, independently mutable message view."""

from __future__ import annotations

import copy
import pickle
import threading
from typing import cast

from protobuf import Oneof, merge_from
from protobuf._wire import BinaryWriter, WireType
from tests.gen.delimited_encoding_pb import DelimitedEncoding
from tests.gen.messages_pb import Recursive


def _message(value: int) -> DelimitedEncoding.Msg:
    return DelimitedEncoding.Msg(
        value=value, child=DelimitedEncoding.Msg(value=value + 1)
    )


def _required_message(message: DelimitedEncoding.Msg | None) -> DelimitedEncoding.Msg:
    assert message is not None
    return message


def _child(message: DelimitedEncoding.Msg) -> DelimitedEncoding.Msg:
    assert message.child is not None
    return message.child


def _choice_message(choice: object) -> DelimitedEncoding.Msg:
    assert isinstance(choice, Oneof)
    assert choice.field == "choice_message"
    return cast("DelimitedEncoding.Msg", choice.value)


def _source() -> DelimitedEncoding:
    singular = _message(10)
    singular._get_or_init_unknown_fields()[99] = [b"\x98\x06\x01"]
    return DelimitedEncoding(
        name="source",
        singular=singular,
        repeated=[_message(20)],
        message_map={"source": _message(30)},
        choice=Oneof(field="choice_message", value=_message(40)),
    )


def test_merge_view_preserves_identity_isolation_and_wire_output() -> None:
    source = _source()
    target = DelimitedEncoding(
        singular=_message(1),
        repeated=[_message(2)],
        message_map={"existing": _message(3)},
    )
    target_singular = target.singular
    target_repeated = target.repeated
    target_map = target.message_map

    merge_from(target, source)

    assert target.singular is target_singular
    assert target.repeated is target_repeated
    assert target.message_map is target_map
    assert target.singular is not source.singular
    assert target.repeated[-1] is not source.repeated[0]
    assert target.message_map["source"] is not source.message_map["source"]
    target_choice = _choice_message(target.choice)
    source_choice = _choice_message(source.choice)
    assert target_choice is not source_choice
    target_wire = target.to_binary()

    source_singular = _required_message(source.singular)
    _child(source_singular).value = 101
    source_singular._get_or_init_unknown_fields()[99].append(b"\x98\x06\x02")
    _child(source.repeated[0]).value = 102
    _child(source.message_map["source"]).value = 103
    _child(source_choice).value = 104

    assert target.to_binary() == target_wire


def test_merge_view_detaches_target_mutation_in_every_message_owner() -> None:
    source = _source()
    source_wire = source.to_binary()
    expected = copy.deepcopy(source)
    target = DelimitedEncoding()

    merge_from(target, source)

    _child(_required_message(target.singular)).value = 201
    _child(_required_message(expected.singular)).value = 201
    _child(target.repeated[0]).value = 202
    _child(expected.repeated[0]).value = 202
    _child(target.message_map["source"]).value = 203
    _child(expected.message_map["source"]).value = 203
    _child(_choice_message(target.choice)).value = 204
    _child(_choice_message(expected.choice)).value = 204

    assert target.to_binary() == expected.to_binary()
    assert source.to_binary() == source_wire


def test_merge_view_keeps_unknown_fields_independent() -> None:
    writer = BinaryWriter()
    writer.tag(120, WireType.VARINT)
    writer.int32(1)
    source = DelimitedEncoding.from_binary(writer.finish())
    target = DelimitedEncoding()

    merge_from(target, source)
    target_wire = target.to_binary()

    assert source._unknown_fields is not None
    source._unknown_fields[120].append(b"\xc0\x07\x02")
    assert target.to_binary() == target_wire


def test_deferred_merge_snapshots_before_source_mutation_and_json_access() -> None:
    source = _source()
    source_wire = source.to_binary()
    target = DelimitedEncoding()

    merge_from(target, source)
    source.name = "changed"
    _child(_required_message(source.singular)).value = 501
    _child(source.repeated[0]).value = 502
    _child(source.message_map["source"]).value = 503

    assert target.desc() is DelimitedEncoding.desc()
    assert target.to_json() == DelimitedEncoding.from_binary(source_wire).to_json()
    assert target.to_binary() == source_wire


def test_deferred_merge_copy_pickle_and_unknown_field_filtering() -> None:
    source = _source()
    target = DelimitedEncoding()
    merge_from(target, source)

    shallow = copy.copy(target)
    deep = copy.deepcopy(target)
    unpickled = pickle.loads(pickle.dumps(target))  # noqa: S301
    assert shallow.to_binary() == target.to_binary()
    assert deep.to_binary() == target.to_binary()
    assert unpickled.to_binary() == target.to_binary()

    expected_without_unknown = source.to_binary(write_unknown_fields=False)
    assert target.to_binary(write_unknown_fields=False) == expected_without_unknown


def test_deferred_merge_keeps_held_empty_containers_observable() -> None:
    source = _source()
    target = DelimitedEncoding()
    held_repeated = target.repeated
    held_map = target.message_map
    expected = DelimitedEncoding()

    merge_from(target, source)
    merge_from(expected, source)
    assert target.to_binary() == expected.to_binary()

    held_repeated.append(_message(50))
    held_map["local"] = _message(60)
    expected.repeated.append(_message(50))
    expected.message_map["local"] = _message(60)

    assert target.to_binary() == expected.to_binary()


def test_deferred_merge_keeps_generic_attribute_container_alias_observable() -> None:
    source = _source()
    target = DelimitedEncoding()
    held_repeated = object.__getattribute__(target, "repeated")
    held_map = object.__getattribute__(target, "message_map")
    expected = DelimitedEncoding()

    merge_from(target, source)
    merge_from(expected, source)
    assert target.to_binary() == expected.to_binary()

    held_repeated.append(_message(70))
    held_map["local"] = _message(80)
    expected.repeated.append(_message(70))
    expected.message_map["local"] = _message(80)

    assert target.to_binary() == expected.to_binary()


def test_deferred_merge_keeps_generic_attribute_assignment_observable() -> None:
    source = _source()
    target = DelimitedEncoding()
    held_repeated: list[DelimitedEncoding.Msg] = []
    object.__setattr__(target, "repeated", held_repeated)
    expected = DelimitedEncoding()

    merge_from(target, source)
    merge_from(expected, source)
    assert target.to_binary() == expected.to_binary()

    held_repeated.append(_message(70))
    expected.repeated.append(_message(70))

    assert target.to_binary() == expected.to_binary()


def test_deferred_nested_generic_attribute_materializes_before_alias() -> None:
    source = Recursive(repeated_recursive=[Recursive(repeated_recursive=[Recursive()])])
    target = Recursive()
    expected = Recursive()

    merge_from(target, source)
    merge_from(expected, source)
    child = object.__getattribute__(target, "repeated_recursive")[0]
    held_repeated = object.__getattribute__(child, "repeated_recursive")
    assert len(held_repeated) == 1

    held_repeated.append(Recursive())
    expected.repeated_recursive[0].repeated_recursive.append(Recursive())

    assert target.to_binary() == expected.to_binary()


def test_deferred_merge_keeps_descriptor_assigned_containers_observable() -> None:
    source = _source()
    repeated = DelimitedEncoding.desc()._fields_by_local_name["repeated"]
    message_map = DelimitedEncoding.desc()._fields_by_local_name["message_map"]
    target = DelimitedEncoding()
    held_repeated: list[DelimitedEncoding.Msg] = []
    held_map: dict[str, DelimitedEncoding.Msg] = {}
    target[repeated] = held_repeated
    target[message_map] = held_map
    expected = DelimitedEncoding()

    merge_from(target, source)
    merge_from(expected, source)
    assert target.to_binary() == expected.to_binary()

    held_repeated.append(_message(70))
    held_map["local"] = _message(80)
    expected.repeated.append(_message(70))
    expected.message_map["local"] = _message(80)

    assert target.to_binary() == expected.to_binary()


def test_deferred_merge_keeps_held_unknown_fields_observable() -> None:
    source = DelimitedEncoding(name="source")
    target = DelimitedEncoding()
    held_unknown_fields = object.__getattribute__(
        target, "_get_or_init_unknown_fields"
    )()
    expected = DelimitedEncoding()

    merge_from(target, source)
    merge_from(expected, source)
    assert target.to_binary() == expected.to_binary()

    writer = BinaryWriter()
    writer.tag(120, WireType.VARINT)
    writer.int32(1)
    held_unknown_fields[120] = [writer.finish()]
    expected._get_or_init_unknown_fields()[120] = [writer.finish()]

    assert target.to_binary() == expected.to_binary()


def test_deferred_merge_materializes_before_binary_merge() -> None:
    source = _source()
    target = DelimitedEncoding()
    merge_from(target, source)

    update = DelimitedEncoding(
        repeated=[_message(70)], message_map={"update": _message(80)}
    )
    expected = DelimitedEncoding.from_binary(source.to_binary())
    merge_from(expected, update)

    target._merge_from_binary(update.to_binary(), ignore_unknown_fields=False)
    assert target.to_binary() == expected.to_binary()


def test_deferred_merge_concurrent_reads_see_one_complete_snapshot() -> None:
    source = _source()
    source_wire = source.to_binary()
    target = DelimitedEncoding()
    merge_from(target, source)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def read_fields() -> None:
        try:
            barrier.wait()
            for _ in range(50):
                assert target.name == "source"
                assert _child(_required_message(target.singular)).value == 11
        except Exception as error:  # noqa: BLE001  # pragma: no cover - raised in worker threads
            errors.append(error)

    def serialize() -> None:
        try:
            barrier.wait()
            for _ in range(50):
                assert target.to_binary() == source_wire
        except Exception as error:  # noqa: BLE001  # pragma: no cover - raised in worker threads
            errors.append(error)

    readers = [
        threading.Thread(target=read_fields, daemon=True),
        threading.Thread(target=serialize, daemon=True),
    ]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join(timeout=5)

    assert all(not reader.is_alive() for reader in readers)
    assert not errors


def test_deferred_merge_materializes_before_descriptor_mutation_and_clear() -> None:
    source = _source()
    name = DelimitedEncoding.desc()._fields_by_local_name["name"]

    target = DelimitedEncoding()
    merge_from(target, source)
    target[name] = "target"
    assert target.name == "target"
    assert source.name == "source"

    target = DelimitedEncoding()
    merge_from(target, source)
    target.clear_field("singular")
    assert target.singular is None
    assert source.singular is not None
