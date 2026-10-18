"""Win64 creation-time containment for short-lived embedding workers."""

import ctypes
import os
import subprocess
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import BinaryIO, Final, Protocol, runtime_checkable

_WAIT_TIMEOUT: Final = 258
_WIN64_POINTER_BYTES: Final = 8
_WORKER_LABEL: Final = "embedding worker"


@runtime_checkable
class _Kernel(Protocol):
    def InitializeProcThreadAttributeList(  # noqa: N802 -- Win32 ABI.
        self,
        attributes: ctypes.Array[ctypes.c_char] | None,
        count: int,
        flags: int,
        size: ctypes.Array[ctypes.c_size_t],
    ) -> int: ...
    def UpdateProcThreadAttribute(  # noqa: N802, PLR0913, PLR0917 -- Exact Win32 ABI.
        self,
        attributes: ctypes.Array[ctypes.c_char],
        flags: int,
        kind: int,
        value: ctypes.Array[ctypes.c_void_p],
        size: int,
        previous: None,
        returned: None,
    ) -> int: ...
    def DeleteProcThreadAttributeList(self, attributes: ctypes.Array[ctypes.c_char]) -> None: ...  # noqa: N802
    def CreateProcessW(  # noqa: N802, PLR0913, PLR0917 -- Exact Win32 ABI.
        self,
        application: str,
        command: ctypes.Array[ctypes.c_wchar],
        process_security: None,
        thread_security: None,
        inherit: int,
        flags: int,
        environment: None,
        directory: None,
        startup: ctypes.Array[ctypes.c_ulonglong],
        process: ctypes.Array[ctypes.c_ulonglong],
    ) -> int: ...
    def CloseHandle(self, handle: int) -> int: ...  # noqa: N802
    def WaitForSingleObject(self, handle: int, milliseconds: int) -> int: ...  # noqa: N802
    def GetExitCodeProcess(self, handle: int, code: ctypes.Array[ctypes.c_ulong]) -> int: ...  # noqa: N802
    def TerminateProcess(self, handle: int, code: int) -> int: ...  # noqa: N802


def _kernel() -> _Kernel:
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.InitializeProcThreadAttributeList.argtypes = (
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    library.UpdateProcThreadAttribute.argtypes = (
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    library.DeleteProcThreadAttributeList.argtypes = (wintypes.LPVOID,)
    library.DeleteProcThreadAttributeList.restype = None
    library.CreateProcessW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
    )
    library.CloseHandle.argtypes = (wintypes.HANDLE,)
    library.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    library.WaitForSingleObject.restype = wintypes.DWORD
    library.GetExitCodeProcess.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
    library.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    return _checked_kernel(library)


def _checked_kernel(interface: object) -> _Kernel:
    match interface:
        case _Kernel() as kernel:
            return kernel
        case _:
            raise OSError


@contextmanager
def _attributes(
    kernel: _Kernel, job: int, handles: tuple[int, int, int]
) -> Generator[ctypes.Array[ctypes.c_char]]:
    size = (ctypes.c_size_t * 1)()
    _ = kernel.InitializeProcThreadAttributeList(None, 2, 0, size)
    attributes = ctypes.create_string_buffer(ctypes.c_size_t.from_buffer(size).value)
    if not kernel.InitializeProcThreadAttributeList(attributes, 2, 0, size):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        job_list = (ctypes.c_void_p * 1)(job)
        handle_list = (ctypes.c_void_p * 3)(*handles)
        for kind, value in ((0x2000D, job_list), (0x20002, handle_list)):
            if not kernel.UpdateProcThreadAttribute(
                attributes, 0, kind, value, ctypes.sizeof(value), None, None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        yield attributes
    finally:
        kernel.DeleteProcThreadAttributeList(attributes)


@dataclass(frozen=True, slots=True)
class EmbeddingChild:
    """Keep a native process handle until physical cleanup, independent of PID reuse."""

    pid: int
    handle: int = field(repr=False)
    stdin: BinaryIO = field(repr=False)
    stdout: BinaryIO = field(repr=False)
    kernel: _Kernel = field(repr=False)

    def poll(self) -> int | None:
        """Read termination through the retained native handle."""
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result == _WAIT_TIMEOUT:
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = (ctypes.c_ulong * 1)()
        if not self.kernel.GetExitCodeProcess(self.handle, code):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.c_ulong.from_buffer(code).value

    def wait(self, timeout: float) -> int:
        """Bound the wait without releasing the process handle."""
        result = self.kernel.WaitForSingleObject(self.handle, max(0, int(timeout * 1000)))
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(_WORKER_LABEL, timeout)
        code = self.poll()
        if code is None:
            raise OSError
        return code

    def kill(self) -> None:
        """Terminate only this retained native process."""
        if not self.kernel.TerminateProcess(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        """Release transport and process handles after lifecycle confirmation."""
        try:
            self.stdin.close()
        finally:
            try:
                self.stdout.close()
            finally:
                _ = self.kernel.CloseHandle(self.handle)


def spawn_contained(command: tuple[str, ...], job: int) -> EmbeddingChild:
    """Atomically create a suspended child inside the non-inherited private job."""
    import msvcrt  # noqa: PLC0415 -- Windows-only adapter; module remains importable on Linux.

    if ctypes.sizeof(ctypes.c_void_p) != _WIN64_POINTER_BYTES:
        raise OSError
    kernel = _kernel()
    with ExitStack() as child_ends, ExitStack() as parent_ends:
        input_read, input_write = os.pipe()
        child_input = child_ends.enter_context(os.fdopen(input_read, "rb", buffering=0))
        parent_input = parent_ends.enter_context(os.fdopen(input_write, "wb"))
        output_read, output_write = os.pipe()
        child_output = child_ends.enter_context(os.fdopen(output_write, "wb", buffering=0))
        parent_output = parent_ends.enter_context(os.fdopen(output_read, "rb"))
        child_error = child_ends.enter_context(open(os.devnull, "wb", buffering=0))  # noqa: PTH123 -- OS sink.
        streams = (child_input, child_output, child_error)
        handles = (
            msvcrt.get_osfhandle(child_input.fileno()),
            msvcrt.get_osfhandle(child_output.fileno()),
            msvcrt.get_osfhandle(child_error.fileno()),
        )
        for stream in streams:
            os.set_inheritable(stream.fileno(), True)  # noqa: FBT003 -- Positional-only OS API.
        with _attributes(kernel, job, handles) as attributes:
            # Win64 STARTUPINFOEXW: 112 bytes; fields follow the Windows SDK layout.
            startup = (ctypes.c_ulonglong * 14)()
            ctypes.c_uint32.from_buffer(startup, 0).value = ctypes.sizeof(startup)
            ctypes.c_uint32.from_buffer(startup, 60).value = 0x100
            for offset, handle in zip((80, 88, 96), handles, strict=True):
                ctypes.c_size_t.from_buffer(startup, offset).value = handle
            ctypes.c_void_p.from_buffer(startup, 104).value = ctypes.addressof(attributes)
            process = (ctypes.c_ulonglong * 3)()
            native_command = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            if not kernel.CreateProcessW(
                command[0],
                native_command,
                None,
                None,
                1,
                0x08080004,
                None,
                None,
                startup,
                process,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        process_handle = ctypes.c_size_t.from_buffer(process, 0).value
        thread_handle = ctypes.c_size_t.from_buffer(process, 8).value
        pid = ctypes.c_uint32.from_buffer(process, 16).value
        _ = kernel.CloseHandle(thread_handle)
        child = EmbeddingChild(pid, process_handle, parent_input, parent_output, kernel)
        _ = parent_ends.pop_all()
        return child
