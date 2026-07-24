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
from __future__ import annotations

import copy
import pickle
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from protobuf import DescFieldValueMessage, DescUnknownField, merge_from
from tests.gen.delimited_encoding_pb import DelimitedEncoding
from tests.gen.messages_pb import Recursive


def _message(value: int) -> DelimitedEncoding.Msg:
    return DelimitedEncoding.Msg(
        value=value, child=DelimitedEncoding.Msg(value=value + 1)
    )


def _singular(message: DelimitedEncoding) -> DelimitedEncoding.Msg:
    assert message.singular is not None
    return message.singular


def _child(message: DelimitedEncoding.Msg) -> DelimitedEncoding.Msg:
    assert message.child is not None
    return message.child


class _SpecializedMsg(DelimitedEncoding.Msg):
    __slots__ = ()

    def marker(self) -> int:
        return 1


def test_wire_empty_merge_preserves_container_aliases_and_source_isolation() -> None:
    source = DelimitedEncoding(
        name="source",
        singular=_message(1),
        repeated=[_message(2)],
        message_map={"first": _message(3), "second": _message(4)},
    )
    target = DelimitedEncoding()
    held_repeated = object.__getattribute__(target, "repeated")
    held_map = object.__getattribute__(target, "message_map")
    source_wire = source.to_binary()

    merge_from(target, source)

    assert target.repeated is held_repeated
    assert target.message_map is held_map
    assert _singular(target) is not _singular(source)
    assert target.repeated[0] is not source.repeated[0]
    assert target.message_map["first"] is not source.message_map["first"]
    assert target.to_binary() == source_wire

    _child(_singular(source)).value = 100
    _child(source.repeated[0]).value = 101
    _child(source.message_map["first"]).value = 102
    assert target.to_binary() == source_wire


def test_wire_empty_merge_populates_an_object_assigned_container() -> None:
    source = DelimitedEncoding(repeated=[_message(1)])
    target = DelimitedEncoding()
    held_repeated: list[DelimitedEncoding.Msg] = []
    object.__setattr__(target, "repeated", held_repeated)

    merge_from(target, source)

    assert target.repeated is held_repeated
    assert target.repeated[0] is not source.repeated[0]
    assert target.to_binary() == source.to_binary()


def test_wire_empty_merge_keeps_explicit_unknown_field_provenance() -> None:
    known_singular = DelimitedEncoding.desc()._fields_by_local_name["singular"]
    assert isinstance(known_singular.value, DescFieldValueMessage)
    source = DelimitedEncoding(singular=_message(1))
    known_wire = source.to_binary()
    source[DescUnknownField(known_singular.number, known_singular.value)] = _message(2)
    source_wire = source.to_binary()
    target = DelimitedEncoding()

    merge_from(target, source)

    assert _singular(target).value == 1
    assert target._unknown_fields == source._unknown_fields
    assert target.to_binary() == source_wire
    assert target.to_binary(write_unknown_fields=False) == known_wire


def test_wire_empty_merge_keeps_nested_unknown_field_provenance() -> None:
    known_child = DelimitedEncoding.Msg.desc()._fields_by_local_name["child"]
    assert isinstance(known_child.value, DescFieldValueMessage)
    child = DelimitedEncoding.Msg(value=1)
    child[DescUnknownField(known_child.number, known_child.value)] = (
        DelimitedEncoding.Msg(value=2)
    )
    source = DelimitedEncoding(singular=child)
    source_wire = source.to_binary()
    target = DelimitedEncoding()

    merge_from(target, source)

    assert _singular(target).child is None
    assert _singular(target)._unknown_fields == child._unknown_fields
    assert target.to_binary() == source_wire


def test_object_attribute_access_materializes_nested_snapshot() -> None:
    source = Recursive(repeated_recursive=[Recursive(repeated_recursive=[Recursive()])])
    target = Recursive()
    expected = Recursive.from_binary(source.to_binary())

    merge_from(target, source)
    child = object.__getattribute__(target, "repeated_recursive")[0]
    held_repeated = object.__getattribute__(child, "repeated_recursive")
    assert len(held_repeated) == 1
    held_repeated.append(Recursive())
    expected.repeated_recursive[0].repeated_recursive.append(Recursive())

    assert target.to_binary() == expected.to_binary()


def test_wire_empty_merge_retains_nested_message_subclass() -> None:
    source = DelimitedEncoding(singular=_SpecializedMsg(value=1))
    target = DelimitedEncoding()

    merge_from(target, source)

    child = _singular(target)
    assert type(child) is _SpecializedMsg
    assert child.marker() == 1


def test_nested_snapshot_repr_preserves_generated_type() -> None:
    source = DelimitedEncoding(singular=_message(1))
    target = DelimitedEncoding()
    merge_from(target, source)
    child = object.__getattribute__(target, "singular")
    assert child is not None

    assert repr(child) == repr(_singular(source))
    assert type(child) is DelimitedEncoding.Msg


def test_merge_into_nested_snapshot_materializes_existing_state() -> None:
    source = DelimitedEncoding(singular=_message(1))
    target = DelimitedEncoding()
    merge_from(target, source)
    child = object.__getattribute__(target, "singular")
    assert child is not None

    merge_from(child, DelimitedEncoding.Msg(value=99))

    assert child.value == 99
    assert _child(child).value == 2


def test_nested_snapshot_setstate_replaces_deferred_wire_data() -> None:
    source = Recursive(repeated_recursive=[Recursive(repeated_recursive=[Recursive()])])
    target = Recursive()
    merge_from(target, source)
    child = object.__getattribute__(target, "repeated_recursive")[0]

    child.__setstate__(b"")

    assert child.to_binary() == b""
    assert child.repeated_recursive == []


def test_object_setattr_materializes_nested_snapshot() -> None:
    source = Recursive(repeated_recursive=[Recursive(repeated_recursive=[Recursive()])])
    target = Recursive()

    merge_from(target, source)
    child = object.__getattribute__(target, "repeated_recursive")[0]
    held_repeated: list[Recursive] = []
    object.__setattr__(child, "repeated_recursive", held_repeated)

    assert child.repeated_recursive is held_repeated
    assert len(source.repeated_recursive[0].repeated_recursive) == 1


def test_concurrent_nested_snapshot_access_completes() -> None:
    source = DelimitedEncoding(singular=_message(1))
    target = DelimitedEncoding()
    merge_from(target, source)
    child = object.__getattribute__(target, "singular")
    assert child is not None
    barrier = Barrier(4)

    def read_value() -> int:
        barrier.wait()
        return child.value

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(lambda _: read_value(), range(4))) == [1] * 4
    assert target.to_binary() == source.to_binary()


def test_nested_snapshot_copy_and_pickle_preserve_wire_output() -> None:
    source = DelimitedEncoding(singular=_message(1), repeated=[_message(2)])
    target = DelimitedEncoding()
    merge_from(target, source)
    child = object.__getattribute__(target, "singular")
    assert child is not None

    assert copy.copy(child).to_binary() == _singular(source).to_binary()
    pickled_child = pickle.loads(pickle.dumps(child))  # noqa: S301
    assert pickled_child.to_binary() == _singular(source).to_binary()
    assert type(child) is DelimitedEncoding.Msg
    assert copy.copy(target).to_binary() == source.to_binary()
    assert copy.deepcopy(target).to_binary() == source.to_binary()
    assert pickle.loads(pickle.dumps(target)).to_binary() == source.to_binary()  # noqa: S301
