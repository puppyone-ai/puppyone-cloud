import hmac
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.config import settings
from src.platform.auth.dependencies import get_current_user
from src.platform.auth.models import CurrentUser
from src.platform.billing.gateway import BillingGatewayError, PuppyPayGateway, get_billing_gateway
from src.platform.managed_ai.contracts import InferenceError
from src.platform.managed_ai.dependencies import get_inference_service
from src.platform.managed_ai.protocol import InferenceRoute, log_validation_failure
from src.platform.managed_ai.response import completion_response
from src.platform.managed_ai.schemas import CheckoutRequest, CompletionRequest, TrialClaimRequest
from src.platform.managed_ai.service import ManagedAIService


def require_enabled():
    if not settings.MANAGED_AI_ENABLED:
        raise HTTPException(404, "Agent credits are not enabled")


def require_person(current: CurrentUser = Depends(get_current_user)):
    if settings.SKIP_AUTH or current.is_anonymous or current.role != "authenticated":
        raise HTTPException(401, "Email sign-in is required for the Agent")
    return current


def key(value: str | None):
    if not value or not re.fullmatch(r"[a-zA-Z0-9_:-]{16,128}", value):
        raise HTTPException(400, "A unique Idempotency-Key of 16 to 128 characters is required")
    return value


async def call(gateway, method, path, **kwargs):
    try:
        return await gateway.request(method, path, **kwargs)
    except BillingGatewayError as error:
        return JSONResponse(error.payload, status_code=error.status_code)


router = APIRouter(prefix="/ai", tags=["desktop-ai"], dependencies=[Depends(require_enabled)])
inference_router = APIRouter(route_class=InferenceRoute)
internal_router = APIRouter(
    prefix="/internal/ai", tags=["desktop-ai-internal"], dependencies=[Depends(require_enabled)]
)


@router.get("/catalog")
async def catalog(gateway: PuppyPayGateway = Depends(get_billing_gateway)):
    return await call(gateway, "GET", "/api/v1/ai/catalog")


@router.get("/balance")
async def balance(
    user=Depends(require_person), gateway: PuppyPayGateway = Depends(get_billing_gateway)
):
    return await call(gateway, "GET", "/api/v1/ai/balance", actor_user_id=user.user_id)


@router.post("/trial")
async def claim_trial(
    body: TrialClaimRequest,
    user=Depends(require_person),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
):
    if not user.email:
        raise HTTPException(403, "Email sign-in is required")
    return await call(
        gateway,
        "POST",
        "/api/v1/ai/trial",
        actor_user_id=user.user_id,
        actor_email=user.email,
        body={},
    )


@router.post("/checkouts")
async def checkout(
    body: CheckoutRequest,
    idempotency_key: str | None = Header(default=None),
    user=Depends(require_person),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
):
    return await call(
        gateway,
        "POST",
        "/api/v1/ai/checkouts",
        actor_user_id=user.user_id,
        actor_email=user.email,
        idempotency_key=key(idempotency_key),
        body=body.model_dump(),
    )


@router.get("/purchases/{purchase_id}")
async def purchase(
    purchase_id: UUID,
    user=Depends(require_person),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
):
    return await call(
        gateway, "GET", f"/api/v1/ai/purchases/{purchase_id}", actor_user_id=user.user_id
    )


@router.get("/usage/{reservation_id}")
async def usage_receipt(
    reservation_id: UUID,
    user=Depends(require_person),
    gateway: PuppyPayGateway = Depends(get_billing_gateway),
):
    return await call(
        gateway, "GET", f"/api/v1/ai/usage/{reservation_id}", actor_user_id=user.user_id
    )


@inference_router.post("/chat/completions")
async def completion(
    request: Request,
    idempotency_key: str | None = Header(default=None),
    user=Depends(require_person),
    service: ManagedAIService = Depends(get_inference_service),
):
    request_id = key(idempotency_key)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 512 * 1024:
            raise HTTPException(413, "Agent request is too large")
    try:
        body = CompletionRequest.model_validate_json(raw)
    except ValidationError as error:
        # Validation details may contain private prompts. Do not echo them.
        log_validation_failure(error)
        raise HTTPException(422, "Invalid text/tool inference request") from None
    try:
        return completion_response(await service.completion(user.user_id, request_id, body))
    except (BillingGatewayError, InferenceError) as error:
        return JSONResponse(error.payload, status_code=error.status_code)


router.include_router(inference_router)


@internal_router.get("/generations/{generation_id}")
async def generation(
    generation_id: str,
    user_id: str,
    provider: str = "openrouter",
    provider_account_ref: str = "managed-default",
    x_internal_secret: str = Header(default=""),
    service: ManagedAIService = Depends(get_inference_service),
):
    if not settings.INTERNAL_API_SECRET or not hmac.compare_digest(
        x_internal_secret, settings.INTERNAL_API_SECRET
    ):
        raise HTTPException(403, "Invalid service credential")
    try:
        # Defaults only preserve old Pay Worker requests during a rolling update.
        return await service.recover_usage(
            generation_id, user_id, provider=provider, provider_account_ref=provider_account_ref
        )
    except (BillingGatewayError, InferenceError) as error:
        return JSONResponse(error.payload, status_code=error.status_code)
