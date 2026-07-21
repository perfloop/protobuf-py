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

from typing import TYPE_CHECKING

import pytest

from protobuf.wkt import (
    DescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    FileDescriptorSet,
)
from tests.gen.enums_pb import Color
from tests.gen.scalars_pb import Scalars

if TYPE_CHECKING:
    from protobuf import DescMessage

OPTIONAL = FieldDescriptorProto.Label.OPTIONAL
FIELD_TYPE = FieldDescriptorProto.Type


def _import_scalars() -> DescMessage:
    descriptor_set = FileDescriptorSet(file=[Scalars.desc().file.proto])
    registry = FileDescriptorSet.from_binary(descriptor_set.to_binary()).to_registry()
    desc = registry.message("Scalars")
    assert desc is not None
    return desc


@pytest.mark.parametrize(
    ("field_name", "wire"),
    [
        pytest.param("bool_field", b"\x68\x00", id="varint-explicit-zero"),
        pytest.param(
            "double_field", b"\x09\x00\x00\x00\x00\x00\x00\xf8\x3f", id="bit64"
        ),
        pytest.param("string_field", b"\x72\x02ok", id="length-delimited"),
        pytest.param("fixed32_field", b"\x4d\x04\x03\x02\x01", id="bit32"),
    ],
)
def test_to_binary_reemits_single_parsed_explicit_scalar(
    field_name: str, wire: bytes
) -> None:
    desc = _import_scalars()
    assert desc._single_present_fast_path_eligible

    message = desc.type.from_binary(wire)

    assert message.has_field(field_name)
    assert message.to_binary() == wire


def test_to_binary_reemits_single_parsed_explicit_enum_out_of_order() -> None:
    descriptor_set = FileDescriptorSet(
        file=[
            FileDescriptorProto(
                name="out_of_order_enum.proto",
                syntax="proto2",
                enum_type=[Color.desc().proto],
                message_type=[
                    DescriptorProto(
                        name="OutOfOrder",
                        field=[
                            FieldDescriptorProto(
                                name="before",
                                number=40,
                                label=OPTIONAL,
                                type=FIELD_TYPE.BOOL,
                            ),
                            FieldDescriptorProto(
                                name="color",
                                number=30,
                                label=OPTIONAL,
                                type=FIELD_TYPE.ENUM,
                                type_name="Color",
                            ),
                        ],
                    )
                ],
            )
        ]
    )
    registry = FileDescriptorSet.from_binary(descriptor_set.to_binary()).to_registry()
    desc = registry.message("OutOfOrder")
    assert desc is not None
    assert desc._single_present_fast_path_eligible

    message = desc.type.from_binary(b"\xf0\x01\x00")

    assert message.has_field("color")
    assert message.to_binary() == b"\xf0\x01\x00"


def test_to_binary_preserves_singleton_reentrant_mutation() -> None:
    descriptor_set = FileDescriptorSet(
        file=[
            FileDescriptorProto(
                name="reentrant_string.proto",
                syntax="proto2",
                message_type=[
                    DescriptorProto(
                        name="ReentrantString",
                        field=[
                            FieldDescriptorProto(
                                name="text",
                                number=1,
                                label=OPTIONAL,
                                type=FIELD_TYPE.STRING,
                            ),
                            FieldDescriptorProto(
                                name="later",
                                number=2,
                                label=OPTIONAL,
                                type=FIELD_TYPE.BOOL,
                            ),
                        ],
                    )
                ],
            )
        ]
    )
    registry = FileDescriptorSet.from_binary(descriptor_set.to_binary()).to_registry()
    desc = registry.message("ReentrantString")
    assert desc is not None
    assert desc._single_present_fast_path_eligible

    message = desc.type()

    class SetLaterString(str):
        __slots__ = ()

        def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
            message.later = True
            return super().encode(encoding, errors)

    message.text = SetLaterString("text")
    expected = b"\x0a\x04text\x10\x01"

    assert message.to_binary() == expected
    assert message.to_binary() == expected


@pytest.mark.parametrize(
    ("fields", "values", "wire"),
    [
        pytest.param(
            [
                FieldDescriptorProto(
                    name="allow", number=1, label=OPTIONAL, type=FIELD_TYPE.BOOL
                ),
                FieldDescriptorProto(
                    name="proof", number=1, label=OPTIONAL, type=FIELD_TYPE.FIXED32
                ),
            ],
            {"allow": True, "proof": 0x01020304},
            b"\x08\x01\x0d\x04\x03\x02\x01",
            id="different-wire-types",
        ),
        pytest.param(
            [
                FieldDescriptorProto(
                    name="allow", number=1, label=OPTIONAL, type=FIELD_TYPE.BOOL
                ),
                FieldDescriptorProto(
                    name="enabled", number=1, label=OPTIONAL, type=FIELD_TYPE.BOOL
                ),
            ],
            {"allow": True, "enabled": False},
            b"\x08\x01\x08\x00",
            id="same-wire-type",
        ),
    ],
)
def test_to_binary_preserves_duplicate_dynamic_field_numbers(
    fields: list[FieldDescriptorProto], values: dict[str, object], wire: bytes
) -> None:
    descriptor_set = FileDescriptorSet(
        file=[
            FileDescriptorProto(
                name="duplicate_field_numbers.proto",
                syntax="proto2",
                message_type=[
                    DescriptorProto(name="DuplicateFieldNumbers", field=fields)
                ],
            )
        ]
    )
    desc = descriptor_set.to_registry().message("DuplicateFieldNumbers")
    assert desc is not None
    assert not desc._single_present_fast_path_eligible

    assert desc.type(**values).to_binary() == wire
