"""Read-only web UI: server-rendered FastAPI + Jinja2, dense UniFi-style.

Every route opens a short-lived Postgres connection (from `DATABASE_URL`) and
renders a template. There is no in-process background worker — ingestion and
sync run under systemd timers; this app only presents their results.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .db import connect
from .overlap import report

BASE = Path(__file__).parent

app = FastAPI(title="Recordkeeper", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["dt"] = lambda d, fmt="%Y-%m-%d %H:%M": (
    d.strftime(fmt) if d else "—"
)

NAV = [
    ("Dashboard", "/"),
    ("Scrobbles", "/scrobbles"),
    ("Playlists", "/playlists"),
    ("Overlap", "/overlap"),
    ("Support artists", "/support"),
    ("Vinyl", "/vinyl"),
]


def _url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return url


@app.exception_handler(RuntimeError)
async def _runtime_error(request: Request, exc: RuntimeError):
    return PlainTextResponse(f"database unavailable: {exc}", status_code=503)


def _ctx(request: Request, active: str, **extra) -> dict:
    ctx = {"request": request, "nav": NAV, "active": active}
    ctx.update(extra)
    return ctx


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with connect(_url()) as conn:
        counts = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM scrobbles)             AS scrobbles,
              (SELECT count(DISTINCT artist_name) FROM scrobbles) AS artists,
              (SELECT count(*) FROM tracks)                AS tracks,
              (SELECT count(*) FROM playlists)             AS playlists,
              (SELECT count(*) FROM saved_tracks)          AS saved_tracks,
              (SELECT count(*) FROM plex_items WHERE entity_type='artist') AS plex_artists,
              (SELECT count(*) FROM plex_items WHERE entity_type='album')  AS plex_albums,
              (SELECT count(*) FROM plex_items WHERE entity_type='track')  AS plex_tracks,
              (SELECT count(*) FROM vinyl_imports)         AS vinyl_imports,
              (SELECT count(*) FROM recommendations)       AS recommendations,
              (SELECT count(*) FROM loved_tracks)          AS loved_tracks
            """
        ).fetchone()
        recent = conn.execute(
            "SELECT artist_name, track_name, played_at FROM scrobbles "
            "ORDER BY played_at DESC LIMIT 12"
        ).fetchall()
        top = conn.execute(
            "SELECT artist_name, count(*) AS plays, max(played_at) AS last_played "
            "FROM scrobbles GROUP BY artist_name ORDER BY plays DESC LIMIT 12"
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _ctx(request, "Dashboard", counts=counts, recent=recent, top=top),
    )


@app.get("/scrobbles", response_class=HTMLResponse)
def scrobbles(request: Request):
    with connect(_url()) as conn:
        top = conn.execute(
            "SELECT artist_name, count(*) AS plays, max(played_at) AS last_played "
            "FROM scrobbles GROUP BY artist_name ORDER BY plays DESC LIMIT 50"
        ).fetchall()
        recent = conn.execute(
            "SELECT artist_name, track_name, album_name, played_at, source "
            "FROM scrobbles ORDER BY played_at DESC LIMIT 50"
        ).fetchall()
    return templates.TemplateResponse(
        request, "scrobbles.html", _ctx(request, "Scrobbles", top=top, recent=recent)
    )


@app.get("/playlists", response_class=HTMLResponse)
def playlists(request: Request):
    with connect(_url()) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.owner_name, p.is_public, p.snapshot_id,
                   COALESCE((
                     SELECT ps.track_count FROM playlist_snapshots ps
                     WHERE ps.playlist_id = p.id
                     ORDER BY ps.captured_at DESC LIMIT 1
                   ), 0) AS track_count
            FROM playlists p
            ORDER BY lower(p.name)
            """
        ).fetchall()
    return templates.TemplateResponse(
        request, "playlists.html", _ctx(request, "Playlists", rows=rows)
    )


@app.get("/overlap", response_class=HTMLResponse)
def overlap(request: Request, account_id: int | None = None):
    """Compare saved playlists for one explicitly selected Spotify account."""
    with connect(_url()) as conn:
        accounts = conn.execute(
            "SELECT id, username FROM accounts WHERE platform = 'spotify' ORDER BY id"
        ).fetchall()
        ids = {a["id"] for a in accounts}
        selected = (
            account_id
            if account_id in ids
            else (accounts[0]["id"] if accounts else None)
        )
        data = (
            report(conn, selected)
            if selected
            else {"pairs": [], "total": 0, "available": 0, "unavailable": []}
        )
    return templates.TemplateResponse(
        request,
        "overlap.html",
        _ctx(
            request,
            "Overlap",
            accounts=accounts,
            selected=selected,
            data=data,
            pairs=data["pairs"][:100],
        ),
    )


@app.get("/support", response_class=HTMLResponse)
def support(request: Request):
    with connect(_url()) as conn:
        rows = conn.execute(
            """
            SELECT a.name, r.score, r.status, r.reason, r.created_at
            FROM recommendations r
            JOIN artists a ON a.id = r.artist_id
            ORDER BY r.score DESC, lower(a.name)
            LIMIT 100
            """
        ).fetchall()
    return templates.TemplateResponse(
        request, "support.html", _ctx(request, "Support artists", rows=rows)
    )


@app.get("/vinyl", response_class=HTMLResponse)
def vinyl(request: Request):
    with connect(_url()) as conn:
        rows = conn.execute(
            """
            SELECT vi.id, vi.status, vi.track_count, vi.detected_at,
                   vi.raw->>'artist' AS artist, vi.raw->>'album' AS album,
                   vi.raw->>'added_at' AS added_at
            FROM vinyl_imports vi
            ORDER BY vi.detected_at DESC
            LIMIT 100
            """
        ).fetchall()
    return templates.TemplateResponse(
        request, "vinyl.html", _ctx(request, "Vinyl", rows=rows)
    )
