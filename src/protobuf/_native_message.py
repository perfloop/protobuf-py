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

from typing import Any


class _LazyFieldDescriptor:
    """Materializes a native message before exposing one of its slot values."""

    __slots__ = ("_slot",)

    def __init__(self, slot: Any) -> None:
        self._slot = slot

    def __get__(self, instance: Any, owner: type[Any] | None = None) -> Any:
        if instance is None:
            return self
        materialize_lazy_message(instance)
        return self._slot.__get__(instance, owner)

    def __set__(self, instance: Any, value: Any) -> None:
        materialize_lazy_message(instance)
        self._slot.__set__(instance, value)

    def __delete__(self, instance: Any) -> None:
        materialize_lazy_message(instance)
        self._slot.__delete__(instance)


try:
    from protobuf_ext import NativeMessage, generic_setattr, materialize_lazy_message

    NativeMessageClass = NativeMessage
    # Workaround Python <3.13 prevents calling object.__setattr__ on objects with
    # a native base class that implements __setattr__.
    object_setattr = generic_setattr
except ImportError:
    NativeMessageClass = None
    object_setattr = object.__setattr__


def install_lazy_field_descriptors(message_type: type[Any]) -> None:
    """Wrap generated slots so ``object.__getattribute__`` observes lazy state too."""
    if NativeMessageClass is None:
        return
    for name in message_type.__slots__:
        slot = vars(message_type).get(name)
        if slot is not None and not isinstance(slot, _LazyFieldDescriptor):
            setattr(message_type, name, _LazyFieldDescriptor(slot))
