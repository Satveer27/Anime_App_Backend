from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

BCRYPT_MAX_BYTES = 72


def _within_bcrypt_limit(value: str) -> str:
    # bcrypt rejects >72 bytes, and multi-byte characters mean max_length (characters) is not enough
    if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password is too long: at most {BCRYPT_MAX_BYTES} bytes "
            "(some characters, such as emoji or accents, use more than one byte)"
        )
    return value


Password = Annotated[str, AfterValidator(_within_bcrypt_limit)]


class SuccessMessage(BaseModel):
    success_message: str = Field(..., description="The success message")
