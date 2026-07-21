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

import cProfile
import pstats

from tests.wide_sparse_serialization import (
    expected_wide_sparse_binary,
    make_wide_sparse_message,
)

_FIELD_COUNT = 1_000
_ITERATIONS = 12


def _call_count(stats: pstats.Stats, function_name: str) -> int:
    return sum(
        calls
        for (filename, _line, name), (calls, _primitive_calls, _self, _cumulative, _callers) in stats.stats.items()
        if filename.endswith(("_message.py", "_to_binary.py", "_validate.py"))
        and name == function_name
    )


def main() -> None:
    field_numbers = range(1, _FIELD_COUNT + 1)
    message = make_wide_sparse_message(field_numbers, (1,))
    expected = expected_wide_sparse_binary(field_numbers, (1,))
    assert message.to_binary() == expected

    def serialize() -> None:
        for _ in range(_ITERATIONS):
            assert message.to_binary() == expected

    profile = cProfile.Profile()
    profile.runcall(serialize)
    stats = pstats.Stats(profile)
    contains_member_calls = _call_count(stats, "_contains_member")
    validate_calls = _call_count(stats, "validate")
    write_message_calls = _call_count(stats, "write_message")

    assert contains_member_calls >= _FIELD_COUNT * _ITERATIONS
    assert validate_calls == _ITERATIONS
    assert write_message_calls == _ITERATIONS
    print("PROFILE workload=wide-explicit-f1000-s1")
    print(f"PROFILE contains_member_calls={contains_member_calls}")
    print(f"PROFILE validate_calls={validate_calls}")
    print(f"PROFILE write_message_calls={write_message_calls}")
    print(f"PROFILE payload={expected.hex()}")


if __name__ == "__main__":
    main()
