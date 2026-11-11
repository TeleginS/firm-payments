import json

import pytest

from app.api.schemas import Malformed, parse_bulk_payment, parse_idempotency_key

PAYER = "3F1C9A2E-7B4D-4C1E-9A55-2D8E6F0B7C41"
PAYEE = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"


def payment(**fields: object) -> dict[str, object]:
    return {"amount": "10", "payee_firm_uuid": PAYEE, "description": "Invoice 1"} | fields


def body(**fields: object) -> bytes:
    request = {"payer_firm_uuid": PAYER, "payments": [payment()]} | fields
    return json.dumps(request).encode()


def test_a_valid_request_parses_with_lower_case_uuids_and_integer_cents() -> None:
    request = parse_bulk_payment(
        body(
            payments=[
                payment(amount="6250", description="  x  "),
                payment(amount="5800.5"),
                payment(amount="1200.75", payee_firm_uuid=PAYEE.upper()),
            ]
        )
    )

    assert request.payer_firm_uuid == PAYER.lower()
    assert [(p.payee_firm_uuid, p.amount_cents, p.description) for p in request.payments] == [
        (PAYEE, 625000, "  x  "),
        (PAYEE, 580050, "Invoice 1"),
        (PAYEE, 120075, "Invoice 1"),
    ]


@pytest.mark.parametrize(
    ("amount", "cents"),
    [("0.01", 1), ("1", 100), ("300", 30000), ("300.00", 30000), ("9999999999.99", 999999999999)],
)
def test_amounts_become_cents(amount: str, cents: int) -> None:
    parsed = parse_bulk_payment(body(payments=[payment(amount=amount)]))

    assert parsed.payments[0].amount_cents == cents


@pytest.mark.parametrize(
    "amount",
    [
        "0", "0.00", "-5", "1e3", " 5", "5 ", "1.234", "1.", ".5", "+5", "12345678901",
        "300\n", "٣٠٠", "１۲۰۰", "", 300, 300.5, None, True, ["5"],
    ],
)  # fmt: skip
def test_a_bad_amount_is_malformed(amount: object) -> None:
    with pytest.raises(Malformed):
        parse_bulk_payment(body(payments=[payment(amount=amount)]))


@pytest.mark.parametrize(
    "description", [" ", "\t", "\r", "\n", " \t\r\n ", "", "x" * 1001, 5, None]
)
def test_a_bad_description_is_malformed(description: object) -> None:
    with pytest.raises(Malformed):
        parse_bulk_payment(body(payments=[payment(description=description)]))


def test_a_description_of_1000_characters_parses() -> None:
    parsed = parse_bulk_payment(body(payments=[payment(description="x" * 1000)]))

    assert len(parsed.payments[0].description) == 1000


@pytest.mark.parametrize(
    "request_body",
    [
        body(extra=1),
        body(payments=[payment(extra=1)]),
        json.dumps({"payments": [payment()]}).encode(),
        json.dumps({"payer_firm_uuid": PAYER}).encode(),
        body(payments=[{"payee_firm_uuid": PAYEE, "description": "x"}]),
        body(payments=[{"amount": "1", "description": "x"}]),
        body(payments=[{"amount": "1", "payee_firm_uuid": PAYEE}]),
        body(payments=[]),
        body(payments=[payment()] * 1001),
        body(payments=None),
        body(payments=payment()),
        b"",
        b"{not json",
        b"[]",
        b"[" + body() + b"]",
        b"null",
        b'"text"',
        b"\xff\xfe",
    ],
)
def test_a_request_outside_the_format_is_malformed(request_body: bytes) -> None:
    with pytest.raises(Malformed):
        parse_bulk_payment(request_body)


def test_1000_payments_parse() -> None:
    assert len(parse_bulk_payment(body(payments=[payment()] * 1000)).payments) == 1000


@pytest.mark.parametrize(
    "uuid",
    [
        "3f1c9a2e7b4d4c1e9a552d8e6f0b7c41",
        "{3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41}",
        "urn:uuid:3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
        "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41\n",
        " 3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
        "\uff13f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
        "not-a-uuid",
        "",
        None,
        5,
    ],
)
def test_a_firm_uuid_outside_the_hyphenated_form_is_malformed(uuid: object) -> None:
    with pytest.raises(Malformed):
        parse_bulk_payment(body(payer_firm_uuid=uuid))
    with pytest.raises(Malformed):
        parse_bulk_payment(body(payments=[payment(payee_firm_uuid=uuid)]))


def test_the_same_payee_may_appear_twice_and_the_payer_may_be_named() -> None:
    # Self-payment is Denied, not Malformed: the service decides it.
    parsed = parse_bulk_payment(body(payments=[payment(), payment(payee_firm_uuid=PAYER)]))

    assert len(parsed.payments) == 2


def test_the_message_names_the_field_and_not_its_value() -> None:
    with pytest.raises(Malformed) as error:
        parse_bulk_payment(body(payments=[payment(amount="9.999")]))

    assert str(error.value).startswith("payments.0.amount: ")
    assert "9.999" not in str(error.value)


@pytest.mark.parametrize(
    "key", [None, "a", "sample-1", "x" * 255, "~!#$%&'()*+,-./:;<=>?@[]^_`{|}"]
)
def test_a_valid_idempotency_key_passes(key: str | None) -> None:
    assert parse_idempotency_key(key) == key


@pytest.mark.parametrize("key", ["", " ", "a b", "x" * 256, "tab\there", "caf\xe9", "k\n", "日本"])
def test_a_bad_idempotency_key_is_malformed(key: str) -> None:
    with pytest.raises(Malformed):
        parse_idempotency_key(key)
