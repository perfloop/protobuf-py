use std::sync::{
    Condvar, Mutex,
    atomic::{AtomicBool, Ordering},
};

use bytes::Bytes;
use pyo3::{
    Bound, IntoPyObject as _, IntoPyObjectExt as _, Py, PyAny, PyResult, Python,
    exceptions::{PyAttributeError, PyTypeError},
    pyclass, pyfunction, pymethods,
    sync::PyOnceLock,
    types::{
        PyAnyMethods as _, PyBytes, PyBytesMethods as _, PyDict, PyDictMethods as _, PyList,
        PyListMethods as _, PyString, PyStringMethods as _, PyTuple, PyType, PyTypeMethods as _,
    },
};

use crate::{
    attribute_access::{generic_getattr, generic_setattr},
    bitset::BitSet,
    constants::Constants,
    marshaler::{Member, MessageMarshaler},
    oneof::Oneof,
};

enum LazyMergeState {
    Inactive,
    Ready(Py<PyBytes>),
    Materializing,
}

/// Base class inserted into Python message types to provide accelerated functions.
///
/// Messages match definitions on the Python Message class. By having priority
/// in method resolution order, the accelerated definitions are used instead of
/// pure Python.
#[pyclass(subclass, frozen, weakref, module = "protobuf_ext")]
pub(super) struct NativeMessage {
    /// Pointer to the marshaler for this message type. Only missing for bootstraps protos.
    marshaler: Option<MessageMarshaler>,

    present: BitSet,
    unknown_fields: PyOnceLock<Py<PyDict>>,
    // A pre-merge field access can hand a caller an empty list, dict, or unknown-field
    // dictionary. Such a target must populate those retained containers before merge returns.
    mutable_state_exposed: AtomicBool,
    // A wire snapshot makes a merge into an empty target independent from later source changes
    // without allocating the target's complete descendant tree. The mutex elects exactly one
    // materializer while other concurrent readers wait for a complete message state.
    lazy_merge: Mutex<LazyMergeState>,
    lazy_merge_ready: Condvar,
}

