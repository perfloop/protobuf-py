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

from protobuf import Oneof
from tests.gen.messages_pb import MixedFields
from tests.wide_sparse_serialization import (
    expected_wide_sparse_binary,
    make_wide_sparse_message,
)

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


_WIDE_SPARSE_SHAPES = [
    pytest.param("f10-s1", 10, 1, id="f10-s1"),
    pytest.param("f100-s1", 100, 1, id="f100-s1"),
    pytest.param("f100-s10", 100, 10, id="f100-s10"),
    pytest.param("f1000-s1", 1000, 1, id="f1000-s1"),
    pytest.param("f1000-s10", 1000, 10, id="f1000-s10"),
    pytest.param("f1000-s1000", 1000, 1000, id="f1000-s1000"),
]


@pytest.mark.parametrize(
    ("_id", "field_count", "selected_count"), _WIDE_SPARSE_SHAPES
)
@pytest.mark.benchmark(min_time=0.1)
def test_to_binary_wide_sparse(
    _id: str, field_count: int, selected_count: int, benchmark: BenchmarkFixture
) -> None:
    field_numbers = range(1, field_count + 1)
    selected_numbers = range(1, selected_count + 1)
    message = make_wide_sparse_message(field_numbers, selected_numbers)
    expected = expected_wide_sparse_binary(field_numbers, selected_numbers)

    result = benchmark(message.to_binary)

    assert result == expected


def test_wide_sparse_binary_follows_descriptor_order_after_mutation() -> None:
    field_numbers = (30, 1, 15)
    selected_numbers = (30, 1, 15)
    message = make_wide_sparse_message(field_numbers, selected_numbers)

    assert message.to_binary() == expected_wide_sparse_binary(
        field_numbers, selected_numbers
    )

    message.clear_field("f_1")

    assert message.to_binary() == expected_wide_sparse_binary(field_numbers, (30, 15))


def test_mixed_serialization_tracks_post_construction_mutations() -> None:
    message = MixedFields()
    message.explicit_field = 0
    message.implicit_field = 0
    message.repeated_field.append("list")
    message.message_field = MixedFields.Bar(value="nested")
    message.map_field["k"] = 7
    message.oneof_group = Oneof(field="oneof_field", value="")
    message.implicit_enum_field = MixedFields.E.ONE
    message.explicit_enum_field = MixedFields.E.UNSPECIFIED

    assert message.to_binary() == bytes.fromhex(
        "080022046c6973742a080a066e65737465643a050a016b1007420050015800"
    )

    message.repeated_field.clear()
    message.map_field.clear()
    message.clear_field("explicit_field")
    message.clear_field("message_field")
    message.clear_field("oneof_field")
    message.clear_field("explicit_enum_field")

    assert message.to_binary() == b"\x50\x01"
