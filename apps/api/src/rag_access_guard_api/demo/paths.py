"""Pure private-path identity shared by bounded readers and protected writers."""

import stat
from pathlib import Path

from rag_access_guard_api.demo.errors import DemoOwnershipError
from rag_access_guard_api.services.demo_corpus import REPOSITORY_ROOT


def private_path(path: Path) -> Path:
    """Deny checkout intersection and symlink/reparse aliases before file operations."""
    try:
        target = path.absolute()
        resolved = target.resolve()
        if resolved.is_relative_to(REPOSITORY_ROOT) or REPOSITORY_ROOT.is_relative_to(resolved):
            raise DemoOwnershipError(code="private_path_in_checkout")
        for ancestor in (target, *target.parents):
            if ancestor.exists() or ancestor.is_symlink():
                metadata = ancestor.lstat()
                if stat.S_ISLNK(metadata.st_mode) or (
                    getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise DemoOwnershipError(code="private_path_alias")
    except (OSError, RuntimeError):
        raise DemoOwnershipError(code="private_path_unavailable") from None
    else:
        return resolved