#[pymethods]
impl NativeMessage {
    #[new]
    #[classmethod]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(
        cls: &Bound<'_, PyType>,
        py: Python<'_>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
        let marshaler_any = cls.getattr_opt(&Constants::get(py)?.ext_marshaler)?;
        let marshaler = if let Some(marshaler_any) = &marshaler_any {
            Some(marshaler_any.cast::<MessageMarshaler>()?.get().clone())
        } else {
            None
        };
        let present = if let Some(marshaler_any) = &marshaler_any {
            let marshaler = marshaler_any.cast::<MessageMarshaler>()?;
            BitSet::new(marshaler.get().max_field_number)
        } else {
            // Don't know the maximum field number yet. This only happens for bootstrap protos.
            BitSet::new(u32::MAX)
        };
        Ok(Self {
            marshaler,
            present,
            unknown_fields: PyOnceLock::new(),
            mutable_state_exposed: AtomicBool::new(false),
            lazy_merge: Mutex::new(LazyMergeState::Inactive),
            lazy_merge_ready: Condvar::new(),
        })
    }

    #[pyo3(signature = (**kwargs))]
    fn __init__(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<()> {
        let marshaler = NativeMessage::get_marshaler(slf)?;
        marshaler.fill_empty_message(py, slf)?;
        if let Some(kwargs) = kwargs {
            slf.get()
                .mutable_state_exposed
                .store(true, Ordering::Release);
            for (key, value) in kwargs {
                if value.is_none() {
                    continue;
                }
                if let Some(member_any) = marshaler.members_by_name.bind(py).get_item(&key)? {
                    // SAFETY - members is a private dict which we only insert Member into.
                    let member = unsafe { member_any.cast_unchecked::<Member>() }.get();
                    member.attr.set(slf, &value)?;
                    if let Some(field_number) = member.field_number {
                        slf.get().add_present_field(field_number);
                    }
                } else {
                    return Err(PyTypeError::new_err(format!(
                        "{}.__init__() got an unexpected keyword argument '{}'",
                        slf.get_type().qualname()?,
                        key
                    )));
                }
            }
        }
        Ok(())
    }

    #[classmethod]
    #[pyo3(signature = (data, *, ignore_unknown_fields = false))]
    fn from_binary<'py>(
        cls: &Bound<'py, PyType>,
        py: Python<'py>,
        data: Bytes,
        ignore_unknown_fields: bool,
    ) -> PyResult<Bound<'py, NativeMessage>> {
        let constants = Constants::get(py)?;
        let marshaler_any = cls.getattr(&constants.ext_marshaler)?;
        let marshaler = marshaler_any.cast::<MessageMarshaler>()?.get();
        let message = marshaler.new_empty_message(py, cls)?;
        let slf = message.cast::<NativeMessage>()?;
        marshaler.merge_from_binary(py, slf, data, ignore_unknown_fields)?;
        Ok(message)
    }

    #[pyo3(signature = (*, write_unknown_fields = true))]
    fn to_binary<'py>(
        slf: &Bound<'_, Self>,
        py: Python<'py>,
        write_unknown_fields: bool,
    ) -> PyResult<Bound<'py, PyBytes>> {
        if write_unknown_fields && let Some(data) = NativeMessage::lazy_merge_data(slf, py)? {
            return Ok(data.bind(py).clone());
        }
        NativeMessage::materialize_lazy_merge(slf, py)?;
        NativeMessage::get_marshaler(slf)?.to_binary(py, slf, write_unknown_fields)
    }

    fn __getattribute__<'py>(
        slf: &Bound<'py, Self>,
        py: Python<'py>,
        name: &Bound<'_, PyString>,
    ) -> PyResult<Bound<'py, PyAny>> {
        if slf.get().marshaler.is_none() {
            return generic_getattr(slf, name);
        }
        let marshaler = NativeMessage::get_marshaler(slf)?;
        if let Some(member_any) = marshaler.members_by_name.bind(py).get_item(name)? {
            slf.get().mark_mutable_state_exposed();
            NativeMessage::materialize_lazy_merge(slf, py)?;
            // SAFETY - members is a private dict which we only insert Member into.
            let member = unsafe { member_any.cast_unchecked::<Member>() }.get();
            // Retrieve through the original slot descriptor so normal field reads do not pay for
            // the wrapper that exists solely for object.__getattribute__ callers.
            return member.attr.get(py, slf);
        }
        let name_str = name.to_str()?;
        if matches!(
            name_str,
            "_unknown_fields"
                | "_present"
                | "_get_or_init_unknown_fields"
                | "_get_field_number_present"
                | "_set_field_number_present"
                | "_clear_field_number_present"
        ) {
            NativeMessage::materialize_lazy_merge(slf, py)?;
        }
        generic_getattr(slf, name)
    }

    fn __getattr__<'py>(slf: &Bound<'py, Self>, name: &str) -> PyResult<Bound<'py, PyAny>> {
        Err(PyAttributeError::new_err(format!(
            "'{}' object has no attribute '{}'",
            slf.get_type().name()?,
            name
        )))
    }

    fn __setattr__(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
        name: &Bound<'_, PyString>,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let marshaler = NativeMessage::get_marshaler(slf)?;

        if let Some(member_any) = marshaler.members_by_name.bind(py).get_item(name)? {
            slf.get()
                .mutable_state_exposed
                .store(true, Ordering::Release);
            NativeMessage::materialize_lazy_merge(slf, py)?;
            // SAFETY - members is a private dict which we only insert Member into.
            let member = unsafe { member_any.cast_unchecked::<Member>() }.get();
            member.attr.set(slf, value)?;
            if let Some(field_number) = member.field_number {
                slf.get().add_present_field(field_number);
            }
        } else {
            // Not a known field. Delegate to Python for error message.
            generic_setattr(slf, name, value)?;
        }

        Ok(())
    }

    fn __copy__<'py>(
        slf: &Bound<'py, Self>,
        py: Python<'py>,
    ) -> PyResult<Bound<'py, NativeMessage>> {
        NativeMessage::materialize_lazy_merge(slf, py)?;
        let marshaler = NativeMessage::get_marshaler(slf)?;

        let new = marshaler.new_unset_message(&slf.get_type())?;
        for member in &marshaler.members {
            let value = member.attr.get(py, slf)?;
            member.attr.set(&new, &value)?;
        }

        new.get().present.set_all(&slf.get().present);
        copy_unknown_fields(py, &new, slf)?;

        Ok(new)
    }

    fn _merge_from_binary(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
        data: Bytes,
        ignore_unknown_fields: bool,
    ) -> PyResult<()> {
        NativeMessage::materialize_lazy_merge(slf, py)?;
        NativeMessage::get_marshaler(slf)?.merge_from_binary(py, slf, data, ignore_unknown_fields)
    }

    fn __deepcopy__<'py>(
        slf: &Bound<'py, Self>,
        py: Python<'py>,
        _memo: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Bound<'py, Self>> {
        NativeMessage::materialize_lazy_merge(slf, py)?;
        let marshaler = NativeMessage::get_marshaler(slf)?;

        let new = marshaler.new_unset_message(&slf.get_type())?;
        for member in &marshaler.members {
            let value = member.attr.get(py, slf)?;
            let value = if let Ok(value_message) = value.cast::<NativeMessage>() {
                NativeMessage::__deepcopy__(value_message, py, None)?.into_any()
            } else if let Ok(value_list) = value.cast::<PyList>() {
                let list = PyList::empty(py);
                deepcopy_list(py, &list, value_list)?;
                list.into_any()
            } else if let Ok(value_dict) = value.cast::<PyDict>() {
                let dict = PyDict::new(py);
                deepcopy_dict(py, &dict, value_dict)?;
                dict.into_any()
            } else if let Ok(oneof) = value.cast::<Oneof>() {
                deepcopy_oneof(py, oneof)?
            } else {
                value
            };
            member.attr.set(&new, &value)?;
        }
        new.get().present.set_all(&slf.get().present);
        copy_unknown_fields(py, &new, slf)?;

        Ok(new)
    }

    fn _merge_from(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
        source: &Bound<'_, Self>,
        ignore_unknown_fields: bool,
    ) -> PyResult<()> {
        let marshaler = NativeMessage::get_marshaler(slf)?;
        if !source.is_instance(marshaler.python_type.bind(py))? {
            return Err(PyTypeError::new_err(format!(
                "invalid source message type, expected {}, got {}",
                marshaler.python_type.bind(py).qualname()?,
                source.get_type().qualname()?
            )));
        }
        if NativeMessage::can_defer_merge(slf, py)? {
            let data = NativeMessage::serialize_for_lazy_merge(source, py, ignore_unknown_fields)?;
            let needs_root_materialization =
                slf.get().mutable_state_exposed.load(Ordering::Acquire);
            slf.get().set_lazy_merge_data(py, &data)?;
            if needs_root_materialization {
                // Populate containers that escaped before merge; descendants created by this parse
                // retain their own snapshots until a field descriptor exposes them.
                NativeMessage::materialize_lazy_merge(slf, py)?;
            }
            return Ok(());
        }
        NativeMessage::materialize_lazy_merge(slf, py)?;
        NativeMessage::materialize_lazy_merge(source, py)?;
        let source_present = &source.get().present;
        slf.get().present.set_all(source_present);

        for member in &marshaler.members {
            if let Some(field_number) = member.field_number
                && !source_present.get(field_number)
            {
                continue;
            }

            let source_value = member.attr.get(py, source)?;
            if source_value.is_none() {
                continue;
            }

            // Unlike in Python, we inspect value types directly without using descriptor information. While there
            // may be some performance benefit to not having to lookup marshalers on every message copy, especially
            // for containers, we don't know if the user gave a correct message and risk segfaults if doing that
            // instead of duck-typing errors.
            if let Ok(source_value_message) = source_value.cast::<NativeMessage>() {
                let target_value_any = member.attr.get(py, slf)?;
                let target_value_message = if target_value_any.is_none() {
                    let source_value_marshaler =
                        NativeMessage::get_marshaler(source_value_message)?;
                    let target_value = source_value_marshaler
                        .new_empty_message(py, &source_value_message.get_type())?;
                    member.attr.set(slf, &target_value)?;
                    target_value
                } else {
                    target_value_any.cast_into::<NativeMessage>()?
                };
                NativeMessage::_merge_from(
                    &target_value_message,
                    py,
                    source_value_message,
                    ignore_unknown_fields,
                )?;
            } else if let Ok(source_value_list) = source_value.cast::<PyList>() {
                let target_value_any = member.attr.get(py, slf)?;
                let target_value_list = target_value_any.cast::<PyList>()?;
                deepcopy_list(py, target_value_list, source_value_list)?;
            } else if let Ok(source_value_dict) = source_value.cast::<PyDict>() {
                let target_value_any = member.attr.get(py, slf)?;
                let target_value_dict = target_value_any.cast::<PyDict>()?;
                deepcopy_dict(py, target_value_dict, source_value_dict)?;
            } else if let Ok(oneof) = source_value.cast::<Oneof>() {
                let value = deepcopy_oneof(py, oneof)?;
                member.attr.set(slf, &value)?;
            } else {
                member.attr.set(slf, &source_value)?;
            }
        }

        if !ignore_unknown_fields
            && let Some(source_unknown_fields_unbound) = source.get().unknown_fields.get(py)
        {
            let source_unknown_fields = source_unknown_fields_unbound.bind(py);
            let self_unknown_fields_unbound = slf.get().get_or_init_unknown_fields_internal(py);
            let self_unknown_fields = self_unknown_fields_unbound.bind(py);
            for (key, value) in source_unknown_fields {
                let list = if let Some(existing) = self_unknown_fields.get_item(&key)? {
                    existing.cast_into::<PyList>()?
                } else {
                    let new_list = PyList::empty(py);
                    self_unknown_fields.set_item(key, &new_list)?;
                    new_list
                };
                for item in value.cast::<PyList>()? {
                    list.append(item)?;
                }
            }
        }

        Ok(())
    }

    fn _get_field_number_present(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
        field_number: u32,
    ) -> PyResult<bool> {
        NativeMessage::materialize_lazy_merge(slf, py)?;
        Ok(slf.get().present.get(field_number))
    }

    fn _set_field_number_present(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
        field_number: u32,
    ) -> PyResult<()> {
        NativeMessage::materialize_lazy_merge(slf, py)?;
        slf.get().present.set(field_number, true);
        Ok(())
    }

    fn _clear_field_number_present(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
        field_number: u32,
    ) -> PyResult<()> {
        NativeMessage::materialize_lazy_merge(slf, py)?;
        slf.get().present.set(field_number, false);
        Ok(())
    }

    #[getter]
    fn _present<'py>(slf: &Bound<'py, Self>, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        NativeMessage::materialize_lazy_merge(slf, py)?;
        let list = PyList::empty(py);
        slf.get().present.for_each(|i| list.append(i))?;
        Ok(list)
    }

    #[pyo3(name = "_get_or_init_unknown_fields")]
    fn get_or_init_unknown_fields_public(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
    ) -> PyResult<Py<PyDict>> {
        slf.get().mark_mutable_state_exposed();
        NativeMessage::materialize_lazy_merge(slf, py)?;
        Ok(slf.get().get_or_init_unknown_fields_internal(py))
    }

    #[getter(_unknown_fields)]
    fn unknown_fields_public(
        slf: &Bound<'_, Self>,
        py: Python<'_>,
    ) -> PyResult<Option<Py<PyDict>>> {
        slf.get().mark_mutable_state_exposed();
        NativeMessage::materialize_lazy_merge(slf, py)?;
        Ok(slf.get().unknown_fields_internal(py))
    }
}

