from __future__ import annotations

import json
import time
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import RunEvent


def append_event(db: Session, run_id: str, event_type: str, data: dict) -> RunEvent:
    event = RunEvent(run_id=run_id, event_type=event_type, data=data)
    db.add(event)
    db.flush()
    db.commit()
    return event


def event_stream(run_id: str, last_event_id: int = 0, max_wait_seconds: int = 30) -> Iterator[str]:
    started = time.time()
    cursor = last_event_id
    idle_cycles = 0
    while time.time() - started < max_wait_seconds:
        with SessionLocal() as db:
            events = db.scalars(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.sequence > cursor)
                .order_by(RunEvent.sequence)
            ).all()
            for event in events:
                cursor = event.sequence
                payload = json.dumps(event.data, ensure_ascii=False)
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {payload}\n\n"
            if events:
                idle_cycles = 0
                if any(
                    event.event_type in {"run.completed", "run.failed", "run.needs_input"}
                    for event in events
                ):
                    return
            else:
                idle_cycles += 1
                if idle_cycles % 40 == 0:
                    yield ": heartbeat\n\n"
        time.sleep(0.25)
    yield ": stream-window-ended\n\n"
