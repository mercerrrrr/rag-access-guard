"""Create a user through a trusted local operator workflow."""

import getpass
from typing import Annotated, Final
from uuid import UUID

import anyio
import psycopg
import typer
from sqlalchemy.exc import IntegrityError

from rag_access_guard_api.config import Settings
from rag_access_guard_api.database import create_database_engine
from rag_access_guard_api.server import create_event_loop
from rag_access_guard_api.services.passwords import hash_password
from rag_access_guard_api.services.security import lock_policy
from rag_access_guard_api.services.user_creation import CreateUserRequest, insert_user_record

MAX_LOGIN_CHARACTERS: Final = 254
MAX_DISPLAY_NAME_CHARACTERS: Final = 200

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


class DuplicateLoginError(Exception):
    """The normalized login is already present."""


async def create_user_record(request: CreateUserRequest) -> UUID:
    """Atomically insert the user, audit event, and policy revision."""
    engine = create_database_engine(Settings())
    try:
        try:
            async with engine.begin() as connection:
                _ = await lock_policy(connection, exclusive=True)
                user_id = await insert_user_record(connection, request)
        except IntegrityError as error:
            if (
                isinstance(error.orig, psycopg.Error)
                and error.orig.sqlstate == "23505"
                and error.orig.diag.constraint_name == "uq_users_login"
            ):
                raise DuplicateLoginError from error
            raise
    finally:
        await engine.dispose()
    return user_id


@app.command()
def create_user(
    login: Annotated[str, typer.Option(help="Unique account login.")],
    display_name: Annotated[str, typer.Option(help="Display name.")],
    *,
    admin: Annotated[bool, typer.Option(help="Create an administrator.")] = False,
) -> None:
    """Create one account after securely prompting for its password."""
    normalized_login = login.strip().lower()
    normalized_display_name = display_name.strip()
    if (
        not normalized_login
        or "\x00" in normalized_login
        or len(normalized_login) > MAX_LOGIN_CHARACTERS
    ):
        typer.echo("Login must contain 1 to 254 valid characters", err=True)
        raise typer.Exit(code=2)
    if (
        not normalized_display_name
        or "\x00" in normalized_display_name
        or len(normalized_display_name) > MAX_DISPLAY_NAME_CHARACTERS
    ):
        typer.echo("Display name must contain 1 to 200 valid characters", err=True)
        raise typer.Exit(code=2)

    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        typer.echo("Passwords do not match", err=True)
        raise typer.Exit(code=1)
    try:
        password_hash = hash_password(password)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    request = CreateUserRequest(
        login=normalized_login,
        display_name=normalized_display_name,
        password_hash=password_hash,
        is_admin=admin,
    )
    try:
        user_id = anyio.run(
            create_user_record,
            request,
            backend="asyncio",
            backend_options={"loop_factory": create_event_loop},
        )
    except DuplicateLoginError as error:
        typer.echo(f"Login already exists: {normalized_login}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"{user_id} {normalized_login}")


def main() -> None:
    """Run the create-user command."""
    app()


if __name__ == "__main__":
    main()