impl NativeMessage {
    pub(super) fn add_present_field(&self, field_number: u32) {
        self.present.set(field_number, true);
    }

    pub(super) fn has_present_field(&self, field_number: u32) -> bool {
        self.present.get(field_number)
    }

    pub(crate) fn mark_mutable_state_exposed(&self) {
        self.mutable_state_exposed.store(true, Ordering::Release);
    }

    pub(super) fn get_or_init_unknown_fields_internal(&self, py: Python<'_>) -> Py<PyDict> {
        self.unknown_fields
            .get_or_init(py, || PyDict::new(py).unbind())
            .clone_ref(py)
    }

    pub(super) fn unknown_fields_internal(&self, py: Python<'_>) -> Option<Py<PyDict>> {
        self.unknown_fields.get(py).map(|v| v.clone_ref(py))
    }

    /// Returns the deferred wire snapshot, waiting for an in-progress materialization.
    pub(crate) fn lazy_merge_data<'py>(
        slf: &Bound<'py, Self>,
        py: Python<'py>,
    ) -> PyResult<Option<Py<PyBytes>>> {
        let mut state = slf
            .get()
            .lazy_merge
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        loop {
            match &*state {
                LazyMergeState::Inactive => return Ok(None),
                LazyMergeState::Ready(data) => return Ok(Some(data.clone_ref(py))),
                LazyMergeState::Materializing => {
                    state = slf
                        .get()
                        .lazy_merge_ready
                        .wait(state)
                        .unwrap_or_else(|poison| poison.into_inner());
                }
            }
        }
    }

    /// Saves wire bytes for a merge that can defer building target field values.
    pub(crate) fn set_lazy_merge_data(
        &self,
        _py: Python<'_>,
        data: &Bound<'_, PyBytes>,
    ) -> PyResult<()> {
        let mut state = self
            .lazy_merge
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        while matches!(*state, LazyMergeState::Materializing) {
            state = self
                .lazy_merge_ready
                .wait(state)
                .unwrap_or_else(|poison| poison.into_inner());
        }
        *state = LazyMergeState::Ready(data.clone().unbind());
        Ok(())
    }

    /// Parses a deferred snapshot before an operation exposes or changes mutable state.
    pub(crate) fn materialize_lazy_merge(slf: &Bound<'_, Self>, py: Python<'_>) -> PyResult<()> {
        let data = {
            let mut state = slf
                .get()
                .lazy_merge
                .lock()
                .unwrap_or_else(|poison| poison.into_inner());
            loop {
                match &*state {
                    LazyMergeState::Inactive => return Ok(()),
                    LazyMergeState::Ready(data) => {
                        let data = data.clone_ref(py);
                        *state = LazyMergeState::Materializing;
                        break data;
                    }
                    LazyMergeState::Materializing => {
                        state = slf
                            .get()
                            .lazy_merge_ready
                            .wait(state)
                            .unwrap_or_else(|poison| poison.into_inner());
                    }
                }
            }
        };
        let bytes = Bytes::copy_from_slice(data.bind(py).as_bytes());
        let result = NativeMessage::get_marshaler(slf)
            .and_then(|marshaler| marshaler.materialize_lazy_merge(py, slf, bytes));
        let mut state = slf
            .get()
            .lazy_merge
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        *state = if result.is_ok() {
            LazyMergeState::Inactive
        } else {
            LazyMergeState::Ready(data)
        };
        slf.get().lazy_merge_ready.notify_all();
        result
    }

    /// Defers only a wire-empty target, for which source wire bytes exactly represent merge output.
    fn can_defer_merge(slf: &Bound<'_, Self>, py: Python<'_>) -> PyResult<bool> {
        if NativeMessage::lazy_merge_data(slf, py)?.is_some() {
            return Ok(false);
        }
        Ok(NativeMessage::get_marshaler(slf)?
            .to_binary(py, slf, true)?
            .as_bytes()
            .is_empty())
    }

    /// Serializes a source at the ownership boundary, preserving its state at merge time.
    fn serialize_for_lazy_merge<'py>(
        source: &Bound<'py, Self>,
        py: Python<'py>,
        ignore_unknown_fields: bool,
    ) -> PyResult<Bound<'py, PyBytes>> {
        if !ignore_unknown_fields && let Some(data) = NativeMessage::lazy_merge_data(source, py)? {
            return Ok(data.bind(py).clone());
        }
        NativeMessage::materialize_lazy_merge(source, py)?;
        NativeMessage::get_marshaler(source)?.to_binary(py, source, !ignore_unknown_fields)
    }

    fn get_marshaler(slf: &Bound<'_, Self>) -> PyResult<MessageMarshaler> {
        if let Some(marshaler) = &slf.get().marshaler {
            Ok(marshaler.clone())
        } else {
            Ok(
                generic_getattr(slf, Constants::get(slf.py())?.ext_marshaler.bind(slf.py()))?
                    .cast::<MessageMarshaler>()?
                    .get()
                    .clone(),
            )
        }
    }
}

