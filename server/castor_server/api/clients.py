from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from .. import models as m
from ..deps import DB, CurrentUser, parse_uuid
from ..schemas import ClientIn, ClientOut

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=list[ClientOut])
async def list_clients(db: DB, _: CurrentUser):
    return (await db.execute(select(m.Client).order_by(m.Client.name))).scalars().all()


@router.post("", response_model=ClientOut, status_code=201)
async def create_client(body: ClientIn, db: DB, _: CurrentUser):
    client = m.Client(name=body.name, notes=body.notes)
    db.add(client)
    await db.commit()
    return client


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(client_id: str, body: ClientIn, db: DB, _: CurrentUser):
    client = await db.get(m.Client, parse_uuid(client_id))
    if client is None:
        raise HTTPException(404, "Client not found")
    client.name = body.name
    client.notes = body.notes
    await db.commit()
    return client


@router.delete("/{client_id}", status_code=204)
async def delete_client(client_id: str, db: DB, _: CurrentUser):
    client = await db.get(m.Client, parse_uuid(client_id))
    if client is None:
        raise HTTPException(404, "Client not found")
    await db.delete(client)
    await db.commit()
