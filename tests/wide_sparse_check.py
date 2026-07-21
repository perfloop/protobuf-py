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

from protobuf import Oneof
from tests.gen.messages_pb import MixedFields
from tests.gen.scalars_pb import Scalars
from tests.test_unknown_fields import (
    test_unknown_fields_dropped_on_write,
    test_unknown_fields_retained,
)
from tests.wide_sparse_serialization import (
    expected_wide_sparse_binary,
    make_wide_sparse_message,
)


def _check_descriptor_order_after_mutation() -> None:
    field_numbers = (30, 1, 15)
    selected_numbers = (30, 1, 15)
    message = make_wide_sparse_message(field_numbers, selected_numbers)
    assert message.to_binary() == expected_wide_sparse_binary(
        field_numbers, selected_numbers
    )

    message.clear_field("f_1")
    assert message.to_binary() == expected_wide_sparse_binary(field_numbers, (30, 15))


def _check_mixed_post_construction_mutations() -> None:
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


def _check_invalid_scalar_validation() -> None:
    try:
        Scalars(int32_field="invalid").to_binary()
    except TypeError:
        return
    raise AssertionError("to_binary accepted an invalid int32 value")


def main() -> None:
    _check_descriptor_order_after_mutation()
    print("CHECK wide_sparse_descriptor_order=ok")
    _check_mixed_post_construction_mutations()
    print("CHECK mixed_mutation_wire=ok")
    _check_invalid_scalar_validation()
    print("CHECK invalid_scalar_validation=ok")
    test_unknown_fields_retained()
    test_unknown_fields_dropped_on_write()
    print("CHECK unknown_field_round_trip=ok")


if __name__ == "__main__":
    main()
