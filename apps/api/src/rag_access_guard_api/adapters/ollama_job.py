"""Native containment prevents owned runners escaping a dead parent on Windows."""

import ctypes
from ctypes import wintypes
from typing import Final, Protocol, runtime_checkable

from rag_access_guard_api.schemas.generation import InferenceUnavailableError

_ALREADY_EXISTS: Final = 183
_MORE_DATA: Final = 234
_MAX_LIST_SIZE: Final = 65536


@runtime_checkable
class _Kernel(Protocol):
    def CreateJobObjectW(self, security: None, name: str) -> int: ...  # noqa: N802 -- Win32 ABI.
    def OpenJobObjectW(self, access: int, inherit: int, name: str) -> int: ...  # noqa: N802
    def OpenProcess(self, access: int, inherit: int, pid: int) -> int: ...  # noqa: N802
    def CloseHandle(self, handle: int) -> int: ...  # noqa: N802
    def SetHandleInformation(self, handle: int, mask: int, flags: int) -> int: ...  # noqa: N802
    def AssignProcessToJobObject(self, job: int, process: int) -> int: ...  # noqa: N802
    def IsProcessInJob(self, process: int, job: int, result: ctypes.Array[ctypes.c_int]) -> int: ...  # noqa: N802
    def TerminateJobObject(self, job: int, code: int) -> int: ...  # noqa: N802
    def QueryInformationJobObject(  # noqa: N802
        self,
        job: int,
        kind: int,
        info: ctypes.Array[ctypes.c_ulonglong],
        size: int,
        returned: None,
    ) -> int: ...


def _kernel() -> _Kernel:
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    for name in ("CreateJobObjectW", "OpenJobObjectW", "OpenProcess"):
        getattr(library, name).restype = wintypes.HANDLE
    library.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    library.OpenJobObjectW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    library.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    library.CloseHandle.argtypes = (wintypes.HANDLE,)
    library.SetHandleInformation.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
    library.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    library.IsProcessInJob.argtypes = (wintypes.HANDLE, wintypes.HANDLE, wintypes.LPVOID)
    library.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    library.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
    )
    return _checked_kernel(library)


def _checked_kernel(interface: object) -> _Kernel:
    match interface:
        case _Kernel() as kernel:
            return kernel
        case _:
            raise InferenceUnavailableError


class WindowsJob:
    """Reopen one randomly named owned job; closing CLI handles leaves processes alive."""

    def __init__(self, name: str, *, create: bool = False) -> None:
        """Open containment or create a collision-rejecting private name."""
        self.name: str = name
        self._kernel: _Kernel = _kernel()
        self._handle: int = (
            self._kernel.CreateJobObjectW(None, name)
            if create
            else self._kernel.OpenJobObjectW(0x001F003F, 0, name)
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if create and ctypes.get_last_error() == _ALREADY_EXISTS:
            self.close()
            raise FileExistsError

    def close(self) -> None:
        """Close this handle without terminating contained processes."""
        if self._handle:
            _ = self._kernel.CloseHandle(self._handle)
            self._handle = 0

    def inherited_handle(self) -> int:
        """Retain containment in the owned root after the starting CLI exits."""
        if not self._kernel.SetHandleInformation(self._handle, 1, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        return self._handle

    def assign(self, pid: int) -> None:
        """Contain the suspended root before it can spawn descendants."""
        process = self._kernel.OpenProcess(0x0101, 0, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _ = self._kernel.CloseHandle(process)

    def contains(self, pid: int) -> bool:
        """Confirm kernel membership before touching an identity-checked process."""
        process = self._kernel.OpenProcess(0x1000, 0, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        result = (ctypes.c_int * 1)()
        try:
            if not self._kernel.IsProcessInJob(process, self._handle, result):
                raise ctypes.WinError(ctypes.get_last_error())
            return bool(ctypes.c_int.from_buffer(result).value)
        finally:
            _ = self._kernel.CloseHandle(process)

    def pids(self) -> tuple[int, ...]:
        """Read the full active member list, rejecting truncation."""
        # DWORD counts followed by ULONG_PTR entries, with native pointer alignment.
        size = 64
        while size <= _MAX_LIST_SIZE:
            info = (ctypes.c_ulonglong * size)()
            if self._kernel.QueryInformationJobObject(
                self._handle, 3, info, ctypes.sizeof(info), None
            ):
                count = ctypes.c_uint32.from_buffer(info, 4).value
                return tuple(
                    ctypes.c_size_t.from_buffer(info, 8 + i * ctypes.sizeof(ctypes.c_size_t)).value
                    for i in range(count)
                )
            if ctypes.get_last_error() != _MORE_DATA:
                raise ctypes.WinError(ctypes.get_last_error())
            size *= 2
        raise OverflowError

    def terminate(self) -> None:
        """Request termination; callers must separately verify all exits."""
        if not self._kernel.TerminateJobObject(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
