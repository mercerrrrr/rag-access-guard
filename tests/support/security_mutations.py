from collections.abc import Callable
from typing import TYPE_CHECKING

from tests.support.chat import ChatHttp
from tests.support.chat_generation import ChatCase

from rag_access_guard_api.schemas.access import GrantView

if TYPE_CHECKING:
    from httpx2 import Response


def prepare_mutation(case: ChatCase, change: str, role_grant: GrantView) -> Callable[[], None]:
    headers = ChatHttp.csrf(case.client)
    grants = f"/api/admin/documents/{case.document.id}/grants"
    if change == "membership":
        case.revoke()
    elif change != "membership_with_direct":
        assert case.client.delete(f"{grants}/{role_grant.id}", headers=headers).status_code == 204

    operations: dict[str, tuple[Callable[[], Response], int]] = {
        "direct": (lambda: case.client.delete(f"{grants}/{case.grant.id}", headers=headers), 204),
        "membership": (
            lambda: case.client.delete(
                f"/api/admin/roles/{role_grant.role_id}/members/{case.grant.user_id}",
                headers=headers,
            ),
            204,
        ),
        "membership_with_direct": (
            lambda: case.client.delete(
                f"/api/admin/roles/{role_grant.role_id}/members/{case.grant.user_id}",
                headers=headers,
            ),
            204,
        ),
        "inactive_user": (
            lambda: case.client.patch(
                f"/api/admin/users/{case.grant.user_id}", json={"is_active": False}, headers=headers
            ),
            200,
        ),
        "logout": (lambda: case.client.post("/api/auth/logout", headers=headers), 204),
        "inactive_document": (
            lambda: case.client.patch(
                f"/api/admin/documents/{case.document.id}",
                json={"is_active": False},
                headers=headers,
            ),
            200,
        ),
        "active_version": (
            lambda: case.client.post(
                f"/api/admin/documents/{case.document.id}/versions",
                files={"file": ("new.txt", b"NEW_VERSION_MARKER", "text/plain")},
                headers=headers,
            ),
            201,
        ),
    }
    operation, status = operations[change]

    def mutate() -> None:
        response = operation()
        assert response.status_code == status, response.text

    return mutate
