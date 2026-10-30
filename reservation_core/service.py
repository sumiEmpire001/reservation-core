import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime


class CapacityError(ValueError):
    pass


def instant(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Use timezone-aware ISO datetimes")
    return value.timestamp()


def units(value):
    if type(value) is not int or not 1 <= value <= 100000:
        raise ValueError("Units must be an integer from 1 to 100000")


def name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Names must be nonempty strings of at most 128 characters")


def peak(rows, first, last):
    changes = {}
    for row in rows:
        start, end = max(first, row["start"]), min(last, row["end"])
        if start < end:
            changes[start] = changes.get(start, 0) + row["units"]
            changes[end] = changes.get(end, 0) - row["units"]
    current = maximum = 0
    for at in sorted(changes):
        current += changes[at]
        maximum = max(maximum, current)
    return maximum


class Reservations:
    def __init__(self, path="bookings.db", clock=time.time):
        if str(path) == ":memory:":
            raise ValueError("Use a filesystem database")
        self.path, self.clock = str(path), clock
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY,capacity INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY,resource TEXT NOT NULL REFERENCES resources(id),
                    owner TEXT NOT NULL,start REAL NOT NULL,end REAL NOT NULL,units INTEGER NOT NULL,state TEXT NOT NULL,
                    expires REAL NOT NULL,command_key TEXT NOT NULL,fingerprint TEXT NOT NULL,UNIQUE(owner,command_key));
                CREATE INDEX IF NOT EXISTS overlaps ON bookings(resource,state,start,end);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,booking TEXT NOT NULL,event TEXT NOT NULL,at REAL NOT NULL);
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, booking, event):
        db.execute("INSERT INTO events(booking,event,at) VALUES(?,?,?)", (booking, event, self.clock()))

    def expire(self, db):
        expired = db.execute("SELECT id FROM bookings WHERE state='held' AND expires<=?", (self.clock(),)).fetchall()
        for row in expired:
            db.execute("UPDATE bookings SET state='expired' WHERE id=?", (row["id"],))
            self.event(db, row["id"], "expired")

    def resource(self, resource_id, capacity):
        name(resource_id)
        units(capacity)
        with self.db(True) as db:
            db.execute("INSERT INTO resources VALUES(?,?)", (resource_id, capacity))

    def interval(self, start, end):
        start, end = instant(start), instant(end)
        if start >= end:
            raise ValueError("End must be after start")
        return start, end

    def free(self, db, resource_id, start, end):
        resource = db.execute("SELECT capacity FROM resources WHERE id=?", (resource_id,)).fetchone()
        if not resource:
            raise ValueError("Resource not found")
        rows = db.execute("SELECT start,end,units FROM bookings WHERE resource=? AND state IN ('held','confirmed') AND start<? AND end>?", (resource_id, end, start)).fetchall()
        return resource["capacity"] - peak(rows, start, end)

    def availability(self, resource_id, start, end):
        start, end = self.interval(start, end)
        with self.db(True) as db:
            self.expire(db)
            return self.free(db, resource_id, start, end)

    def hold(self, resource_id, owner, start, end, *, amount=1, seconds=300, command_key):
        name(owner)
        name(command_key)
        units(amount)
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise ValueError("Hold duration must be 1..3600 seconds")
        start, end = self.interval(start, end)
        fingerprint = json.dumps([resource_id, start, end, amount, seconds])
        with self.db(True) as db:
            self.expire(db)
            old = db.execute("SELECT * FROM bookings WHERE owner=? AND command_key=?", (owner, command_key)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Command key already belongs to a different booking")
                return dict(old)
            if start < self.clock():
                raise ValueError("New bookings must start in the future")
            if self.free(db, resource_id, start, end) < amount:
                raise CapacityError("Insufficient overlapping capacity")
            booking = uuid.uuid4().hex
            db.execute("INSERT INTO bookings VALUES(?,?,?,?,?,?,'held',?,?,?)", (booking, resource_id, owner, start, end, amount, self.clock() + seconds, command_key, fingerprint))
            self.event(db, booking, "held")
            return dict(db.execute("SELECT * FROM bookings WHERE id=?", (booking,)).fetchone())

    def change(self, booking_id, actor, action):
        if action not in ("confirm", "cancel"):
            raise ValueError("Unknown action")
        with self.db(True) as db:
            self.expire(db)
            row = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
            if not row or row["owner"] != actor:
                raise PermissionError("Booking unavailable to this actor")
            desired = "confirmed" if action == "confirm" else "cancelled"
            if row["state"] == desired:
                return dict(row)
            allowed = ("held",) if action == "confirm" else ("held", "confirmed")
            if row["state"] not in allowed:
                raise ValueError("Booking is not in a valid state for this action")
            db.execute("UPDATE bookings SET state=? WHERE id=?", (desired, booking_id))
            self.event(db, booking_id, desired)
            return dict(db.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone())

    def get(self, booking_id, actor):
        with self.db(True) as db:
            self.expire(db)
            row = db.execute("SELECT * FROM bookings WHERE id=? AND owner=?", (booking_id, actor)).fetchone()
            if not row:
                raise PermissionError("Booking unavailable to this actor")
            return dict(row)

    def history(self, booking_id, actor):
        self.get(booking_id, actor)
        with self.db() as db:
            return [dict(row) for row in db.execute("SELECT event,at FROM events WHERE booking=? ORDER BY seq", (booking_id,))]
