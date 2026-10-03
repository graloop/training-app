import time
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import ai_coach, models, schemas
from ..ai_providers import DEFAULT_PROVIDER, PROVIDERS
from ..config import get_settings
from ..database import get_db
from ..deps import get_current_user
from ..security import decrypt_text, encrypt_text

router = APIRouter(prefix="/api/ai-settings", tags=["ai-settings"])
settings = get_settings()


def _to_out(row: models.AISettings | None) -> schemas.AISettingsOut:
    provider = PROVIDERS[row.provider if row else DEFAULT_PROVIDER]
    hint = None
    if row and row.api_key_encrypted:
        try:
            hint = decrypt_text(row.api_key_encrypted)[-4:]
        except ValueError:
            hint = "????"
    has_key = hint is not None
    if row and row.model:
        model = row.model
    elif provider.id == "anthropic" and not has_key and settings.anthropic_api_key:
        model = settings.anthropic_model  # the server key comes with the server's model
    else:
        model = provider.default_model
    return schemas.AISettingsOut(
        provider=provider.id,
        model=model,
        custom_model=bool(row and row.model),
        has_api_key=has_key,
        api_key_hint=hint,
        using_server_key=not has_key and provider.id == "anthropic" and bool(settings.anthropic_api_key),
        base_url=row.base_url if row and provider.custom else None,
        api_format=(row.api_format or "openai") if row and provider.custom else None,
        providers=[
            schemas.AIProviderOut(
                id=p.id, label=p.label, default_model=p.default_model, key_url=p.key_url, custom=p.custom
            )
            for p in PROVIDERS.values()
        ],
    )


@router.get("", response_model=schemas.AISettingsOut)
def get_ai_settings(user: models.User = Depends(get_current_user)):
    return _to_out(user.ai_settings)


def _validate(payload: schemas.AISettingsUpdate):
    """(provider, model, base_url) from the form, or a 400 explaining what's missing."""
    if payload.provider not in PROVIDERS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown provider '{payload.provider}'")
    provider = PROVIDERS[payload.provider]
    model = (payload.model or "").strip() or None
    base_url = (payload.base_url or "").strip() or None

    if provider.custom:
        parsed = urlparse(base_url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom provider needs an API URL starting with https:// (e.g. https://example.com/v1)",
            )
        if model is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Custom provider needs a model name")
    return provider, model, base_url


@router.post("/test", response_model=schemas.AITestResult)
def test_ai_settings(payload: schemas.AISettingsUpdate, user: models.User = Depends(get_current_user)):
    """Check the server can reach the AI with the settings currently in the form
    (saved or not). Nothing is saved. An empty key field reuses the saved key
    when it belongs to the same provider/endpoint."""
    provider, model, base_url = _validate(payload)
    saved = user.ai_settings
    same_endpoint = (
        saved is not None
        and saved.provider == provider.id
        and (not provider.custom or saved.base_url == base_url)
    )
    draft = models.AISettings(
        provider=provider.id,
        model=model,
        base_url=base_url if provider.custom else None,
        api_format=(payload.api_format or "openai") if provider.custom else None,
        api_key_encrypted=saved.api_key_encrypted if same_endpoint and not payload.clear_api_key else None,
    )
    try:
        resolved, resolved_model, api_key = ai_coach.resolve_settings(draft, (payload.api_key or "").strip() or None)
        started = time.monotonic()
        reply = ai_coach.test_connection(resolved, resolved_model, api_key)
    except ai_coach.CoachError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return schemas.AITestResult(
        ok=True,
        provider=resolved.label,
        model=resolved_model,
        latency_ms=round((time.monotonic() - started) * 1000),
        reply=reply[:120],
    )


@router.put("", response_model=schemas.AISettingsOut)
def update_ai_settings(
    payload: schemas.AISettingsUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    provider, model, base_url = _validate(payload)

    row = user.ai_settings
    if row is None:
        row = models.AISettings(user_id=user.id, provider=payload.provider)
        db.add(row)
    elif row.provider != payload.provider or (provider.custom and row.base_url != base_url):
        # A key only works for the provider/endpoint it was issued by.
        row.api_key_encrypted = None
    row.provider = payload.provider
    row.model = model
    row.base_url = base_url if provider.custom else None
    row.api_format = (payload.api_format or "openai") if provider.custom else None

    new_key = (payload.api_key or "").strip()
    if payload.clear_api_key:
        row.api_key_encrypted = None
    elif new_key:
        row.api_key_encrypted = encrypt_text(new_key)

    db.commit()
    db.refresh(row)
    return _to_out(row)
