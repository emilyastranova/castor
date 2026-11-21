"""Company branding (branding.py): the instance's name and logos (public: the sign-in page shows
them), and each client's logo."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from .. import branding
from .. import models as m
from ..deps import DB, Admin, CurrentUser, Operator, parse_uuid
from ..events import bus
from ..schemas import ClientOut
from ..services import actor_of, record_activity

router = APIRouter(tags=["branding"])
Kind = Literal["mark", "mark_dark", "wordmark", "wordmark_dark"]
WORDS = {"mark": "square logo", "mark_dark": "square logo for dark themes", "wordmark": "wordmark",
         "wordmark_dark": "wordmark for dark themes"}


def _logo(path: Any) -> Response:
    return Response(path.read_bytes(), media_type=branding.content_type(path.name),
                    headers={**branding.HEADERS, "Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/branding")
async def read_branding(db: DB) -> dict[str, Any]:
    """The instance's name (empty: Castor's own), tagline and logo addresses. Public."""
    return await branding.public(db)


@router.get("/branding/logo/{kind}")
async def branding_logo(kind: Kind, db: DB):
    """One of the instance's logos. Public (the sign-in page shows it)."""
    path = branding.file_path((await branding.logos(db)).get(kind))
    if path is None:
        raise HTTPException(404, "No logo")
    return _logo(path)


@router.put("/branding/logo/{kind}")
async def upload_branding_logo(kind: Kind, db: DB, user: Admin, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """A logo (SVG, PNG, WebP, JPEG or GIF, up to 512 KB): `mark` is square (the navigation rail,
    the browser tab, the installed app), `wordmark` horizontal (the sign-in page, exports); the
    `_dark` ones are for dark themes (optional)."""
    try:
        name = branding.save(kind, await file.read(branding.MAX_BYTES + 1))
    except branding.BrandingError as exc:
        raise HTTPException(422, str(exc)) from exc
    await branding.set_logo(db, kind, name, user.id)
    await record_activity(db, type="branding.changed", message=f"Uploaded the company {WORDS[kind]}", project_id=None,
                          payload={"kind": kind}, **actor_of(user))
    await db.commit()
    out = await branding.public(db)
    bus.publish("branding.changed", None, out)
    return out


@router.delete("/branding/logo/{kind}")
async def delete_branding_logo(kind: Kind, db: DB, user: Admin) -> dict[str, Any]:
    await branding.set_logo(db, kind, None, user.id)
    await record_activity(db, type="branding.changed", message=f"Removed the company {WORDS[kind]}", project_id=None,
                          payload={"kind": kind}, **actor_of(user))
    await db.commit()
    out = await branding.public(db)
    bus.publish("branding.changed", None, out)
    return out


# ---------- clients ----------

async def _client(db, client_id: str) -> m.Client:
    client = await db.get(m.Client, parse_uuid(client_id))
    if client is None:
        raise HTTPException(404, "Client not found")
    return client


@router.get("/clients/{client_id}/logo")
async def client_logo(client_id: str, db: DB, _: CurrentUser, variant: Literal["light", "dark"] = "light"):
    client = await _client(db, client_id)
    path = branding.file_path(client.logo_dark_file if variant == "dark" else client.logo_file)
    if path is None:
        raise HTTPException(404, "No logo")
    return _logo(path)


@router.put("/clients/{client_id}/logo", response_model=ClientOut)
async def upload_client_logo(client_id: str, db: DB, user: Operator, file: Annotated[UploadFile, File()],
                             variant: Literal["light", "dark"] = "light"):
    """The client's logo, on its projects' exports (`dark`: for dark themes, optional)."""
    client = await _client(db, client_id)
    try:
        name = branding.save(f"client-{variant}", await file.read(branding.MAX_BYTES + 1))
    except branding.BrandingError as exc:
        raise HTTPException(422, str(exc)) from exc
    attr = "logo_dark_file" if variant == "dark" else "logo_file"
    branding.remove(getattr(client, attr))
    setattr(client, attr, name)
    await record_activity(db, type="branding.client_logo", message=f"Uploaded a logo for the client {client.name}", project_id=None,
                          payload={"client_id": str(client.id)}, **actor_of(user))
    await db.commit()
    return ClientOut.model_validate(client)


@router.delete("/clients/{client_id}/logo", response_model=ClientOut)
async def delete_client_logo(client_id: str, db: DB, user: Operator, variant: Literal["light", "dark"] = "light"):
    client = await _client(db, client_id)
    attr = "logo_dark_file" if variant == "dark" else "logo_file"
    branding.remove(getattr(client, attr))
    setattr(client, attr, None)
    await record_activity(db, type="branding.client_logo", message=f"Removed the logo of the client {client.name}", project_id=None,
                          payload={"client_id": str(client.id)}, **actor_of(user))
    await db.commit()
    return ClientOut.model_validate(client)
