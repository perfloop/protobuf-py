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

from tests.gen.maps_pb import Maps

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


MEMBER_COUNT = 1000


def _non_ascii_string_map_payload() -> str:
    entries = {f"clé_{index}": f"東京_{index}" for index in range(MEMBER_COUNT)}
    return json.dumps(
        {"stringToString": entries}, ensure_ascii=False, separators=(",", ":")
    )


def _int32_map_payload() -> str:
    entries = {str(index): index for index in range(MEMBER_COUNT)}
    return json.dumps({"int32ToInt32": entries}, separators=(",", ":"))


NON_ASCII_STRING_MAP_PAYLOAD = _non_ascii_string_map_payload()
INT32_MAP_PAYLOAD = _int32_map_payload()


@pytest.mark.parametrize("_id", [pytest.param("1000-members", id="1000-members")])
@pytest.mark.benchmark(min_time=0.1, max_time=10)
def test_maps_from_json_non_ascii_string_map_1000_members(
    _id: str, benchmark: BenchmarkFixture
) -> None:
    first_key = "clé_0"
    last_key = f"clé_{MEMBER_COUNT - 1}"
    last_value = f"東京_{MEMBER_COUNT - 1}"

    def parse_and_validate() -> Maps:
        message = Maps.from_json(NON_ASCII_STRING_MAP_PAYLOAD)
        assert len(message.string_to_string) == MEMBER_COUNT
        assert message.string_to_string[first_key] == "東京_0"
        assert message.string_to_string[last_key] == last_value
        return message

    result = benchmark(parse_and_validate)

    assert len(result.string_to_string) == MEMBER_COUNT


@pytest.mark.parametrize("_id", [pytest.param("1000-members", id="1000-members")])
@pytest.mark.benchmark(min_time=0.1, max_time=10)
def test_maps_from_json_int32_map_1000_members(
    _id: str, benchmark: BenchmarkFixture
) -> None:
    last_key = MEMBER_COUNT - 1

    def parse_and_validate() -> Maps:
        message = Maps.from_json(INT32_MAP_PAYLOAD)
        assert len(message.int32_to_int32) == MEMBER_COUNT
        assert message.int32_to_int32[0] == 0
        assert message.int32_to_int32[last_key] == last_key
        return message

    result = benchmark(parse_and_validate)

    assert len(result.int32_to_int32) == MEMBER_COUNT
