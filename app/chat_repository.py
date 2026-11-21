"""SQLite queries and public itinerary projections for the travel chat."""
import json
import secrets
import time

from .compiler import canonical, check, digest
from .flight_links import fare_search_url
from .store import booking, now, pack, uid
from .trip_intake import FIELDS, QUESTIONS, SEAT_PREFERENCES, total

SCHEMA = """
CREATE TABLE IF NOT EXISTS web_chats(
 id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, active TEXT, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS web_trips(
 id TEXT PRIMARY KEY, thread TEXT NOT NULL, sid TEXT UNIQUE, phase TEXT NOT NULL DEFAULT 'draft',
 draft TEXT NOT NULL DEFAULT '{}', queue TEXT NOT NULL DEFAULT '[]',
 decision TEXT, offer TEXT, clarification TEXT, quote_hash TEXT, created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS web_trips_thread ON web_trips(thread);
CREATE TABLE IF NOT EXISTS web_messages(
 seq INTEGER PRIMARY KEY, thread TEXT NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS web_messages_thread ON web_messages(thread,seq);
CREATE TABLE IF NOT EXISTS web_turns(
 thread TEXT NOT NULL, nonce TEXT NOT NULL, message TEXT NOT NULL, PRIMARY KEY(thread,nonce)
);
CREATE TABLE IF NOT EXISTS web_sales(
 id TEXT PRIMARY KEY, trip TEXT NOT NULL, request TEXT NOT NULL UNIQUE, message TEXT NOT NULL,
 decision TEXT NOT NULL UNIQUE, status TEXT NOT NULL, lease TEXT, deadline REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS web_sales_trip ON web_sales(trip,status);
"""


def initialize(db):
    db.executescript(SCHEMA)
    for table, column in (("web_messages", "trip"), ("web_turns", "trip"), ("web_messages", "photo")):
        if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    # A legacy transcript covering one trip can be assigned to it safely.
    db.execute("""UPDATE web_messages SET trip=(SELECT id FROM web_trips WHERE thread=web_messages.thread)
        WHERE trip IS NULL AND (SELECT count(*) FROM web_trips WHERE thread=web_messages.thread)=1""")


def messages(db, thread, trip_id):
    rows = db.execute(
        "SELECT seq,role,body,photo FROM web_messages WHERE thread=? AND trip IS ? ORDER BY seq",
        (thread, trip_id),
    )
    return [dict(row) | {"photo": json.loads(row["photo"]) if row["photo"] else None} for row in rows]


def previous_question(db, thread, trip_id):
    last = db.execute(
        "SELECT body FROM web_messages WHERE thread=? AND trip=? AND role='assistant' ORDER BY seq DESC LIMIT 1",
        (thread, trip_id),
    ).fetchone()
    if last:
        return next((field for field, prompt in QUESTIONS.items() if last["body"].endswith(prompt)), None)
    return None


def append(db, thread, role, body, trip_id=None, *, photo=None):
    trip = active(db,thread)
    db.execute(
        "INSERT INTO web_messages(thread,role,body,at,trip,photo) VALUES(?,?,?,?,?,?)",
        (thread, role, body, now(), trip_id or (trip["id"] if trip else None),
         canonical(photo) if photo else None),
    )


def trip_row(db, thread, trip_id):
    return db.execute("SELECT * FROM web_trips WHERE id=? AND thread=?", (trip_id,thread)).fetchone()


def active(db, thread):
    return db.execute("SELECT t.* FROM web_chats c JOIN web_trips t ON t.id=c.active AND t.thread=c.id WHERE c.id=?", (thread,)).fetchone()


def new_trip(db, thread):
    trip_id = "NT-" + secrets.token_hex(4).upper()
    db.execute("INSERT INTO web_trips(id,thread,created) VALUES(?,?,?)", (trip_id,thread,now()))
    db.execute("UPDATE web_chats SET active=? WHERE id=?", (trip_id,thread))
    return trip_row(db,thread,trip_id)


def actual_offer(db, trip):
    if not trip or not trip["offer"]:
        return None
    obj = json.loads(trip["offer"])
    grant = db.execute("SELECT expires,used,details FROM grants WHERE decision=? AND sid=?",(obj["decision_id"],trip["sid"])).fetchone()
    if not grant or grant["used"] or grant["expires"] <= time.time():
        return None
    details = json.loads(grant["details"])
    if details["snapshot"] != digest(booking(db,trip["sid"])) or details["pack"] != pack(db)["version"]:
        return None
    return obj


def busy(db, trip_id):
    return db.execute("SELECT 1 FROM web_sales WHERE trip=? AND status IN ('queued','working')",(trip_id,)).fetchone() is not None


def dispatch(db, trip):
    waiting = json.loads(trip["queue"])
    if not trip["sid"] or not waiting or actual_offer(db,trip) or busy(db,trip["id"]):
        return
    message = waiting.pop(0)
    request_id = uid("R")
    db.execute("INSERT INTO requests VALUES(?,?,?,?)",(request_id,trip["sid"],message,now()))
    db.execute("INSERT INTO web_sales(id,trip,request,message,decision,status) VALUES(?,?,?,?,?,'queued')",
               (uid("J"),trip["id"],request_id,message,uid("D")))
    db.execute("UPDATE web_trips SET queue=?,offer=NULL WHERE id=?", (canonical(waiting),trip["id"]))


def enqueue(db, trip, message):
    waiting = json.loads(trip["queue"])
    if message.casefold() not in {t.casefold() for t in waiting}:
        check(len(waiting)<8,"There are already eight saved requests for this trip.")
        waiting.append(message)
        db.execute("UPDATE web_trips SET queue=? WHERE id=?", (canonical(waiting),trip["id"]))


def itinerary(db, trip):
    if not trip:
        return None
    state = booking(db,trip["sid"]) if trip["sid"] else json.loads(trip["draft"])
    data = {key:state.get(key) for key in FIELDS}
    if not trip["sid"]:
        for field in ("origin","destination"):
            if not state.get(field+"_location"):
                data[field]=None
    if trip["sid"]:
        price = state.get("paid_cents")
    else:
        price = total(state) if state.get("fare") else None
    data.update(id=trip["id"],phase=trip["phase"],price_cents=price,photo=state.get("photo"),fare_source=(state.get("fare") or {}).get("source"),fare_live=(state.get("fare") or {}).get("live",False),
                package=state.get("package"),currency="SGD",upgraded=state.get("upgraded",False),
                credit_cents=state.get("credit_cents",0),queued_requests=len(json.loads(trip["queue"])),
                reviewing=busy(db,trip["id"]),has_offer=bool(actual_offer(db,trip)),
                has_decision=bool(trip["decision"]))
    fare = state.get("fare") or {}
    stale = bool(state.get("flight_details_stale"))
    data.update(
        airline_preference=state.get("airline_preference"),
        fare_demo=fare.get("demo", False),
        fare_estimate=fare.get("estimate", False),
        fare_url=None if stale else fare.get("source_url"),
        fare_search_url=fare_search_url(state) if not trip["sid"] and (not fare or fare.get("demo")) else None,
        airlines=[] if stale else fare.get("airlines", []),
        outbound_airlines_only=fare.get("outbound_airlines_only", False),
        route_airlines=state.get("route_airlines", []),
        seat_preference=SEAT_PREFERENCES.get(state.get("seat_preference")),
        seat_maps=[] if stale or trip["sid"] or fare.get("expires", 0) <= time.time()
        else fare.get("seat_maps", []),
        flight_details_stale=stale,
    )
    return data


