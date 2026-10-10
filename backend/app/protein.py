"""Protein journal routes (SPEC §4.7).

Both routes are idempotent so the phone can retry them freely after a bad connection:
  PUT    /protein/{client_id}  create the line, or replace it if it already exists
  DELETE /protein/{client_id}  remove the line; 204 whether or not it was still there
Deleting is a DELETE: the line really disappears, but its id is remembered in protein_tombstone.
A PUT for a deleted id (a retry that was already on its way) is answered 200 and ignored, so
the phone drops it from its queue and the line never comes back.
"""

import datetime as dt

from fastapi import APIRouter, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import DbSession
from app.models import ProteinEntry, ProteinTombstone
from app.schemas import ClientId, ProteinSyncIn, SyncOut

router = APIRouter(prefix="/protein", tags=["protein"])


@router.put("/{client_id}")
def sync_protein(client_id: ClientId, body: ProteinSyncIn, db: DbSession, response: Response) -> SyncOut:
    """Offline sync: store (or correct) one journal line made on the phone."""
    key = str(client_id)
    if db.get(ProteinTombstone, key) is not None:
        response.status_code = 200
        return SyncOut(id=0, created=False)  # deleted earlier: nothing to store, nothing to retry
    for attempt in (1, 2):
        row = db.scalars(select(ProteinEntry).where(ProteinEntry.client_id == key)).first()
        created = row is None
        if created:
            row = ProteinEntry(client_id=key)
            db.add(row)
        row.date = body.date
        row.label = body.label
        row.grams = body.grams
        row.created_at = body.created_at
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()  # same client_id inserted at once by another request: retry as update
            if not created or attempt == 2:
                raise

    response.status_code = 201 if created else 200
    return SyncOut(id=row.id, created=created)


@router.delete("/{client_id}", status_code=204)
def delete_protein(client_id: ClientId, db: DbSession) -> Response:
    """Remove a journal line. Already gone is fine (idempotent): still 204."""
    key = str(client_id)
    row = db.scalars(select(ProteinEntry).where(ProteinEntry.client_id == key)).first()
    if row is not None:
        db.delete(row)
    if db.get(ProteinTombstone, key) is None:  # also when the line is unknown: its PUT may still come
        db.add(ProteinTombstone(client_id=key, deleted_at=dt.datetime.now()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # the same DELETE ran at once in another request: already done
        row = db.scalars(select(ProteinEntry).where(ProteinEntry.client_id == key)).first()
        if row is not None:
            db.delete(row)
            db.commit()
    return Response(status_code=204)
