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

import json
from typing import TYPE_CHECKING

import pytest

from protobuf import _from_json
from protobuf._from_json import _no_duplicates
from tests.gen.maps_pb import Maps

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


def _map_payload(member_count: int) -> str:
    entries = {f"key_{index}": f"value_{index}" for index in range(member_count)}
    return json.dumps({"stringToString": entries}, separators=(",", ":"))


def test_no_duplicates_preserves_unique_mapping_order() -> None:
    pairs = [("first", 1), ("second", 2), ("third", 3)]

    assert list(_no_duplicates(pairs).items()) == pairs


@pytest.mark.parametrize(
    ("payload", "duplicate_key"),
    [
        pytest.param(
            '{"stringToString":{"first":"one","first":"two"}}',
            "first",
            id="second-pair",
        ),
        pytest.param(
            '{"stringToString":{"first":"one","second":"two","second":"three"}}',
            "second",
            id="third-pair",
        ),
        pytest.param(
            '{"stringToString":{},"stringToString":{}}',
            "stringToString",
            id="outer-object",
        ),
    ],
)
def test_maps_from_json_reports_first_duplicate_key(
    payload: str, duplicate_key: str
) -> None:
    with pytest.raises(ValueError, match=rf"^duplicate key: {duplicate_key}$"):
        Maps.from_json(payload)


def test_maps_from_json_keeps_unique_nested_entries() -> None:
    payload = _map_payload(3)

    message = Maps.from_json(payload)

    assert dict(message.string_to_string) == {
        "key_0": "value_0",
        "key_1": "value_1",
        "key_2": "value_2",
    }


def test_maps_from_json_calls_duplicate_hook_for_each_nested_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    original = _from_json._no_duplicates

    def count_pairs(pairs: list[tuple[str, _from_json.JsonValue]]) -> dict[str, _from_json.JsonValue]:
        calls.append(len(pairs))
        return original(pairs)

    monkeypatch.setattr(_from_json, "_no_duplicates", count_pairs)
    message = Maps.from_json(_map_payload(1000))

    assert calls == [1000, 1]
    assert len(message.string_to_string) == 1000


@pytest.mark.parametrize(
    ("_id", "payload", "member_count"),
    [
        pytest.param("1-members", _map_payload(1), 1, id="1-members"),
        pytest.param("10-members", _map_payload(10), 10, id="10-members"),
        pytest.param("100-members", _map_payload(100), 100, id="100-members"),
        pytest.param("1000-members", _map_payload(1000), 1000, id="1000-members"),
    ],
)
@pytest.mark.benchmark(min_time=0.01)
def test_maps_from_json_duplicate_free(
    _id: str, payload: str, member_count: int, benchmark: BenchmarkFixture
) -> None:
    first_key = "key_0"
    last_key = f"key_{member_count - 1}"
    last_value = f"value_{member_count - 1}"

    def parse_and_validate() -> Maps:
        message = Maps.from_json(payload)
        assert len(message.string_to_string) == member_count
        assert message.string_to_string[first_key] == "value_0"
        assert message.string_to_string[last_key] == last_value
        return message

    result = benchmark(parse_and_validate)

    assert len(result.string_to_string) == member_count
