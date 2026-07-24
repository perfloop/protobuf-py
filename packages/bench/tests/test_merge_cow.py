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

import os
import tracemalloc
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture

TREE_WIDTH = 6
TREE_DEPTH = 3
TREE_NODE_COUNT = 2380


def _recursive_type() -> type[Any]:
    """Build a fresh generated type so its owner matches the active runtime."""
    from protobuf.wkt import FileDescriptorSet
    from tests.gen import enums_pb, messages_pb

    registry = FileDescriptorSet(
        file=[enums_pb.desc().proto, messages_pb.desc().proto]
    ).to_registry()
    desc = registry.message("Recursive")
    assert desc is not None
    return desc.type


def _uses_native(message_type: type[Any]) -> bool:
    return any(base.__name__ == "NativeMessage" for base in message_type.__mro__)


def _make_tree(message_type: type[Any], depth: int = TREE_DEPTH) -> Any:
    message = message_type()
    if depth == 0:
        return message
    message.recursive = _make_tree(message_type, depth - 1)
    message.repeated_recursive.extend(
        _make_tree(message_type, depth - 1) for _ in range(TREE_WIDTH)
    )
    message.map_recursive.update(
        {str(index): _make_tree(message_type, depth - 1) for index in range(TREE_WIDTH)}
    )
    return message


def _count_tree(message: Any) -> int:
    count = 1
    if message.recursive is not None:
        count += _count_tree(message.recursive)
    count += sum(_count_tree(child) for child in message.repeated_recursive)
    count += sum(_count_tree(child) for child in message.map_recursive.values())
    return count


def _merge_tree(message_type: type[Any], source: Any) -> Any:
    from protobuf import merge_from

    target = message_type()
    merge_from(target, source)
    return target


def _merge_mutate_serialize(message_type: type[Any], source: Any) -> bytes:
    target = _merge_tree(message_type, source)
    target.recursive.recursive = message_type()
    target.repeated_recursive[0].recursive = message_type()
    target.map_recursive["0"].recursive = message_type()
    return target.to_binary()


def test_merge_tree_contract() -> None:
    message_type = _recursive_type()
    expected_native = os.environ.get("PERFLOOP_EXPECT_NATIVE") == "1"
    assert _uses_native(message_type) is expected_native

    source = _make_tree(message_type)
    assert _count_tree(source) == TREE_NODE_COUNT
    target = message_type()
    target.recursive = message_type()
    target.repeated_recursive.append(message_type())
    target.map_recursive["existing"] = message_type()

    target_child = target.recursive
    target_list = target.repeated_recursive
    target_map = target.map_recursive

    from protobuf import merge_from

    merge_from(target, source)
    assert target.recursive is target_child
    assert target.repeated_recursive is target_list
    assert target.map_recursive is target_map
    assert target.recursive is not source.recursive
    assert target.repeated_recursive[1] is not source.repeated_recursive[0]
    assert target.map_recursive["0"] is not source.map_recursive["0"]

    target_bytes = target.to_binary()
    source.recursive.recursive = message_type()
    source.repeated_recursive[0].recursive = message_type()
    source.map_recursive["0"].recursive = message_type()
    assert target.to_binary() == target_bytes


def test_merge_tree_allocation_profile() -> None:
    message_type = _recursive_type()
    expected_native = os.environ.get("PERFLOOP_EXPECT_NATIVE") == "1"
    assert _uses_native(message_type) is expected_native

    source = _make_tree(message_type)
    tracemalloc.start()
    target = _merge_tree(message_type, source)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert _count_tree(target) == TREE_NODE_COUNT
    assert peak > 0
    print(f"merge_tree_profile nodes={TREE_NODE_COUNT} peak_bytes={peak}")


@pytest.mark.benchmark(min_time=0.1)
def test_native_merge_tree(benchmark: BenchmarkFixture) -> None:
    message_type = _recursive_type()
    assert _uses_native(message_type)
    source = _make_tree(message_type)
    source_bytes = source.to_binary()

    target = benchmark(_merge_tree, message_type, source)

    assert target.to_binary() == source_bytes
    assert source.to_binary() == source_bytes


@pytest.mark.benchmark(min_time=0.1)
def test_python_merge_tree(benchmark: BenchmarkFixture) -> None:
    message_type = _recursive_type()
    assert not _uses_native(message_type)
    source = _make_tree(message_type)
    source_bytes = source.to_binary()

    target = benchmark(_merge_tree, message_type, source)

    assert target.to_binary() == source_bytes
    assert source.to_binary() == source_bytes


@pytest.mark.benchmark(min_time=0.1)
def test_native_merge_mutate_serialize(benchmark: BenchmarkFixture) -> None:
    message_type = _recursive_type()
    assert _uses_native(message_type)
    source = _make_tree(message_type)
    source_bytes = source.to_binary()
    expected = _merge_mutate_serialize(message_type, source)

    result = benchmark(_merge_mutate_serialize, message_type, source)

    assert result == expected
    assert source.to_binary() == source_bytes