#[pyfunction]
pub(super) fn materialize_lazy_message(message: &Bound<'_, PyAny>) -> PyResult<()> {
    let message = message.cast::<NativeMessage>()?;
    message.get().mark_mutable_state_exposed();
    NativeMessage::materialize_lazy_merge(message, message.py())
}

#[pyfunction]
pub(super) fn initialize_message_type(
    py: Python<'_>,
    message_type: &Bound<'_, PyType>,
) -> PyResult<()> {
    let constants = Constants::get(py)?;
    if message_type
        .getattr_opt(&constants.ext_marshaler)?
        .is_some()
    {
        return Ok(());
    }

    let constants = Constants::get(py)?;
    let message_desc = message_type.getattr(&constants.desc)?;
    let marshaler = MessageMarshaler::new(py, &message_desc, &constants)?.into_pyobject(py)?;
    message_type.setattr(&constants.ext_marshaler, marshaler)?;
    Ok(())
}

fn deepcopy_list(
    py: Python<'_>,
    target: &Bound<'_, PyList>,
    source: &Bound<'_, PyList>,
) -> PyResult<()> {
    for item in source {
        if let Ok(message) = item.cast::<NativeMessage>() {
            let copied = NativeMessage::__deepcopy__(message, py, None)?;
            target.append(copied)?;
        } else {
            target.append(item)?;
        }
    }
    Ok(())
}

