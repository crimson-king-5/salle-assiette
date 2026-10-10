"""Weigh-in routes (SPEC §4.8), same offline pattern as the protein journal.

  PUT    /weights/{client_id}  create the weigh-in, or replace it if it already exists
  DELETE /weights/{client_id}  remove it; always 204, and the id is remembered so that a late
                               PUT (a retry already on its way) cannot bring it back
"""

import datetime as dt

from fastapi import APIRouter, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import DbSession
from app.models import WeightEntry, WeightTombstone
from app.schemas import ClientId, SyncOut, WeightSyncIn

router = APIRouter(prefix="/weights", tags=["weights"])


def store_weight(
    db, key: str, date: dt.date, kg: float, created_at: dt.datetime, imported: bool = False,
) -> tuple[WeightEntry | None, bool]:
    """Create or replace weigh-in `key`, keeping ONE weigh-in per day (no commit).

    Rules when another weigh-in already exists on that day:
    - an existing row of the user moved/corrected onto the day wins (their newest action);
    - a brand-new weigh-in wins only if its created_at is not older than the rival's (a late
      offline arrival is dropped);
    - an imported one never replaces anything (skipped).
    Losers are deleted and remembered in the tombstones. Returns (row, created); row is None when
    the weigh-in was not stored.
    """
    row = db.scalars(select(WeightEntry).where(WeightEntry.client_id == key)).first()
    created = row is None
    rivals = [r for r in db.scalars(select(WeightEntry).where(WeightEntry.date == date))
              if r.client_id != key]
    if imported and rivals:
        return None, created  # an imported value never replaces a real one; nothing to remember
    if created and any(r.created_at > created_at for r in rivals):
        if row is not None:
            db.delete(row)
        if db.get(WeightTombstone, key) is None:
            db.add(WeightTombstone(client_id=key, deleted_at=dt.datetime.now()))
        return None, created
    for r in rivals:
        db.delete(r)
        if db.get(WeightTombstone, r.client_id) is None:
            db.add(WeightTombstone(client_id=r.client_id, deleted_at=dt.datetime.now()))
    if created:
        row = WeightEntry(client_id=key)
        db.add(row)
    row.date, row.kg, row.created_at = date, kg, created_at
    return row, created


@router.put("/{client_id}")
def sync_weight(client_id: ClientId, body: WeightSyncIn, db: DbSession, response: Response) -> SyncOut:
    """Offline sync: store (or correct) one weigh-in made on the phone. One per day is kept."""
    key = str(client_id)
    if db.get(WeightTombstone, key) is not None:
        response.status_code = 200
        return SyncOut(id=0, created=False)  # deleted earlier: nothing to store, nothing to retry
    for attempt in (1, 2):
        row, created = store_weight(db, key, body.date, body.kg, body.created_at)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()  # same client_id inserted at once by another request: retry as update
            if not created or attempt == 2:
                raise

    if row is None:  # a later weigh-in of the same day already stands
        response.status_code = 200
        return SyncOut(id=0, created=False)
    response.status_code = 201 if created else 200
    return SyncOut(id=row.id, created=created)


@router.delete("/{client_id}", status_code=204)
def delete_weight(client_id: ClientId, db: DbSession) -> Response:
    """Remove a weigh-in. Already gone is fine (idempotent): still 204."""
    key = str(client_id)
    row = db.scalars(select(WeightEntry).where(WeightEntry.client_id == key)).first()
    if row is not None:
        db.delete(row)
    if db.get(WeightTombstone, key) is None:
        db.add(WeightTombstone(client_id=key, deleted_at=dt.datetime.now()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # the same DELETE ran at once in another request: already done
        row = db.scalars(select(WeightEntry).where(WeightEntry.client_id == key)).first()
        if row is not None:
            db.delete(row)
            db.commit()
    return Response(status_code=204)
