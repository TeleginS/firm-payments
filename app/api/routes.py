from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.errors import error_response
from app.api.schemas import parse_bulk_payment, parse_idempotency_key
from app.payments import BulkPayment, Denied, Executed, Payment, PaymentService, Replayed

router = APIRouter()


async def raw_body(request: Request) -> bytes:
    """The body is parsed by `parse_bulk_payment`, so every format error is one Malformed."""
    return await request.body()


def payment_service(request: Request) -> PaymentService:
    service: PaymentService = request.app.state.payments
    return service


@router.post("/bulk_payments")
def bulk_payments(
    request: Request,
    body: Annotated[bytes, Depends(raw_body)],
    service: Annotated[PaymentService, Depends(payment_service)],
) -> JSONResponse:
    parsed = parse_bulk_payment(body)
    key = parse_idempotency_key(request.headers.get("Idempotency-Key"))

    outcome = service.execute(
        BulkPayment(
            parsed.payer_firm_uuid,
            tuple(
                Payment(p.payee_firm_uuid, p.amount_cents, p.description) for p in parsed.payments
            ),
            key,
        )
    )
    match outcome:
        case Executed(payment_ids):
            return JSONResponse({"payment_ids": list(payment_ids)}, status_code=201)
        case Replayed(payment_ids):
            return JSONResponse(
                {"payment_ids": list(payment_ids)},
                status_code=201,
                headers={"Idempotent-Replayed": "true"},
            )
        case Denied("self_payment"):
            return error_response(422, "self_payment", "A payment names the payer as its payee.")
        case Denied("unknown_firm", uuids):
            return error_response(
                422,
                "unknown_firm",
                "A firm in the request is not on the platform.",
                unknown_firm_uuids=list(uuids),
            )
        case Denied("idempotency_key_reused"):
            return error_response(
                422, "idempotency_key_reused", "The Idempotency-Key was used for another request."
            )
        case Denied():
            return error_response(422, "request_denied", "The request was denied.")
