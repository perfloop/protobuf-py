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

from itertools import cycle
from typing import TYPE_CHECKING

import pytest

from .gen.delimited_encoding_pb import DelimitedEncoding
from .gen.lists_pb import Lists
from .gen.scalars_pb import Scalars

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


def _make_scalars(index: int) -> Scalars:
    return Scalars(
        double_field=1.25 + index,
        float_field=2.5 + index,
        int32_field=-3 - index,
        int64_field=-4 - index,
        uint32_field=5 + index,
        uint64_field=6 + index,
        sint32_field=-7 - index,
        sint64_field=-8 - index,
        fixed32_field=9 + index,
        fixed64_field=10 + index,
        sfixed32_field=-11 - index,
        sfixed64_field=-12 - index,
        bool_field=index % 2 == 0,
        string_field=f"known-fields-{index:02d}",
        bytes_field=f"payload-{index:02d}".encode(),
    )


_KNOWN_FIELD_MESSAGES = tuple(_make_scalars(index) for index in range(3))
_KNOWN_FIELD_PAYLOADS = tuple(message.to_binary() for message in _KNOWN_FIELD_MESSAGES)


def test_from_binary_tag_branches() -> None:
    for message, payload in zip(_KNOWN_FIELD_MESSAGES, _KNOWN_FIELD_PAYLOADS, strict=True):
        assert Scalars.from_binary(payload) == message

    list_message = Lists(int32_list=[1, 2, 3], string_list=["one", "two"])
    assert Lists.from_binary(list_message.to_binary()) == list_message

    group_message = DelimitedEncoding(
        singular=DelimitedEncoding.Msg(
            value=1, child=DelimitedEncoding.Msg(value=2)
        ),
        repeated=[DelimitedEncoding.Msg(value=3)],
        scalar_map={"key": 4},
    )
    assert DelimitedEncoding.from_binary(group_message.to_binary()) == group_message

    unknown_bytes = b"\x98\x06\x07"
    decoded = Scalars.from_binary(_KNOWN_FIELD_PAYLOADS[0] + unknown_bytes)
    assert decoded == _KNOWN_FIELD_MESSAGES[0]
    assert decoded._unknown_fields == {99: [unknown_bytes]}
    assert Scalars.from_binary(
        _KNOWN_FIELD_PAYLOADS[0] + unknown_bytes, ignore_unknown_fields=True
    ) == _KNOWN_FIELD_MESSAGES[0]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        pytest.param(b"\x00", "invalid tag with field number 0", id="zero-field-number"),
        pytest.param(b"\x0e", "6 is not a valid WireType", id="reserved-wire-type"),
        pytest.param(
            b"\x0c",
            "unexpected end group tag outside of group",
            id="top-level-end-group",
        ),
    ],
)
def test_from_binary_rejects_malformed_tags(payload: bytes, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        Scalars.from_binary(payload)


def test_from_binary_many_known_fields(benchmark: BenchmarkFixture) -> None:
    payloads = cycle(_KNOWN_FIELD_PAYLOADS)

    def decode() -> Scalars:
        return Scalars.from_binary(next(payloads))

    decoded = benchmark(decode)
    assert decoded in _KNOWN_FIELD_MESSAGES
