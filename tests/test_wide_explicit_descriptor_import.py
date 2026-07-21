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

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture

    from protobuf import DescMessage


_FIELD_COUNT = 1000


def _wide_explicit_descriptor_set() -> FileDescriptorSet:
    fields = [
        FieldDescriptorProto(
            name=f"f_{number}",
            number=number,
            label=FieldDescriptorProto.Label.OPTIONAL,
            type=FieldDescriptorProto.Type.INT32,
        )
        for number in range(1, _FIELD_COUNT + 1)
    ]
    return FileDescriptorSet(
        file=[
            FileDescriptorProto(
                name="wide_explicit_descriptor_import.proto",
                syntax="proto2",
                message_type=[DescriptorProto(name="WideExplicit", field=fields)],
            )
        ]
    )


def _import_wide_explicit_descriptor(descriptor_set: FileDescriptorSet) -> DescMessage:
    desc = descriptor_set.to_registry().message("WideExplicit")
    assert desc is not None
    return desc


@pytest.mark.parametrize("_id", ["f1000"])
@pytest.mark.benchmark(min_time=0.1)
def test_import_wide_explicit_descriptor_f1000(
    _id: str, benchmark: BenchmarkFixture
) -> None:
    descriptor_set = _wide_explicit_descriptor_set()

    desc = benchmark(_import_wide_explicit_descriptor, descriptor_set)

    assert len(desc.fields) == _FIELD_COUNT
    assert desc.fields[0].number == 1
    assert desc.fields[-1].number == _FIELD_COUNT
