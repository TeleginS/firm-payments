"""Parsing of the bulk payment request (ADR 0002: anything outside the format is Malformed).

Nothing here touches the database. Money is parsed from its decimal string straight to
integer cents.
"""

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

# Explicit [0-9] and fullmatch: `\d` accepts non-ASCII digits that int() converts, and `$`
# accepts a trailing newline.
AMOUNT = re.compile(r"[0-9]{1,10}(\.[0-9]{1,2})?")
FIRM_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
IDEMPOTENCY_KEY = re.compile(r"[\x21-\x7e]{1,255}")


class Malformed(Exception):
    """The request breaks the format. The message names the field, never its value."""


def _cents(value: object) -> int:
    if not isinstance(value, str) or not AMOUNT.fullmatch(value):
        raise ValueError('must be a string like "300", "5800.5" or "1200.75"')
    dollars, _, fraction = value.partition(".")
    cents = int(dollars) * 100 + int(fraction.ljust(2, "0") or "0")
    if cents == 0:
        raise ValueError("must be above zero")
    return cents


def _firm_uuid(value: object) -> str:
    if not isinstance(value, str) or not FIRM_UUID.fullmatch(value):
        raise ValueError("must be a UUID in the hyphenated 8-4-4-4-12 form")
    return value.lower()


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be only whitespace")
    return value


def _storable(value: str) -> str:
    if "\x00" in value:
        raise ValueError("must not contain a NUL character")
    return value


FirmUuid = Annotated[str, BeforeValidator(_firm_uuid)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PaymentRequest(_Strict):
    payee_firm_uuid: FirmUuid
    amount_cents: Annotated[int, BeforeValidator(_cents)] = Field(validation_alias="amount")
    description: Annotated[
        str,
        Field(min_length=1, max_length=1000),
        AfterValidator(_not_blank),
        AfterValidator(_storable),
    ]


class BulkPaymentRequest(_Strict):
    payer_firm_uuid: FirmUuid
    payments: Annotated[list[PaymentRequest], Field(min_length=1, max_length=1000)]


def parse_bulk_payment(body: bytes) -> BulkPaymentRequest:
    try:
        return BulkPaymentRequest.model_validate_json(body)
    except ValidationError as error:
        first = error.errors(include_url=False, include_input=False, include_context=False)[0]
        where = ".".join(str(part) for part in first["loc"]) or "body"
        raise Malformed(f"{where}: {first['msg']}") from None


def parse_idempotency_key(value: str | None) -> str | None:
    """The header's format (ADR 0003); a malformed key is Malformed, not ignored."""
    if value is not None and not IDEMPOTENCY_KEY.fullmatch(value):
        raise Malformed("Idempotency-Key: must be 1 to 255 visible ASCII characters, no spaces")
    return value