fn deepcopy_dict(
    py: Python<'_>,
    target: &Bound<'_, PyDict>,
    source: &Bound<'_, PyDict>,
) -> PyResult<()> {
    for (key, value) in source {
        if let Ok(message) = value.cast::<NativeMessage>() {
            let copied = NativeMessage::__deepcopy__(message, py, None)?;
            target.set_item(key, copied)?;
        } else {
            target.set_item(key, value)?;
        }
    }
    Ok(())
}

fn deepcopy_oneof<'py>(
    py: Python<'py>,
    source_value: &Bound<'py, Oneof>,
) -> PyResult<Bound<'py, PyAny>> {
    let value = source_value.get().value.bind(py);
    if let Ok(value_message) = value.cast::<NativeMessage>() {
        let field = source_value.get().field.bind(py);
        let copied = NativeMessage::__deepcopy__(value_message, py, None)?;
        Oneof::new(field, &copied.into_any()).into_bound_py_any(py)
    } else {
        Ok(source_value.clone().into_any())
    }
}

fn copy_unknown_fields(
    py: Python<'_>,
    target: &Bound<'_, NativeMessage>,
    source: &Bound<'_, NativeMessage>,
) -> PyResult<()> {
    if let Some(source_unknown_fields) = source.get().unknown_fields.get(py) {
        let target_unknown_fields_unbound = target.get().get_or_init_unknown_fields_internal(py);
        let target_unknown_fields = target_unknown_fields_unbound.bind(py);
        for (key, value) in source_unknown_fields.bind(py) {
            let list = value.cast::<PyList>()?;
            target_unknown_fields.set_item(key, PyList::new(py, list)?)?;
        }
    }
    Ok(())
}
