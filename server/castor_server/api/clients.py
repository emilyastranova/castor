from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from .. import models as m
from ..deps import DB, Admin, CurrentUser, Operator, parse_uuid
from ..schemas import ClientIn, ClientOut

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=list[ClientOut])
async def list_clients(db: DB, _: CurrentUser):
    return (await db.execute(select(m.Client).order_by(m.Client.name))).scalars().all()


async def _check_name(db: DB, name: str, other_than: m.Client | None = None) -> str:
    """A client's name, trimmed: required and not another client's (whatever its case)."""
    name = name.strip()
    if not name:
        raise HTTPException(422, "name: a client needs a name")
    same = (await db.execute(select(m.Client).where(func.lower(m.Client.name) == name.lower()))).scalars().first()
    if same is not None and same is not other_than:
        raise HTTPException(409, f"A client named {same.name} already exists")
    return name


@router.post("", response_model=ClientOut, status_code=201)
async def create_client(body: ClientIn, db: DB, _: Operator):
    client = m.Client(name=await _check_name(db, body.name), notes=body.notes)
    db.add(client)
    await db.commit()
    return client


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(client_id: str, body: ClientIn, db: DB, _: Operator):
    client = await db.get(m.Client, parse_uuid(client_id))
    if client is None:
        raise HTTPException(404, "Client not found")
    client.name = await _check_name(db, body.name, client)
    client.notes = body.notes
    await db.commit()
    return client


@router.delete("/{client_id}", status_code=204)
async def delete_client(client_id: str, db: DB, _: Admin):
    client = await db.get(m.Client, parse_uuid(client_id))
    if client is None:
        raise HTTPException(404, "Client not found")
    await db.delete(client)
    await db.commit()
