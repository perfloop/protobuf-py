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

from typing import TYPE_CHECKING, Any

import pytest

from protobuf import merge_from
from tests.gen.delimited_encoding_pb import DelimitedEncoding
from tests.gen.messages_pb import Recursive

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


def _uses_native(message_type: type[Any]) -> bool:
    return any(base.__name__ == "NativeMessage" for base in message_type.__mro__)


def _activate_lazy_descriptors() -> None:
    delimited_source = DelimitedEncoding(singular=DelimitedEncoding.Msg(value=1))
    merge_from(DelimitedEncoding(), delimited_source)
    recursive_source = Recursive(repeated_recursive=[Recursive()])
    merge_from(Recursive(), recursive_source)


def _read_fields(message: DelimitedEncoding.Msg, recursive: Recursive) -> int:
    result = 0
    for _ in range(100):
        result += message.value
        result += len(recursive.repeated_recursive)
        result += len(recursive.map_recursive)
    return result


@pytest.mark.benchmark(min_time=0.1)
def test_native_inactive_field_reads(benchmark: BenchmarkFixture) -> None:
    assert _uses_native(DelimitedEncoding)
    _activate_lazy_descriptors()
    message = DelimitedEncoding.Msg(value=1)
    recursive = Recursive()
    assert type(message) is DelimitedEncoding.Msg
    assert type(recursive) is Recursive

    assert benchmark(_read_fields, message, recursive) == 100


@pytest.mark.benchmark(min_time=0.1)
def test_native_inactive_to_binary(benchmark: BenchmarkFixture) -> None:
    assert _uses_native(DelimitedEncoding)
    _activate_lazy_descriptors()
    message = DelimitedEncoding.Msg(value=1)
    assert type(message) is DelimitedEncoding.Msg
    expected = message.to_binary()

    assert benchmark(message.to_binary) == expected
