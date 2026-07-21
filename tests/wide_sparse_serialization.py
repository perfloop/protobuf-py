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

from collections.abc import Iterable

from protobuf import Message
from protobuf.wkt import (
    DescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
)


def encode_varint(value: int) -> bytes:
    assert value >= 0
    encoded = bytearray()
    while value > 0x7F:
        encoded.append(value & 0x7F | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def expected_wide_sparse_binary(
    field_numbers: Iterable[int], selected_numbers: Iterable[int]
) -> bytes:
    selected = set(selected_numbers)
    return b"".join(
        encode_varint(number << 3) + encode_varint(number)
        for number in field_numbers
        if number in selected
    )


def make_wide_sparse_message(
    field_numbers: Iterable[int], selected_numbers: Iterable[int]
) -> Message:
    numbers = tuple(field_numbers)
    selected = tuple(selected_numbers)
    assert len(set(numbers)) == len(numbers)
    assert all(number > 0 for number in numbers)
    assert set(selected) <= set(numbers)

    fields = [
        FieldDescriptorProto(
            name=f"f_{number}",
            number=number,
            label=FieldDescriptorProto.Label.OPTIONAL,
            type=FieldDescriptorProto.Type.INT32,
        )
        for number in numbers
    ]
    file_proto = FileDescriptorProto(
        name="wide_sparse.proto",
        syntax="proto2",
        message_type=[DescriptorProto(name="WideSparse", field=fields)],
    )
    desc = FileDescriptorSet(file=[file_proto]).to_registry().message("WideSparse")
    assert desc is not None
    message = desc.type()
    for number in reversed(selected):
        setattr(message, f"f_{number}", number)
    return message
