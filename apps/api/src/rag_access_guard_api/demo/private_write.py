"""Restrict permissions before secrets and atomically publish without replacement."""

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Final
from uuid import uuid4

from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.demo.paths import private_path

_WINDOWS_ACL: Final = """$ErrorActionPreference='Stop'
$p=$env:RAG_DEMO_PRIVATE_PATH
$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
if ($env:RAG_DEMO_SET_ACL -eq '1') {
 if (Test-Path -LiteralPath $p -PathType Container) {
  $acl=New-Object System.Security.AccessControl.DirectorySecurity
  $rule=New-Object System.Security.AccessControl.FileSystemAccessRule(
   $sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
 } else {
  $acl=New-Object System.Security.AccessControl.FileSecurity
  $rule=New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','Allow')
 }
 $acl.SetOwner($sid); $acl.SetAccessRuleProtection($true,$false)
 $acl.AddAccessRule($rule); Set-Acl -LiteralPath $p -AclObject $acl -ErrorAction Stop
}
$acl=Get-Acl -LiteralPath $p -ErrorAction Stop
$rules=@($acl.Access)
if ($acl.Owner -ne $sid.Translate([System.Security.Principal.NTAccount]).Value -or
 $rules.Count -ne 1 -or $rules[0].IsInherited -or
 $rules[0].IdentityReference.Translate(
  [System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value -or
 $rules[0].AccessControlType -ne 'Allow' -or -not $acl.AreAccessRulesProtected) { exit 1 }
"""


def owner_only(path: Path, *, initialize: bool = False) -> None:
    """Windows enforces one current-user ACL; POSIX requires current owner and 0600."""
    if os.name == "nt":
        environment = os.environ.copy()
        _ = environment.pop("PSMODULEPATH", None)
        environment["RAG_DEMO_PRIVATE_PATH"] = str(path)
        environment["RAG_DEMO_SET_ACL"] = "1" if initialize else "0"
        executable = (
            Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        try:
            result = subprocess.run(  # noqa: S603 -- fixed native ACL script; no shell/secret argv
                [
                    str(executable.resolve(strict=True)),
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _WINDOWS_ACL,
                ],
                env=environment,
                capture_output=True,
                check=False,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except (OSError, subprocess.SubprocessError):
            raise DemoOwnershipError(code="credential_acl_unavailable") from None
        if result.returncode:
            raise DemoOwnershipError(code="credential_acl_unavailable")
    else:
        if initialize:
            path.chmod(0o700 if path.is_dir() else 0o600)
        metadata = path.stat()
        expected = 0o700 if path.is_dir() else 0o600
        if stat.S_IMODE(metadata.st_mode) != expected or metadata.st_uid != os.getuid():
            raise DemoOwnershipError(code="credential_permissions")


def private_directory(path: Path) -> Path:
    """Protect a designated secret child without tightening an existing unrelated directory."""
    target = private_path(path)
    try:
        target.mkdir()
    except FileExistsError:
        owner_only(target)
    except OSError:
        raise DemoOwnershipError(code="private_directory_unavailable") from None
    else:
        owner_only(target, initialize=True)
    return target


def exclusive_private_write(path: Path, data: bytes) -> None:
    """Fsync a permission-restricted sibling, then publish with no overwrite operation."""
    target = private_path(path)
    if target.exists() or not target.parent.is_dir():
        raise DemoOwnershipError(code="private_output_conflict")
    owner_only(target.parent)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.pending")
    try:
        with temporary.open("xb") as stream:
            owner_only(temporary, initialize=True)
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            _ = temporary.rename(target)
        else:
            os.link(temporary, target)
            temporary.unlink()
    except OSError:
        raise DemoOwnershipError(code="private_output_unavailable") from None
    finally:
        if temporary.exists():
            temporary.unlink()
