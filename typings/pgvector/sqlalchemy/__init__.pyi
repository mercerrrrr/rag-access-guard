from sqlalchemy.types import UserDefinedType

class Vector(UserDefinedType[list[float]]):
    def __init__(self, dim: int) -> None: ...
