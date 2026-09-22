from argon2 import extract_parameters
from argon2.low_level import Type

from rag_access_guard_api.services import passwords


def test_argon2id_parameters_are_explicit() -> None:
    encoded = passwords.hash_password("Synthetic-Pass-123")
    parameters = extract_parameters(encoded)
    assert (
        parameters.type,
        parameters.memory_cost,
        parameters.time_cost,
        parameters.parallelism,
        parameters.salt_len,
        parameters.hash_len,
    ) == (Type.ID, 65536, 3, 4, 16, 32)
    assert passwords.verify_password("Synthetic-Pass-123", encoded)
    assert not passwords.verify_password(" Synthetic-Pass-123", encoded)
