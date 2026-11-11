"""Themes for the web UI (theme_spec.py, docs/THEME_SPEC.md): everyone reads them (with the CSS
they compile to, which the browser injects); admins add, change and remove custom ones.
Built-ins (themes/*.yaml) are read-only."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import models as m
from ..deps import DB, Admin, CurrentUser
from ..events import bus
from ..services import actor_of, record_activity
from ..theme_library import definition_from_spec, forget_theme
from ..theme_spec import ThemeSpec, compile_css, parse_theme_spec, stylesheets

router = APIRouter(prefix="/themes", tags=["themes"])


class ThemeYamlIn(BaseModel):
    yaml: str = Field(max_length=200_000)


class ThemeOut(BaseModel):
    id: str
    origin: str  # builtin | user
    name: str
    description: str
    modes: str  # both | dark | light
    colour: str  # palette | accent | fixed
    default_colour: str
    preview: list[str]
    # What the browser needs: the compiled CSS and the stylesheets (fonts) to load with it.
    css: str
    stylesheets: list[str]
    source_yaml: str = ""
    version: int = 1
    used_by: int = 0  # people who picked it
    spec: dict[str, Any] = Field(default_factory=dict)


def _theme_out(spec: ThemeSpec, origin: str = "user", source_yaml: str = "", version: int = 1, used_by: int = 0) -> ThemeOut:
    return ThemeOut(id=spec.id, origin=origin, name=spec.name, description=spec.description, modes=spec.modes,
                    colour=spec.colour, default_colour=spec.default_colour, preview=spec.preview_colours(),
                    css=compile_css(spec), stylesheets=stylesheets(spec), source_yaml=source_yaml, version=version,
                    used_by=used_by, spec=spec.model_dump(mode="json"))


async def _counts(db: DB) -> dict[str, int]:
    rows = await db.execute(select(m.User.theme, func.count()).where(m.User.theme != "").group_by(m.User.theme))
    return dict(rows.all())


def _out(row: m.ThemeDefinition, counts: dict[str, int]) -> ThemeOut:
    return _theme_out(ThemeSpec.model_validate(row.spec), row.origin, row.source_yaml, row.version, counts.get(row.id, 0))


def _parse_or_422(text: str) -> ThemeSpec:
    try:
        return parse_theme_spec(text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("", response_model=list[ThemeOut])
async def list_themes(db: DB, _: CurrentUser):
    """Every theme people can pick (Castor's own, "", isn't one: it's the default look)."""
    rows = (await db.execute(select(m.ThemeDefinition).order_by(m.ThemeDefinition.name))).scalars().all()
    counts = await _counts(db)
    out = []
    for r in rows:
        try:
            out.append(_out(r, counts))
        except ValueError:  # a stored spec from an older format: leave it out rather than fail
            continue
    return out


@router.get("/{theme_id}", response_model=ThemeOut)
async def get_theme(theme_id: str, db: DB, _: CurrentUser):
    row = await db.get(m.ThemeDefinition, theme_id)
    if row is None:
        raise HTTPException(404, "Theme not found")
    return _out(row, await _counts(db))


@router.post("/validate")
async def validate_theme(body: ThemeYamlIn, _: CurrentUser):
    """Check a theme and compile it, without saving: for the editor's live preview."""
    try:
        spec = parse_theme_spec(body.yaml)
    except ValueError as exc:
        return {"ok": False, "errors": str(exc).split("; "), "theme": None}
    return {"ok": True, "errors": [], "theme": _theme_out(spec, source_yaml=body.yaml)}


@router.post("", response_model=ThemeOut, status_code=201)
async def create_theme(body: ThemeYamlIn, db: DB, user: Admin):
    spec = _parse_or_422(body.yaml)
    if await db.get(m.ThemeDefinition, spec.id) is not None:
        raise HTTPException(409, f"A theme with id “{spec.id}” already exists")
    row = m.ThemeDefinition(id=spec.id, origin="user", version=1)
    definition_from_spec(row, spec, body.yaml)
    db.add(row)
    await record_activity(db, type="theme.created", message=f"Added the theme {spec.name}", project_id=None,
                          payload={"id": spec.id}, **actor_of(user))
    await db.commit()
    bus.publish("themes.changed", None, {"id": spec.id})
    return _out(row, await _counts(db))


@router.put("/{theme_id}", response_model=ThemeOut)
async def update_theme(theme_id: str, body: ThemeYamlIn, db: DB, user: Admin):
    row = await db.get(m.ThemeDefinition, theme_id)
    if row is None:
        raise HTTPException(404, "Theme not found")
    if row.origin == "builtin":
        raise HTTPException(409, "Built-in themes are read-only; duplicate it to customise")
    spec = _parse_or_422(body.yaml)
    if spec.id != theme_id:
        raise HTTPException(422, "id: cannot be changed (create a new theme instead)")
    definition_from_spec(row, spec, body.yaml)
    await record_activity(db, type="theme.updated", message=f"Changed the theme {spec.name}", project_id=None,
                          payload={"id": theme_id}, **actor_of(user))
    await db.commit()
    bus.publish("themes.changed", None, {"id": theme_id})
    return _out(row, await _counts(db))


@router.delete("/{theme_id}", status_code=204)
async def delete_theme(theme_id: str, db: DB, user: Admin):
    """Remove a custom theme; anyone using it goes back to Castor's own."""
    row = await db.get(m.ThemeDefinition, theme_id)
    if row is None:
        raise HTTPException(404, "Theme not found")
    if row.origin == "builtin":
        raise HTTPException(409, "Built-in themes cannot be deleted")
    await forget_theme(db, theme_id)
    await db.delete(row)
    await record_activity(db, type="theme.deleted", message=f"Removed the theme {row.name}", project_id=None,
                          payload={"id": theme_id}, **actor_of(user))
    await db.commit()
    bus.publish("themes.changed", None, {"id": theme_id})
