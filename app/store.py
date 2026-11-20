"""Local SQLite authority. Transactions serialize changes to knowledge and bookings."""
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager,closing
from datetime import datetime,timezone
from pathlib import Path
from .compiler import canonical, check

ROOT=Path(__file__).resolve().parents[1]
def seed(name):
    return json.loads((ROOT/"domain_packs"/"travel"/name).read_text(encoding="utf-8"))
def now():
    return datetime.now(timezone.utc).isoformat()
def uid(prefix):
    return prefix+secrets.token_hex(10)

class Store:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.tx() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS accounts(sid TEXT PRIMARY KEY,customer TEXT UNIQUE,sales TEXT UNIQUE,booking TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,sid TEXT,message TEXT,created TEXT);
            CREATE TABLE IF NOT EXISTS packs(version INTEGER PRIMARY KEY,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY,sid TEXT,request TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS grants(decision TEXT PRIMARY KEY,sid TEXT,details TEXT,expires INTEGER,used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,at TEXT,decision TEXT,actor TEXT,kind TEXT,data TEXT);
            CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY,base INTEGER,body TEXT,status TEXT);
            CREATE TABLE IF NOT EXISTS roles(role TEXT PRIMARY KEY,token TEXT UNIQUE);
            CREATE TRIGGER IF NOT EXISTS immutable_events BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'immutable event'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete_events BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'immutable event'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_packs BEFORE UPDATE ON packs BEGIN SELECT RAISE(ABORT,'immutable pack'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete_packs BEFORE DELETE ON packs BEGIN SELECT RAISE(ABORT,'immutable pack'); END;
            """)
            db.execute("INSERT OR IGNORE INTO packs VALUES(1,?)",(canonical(seed("pack.json")),))
            for role in ("founder","knowledge","risk","audit"):
                db.execute("INSERT OR IGNORE INTO roles VALUES(?,?)",(role,secrets.token_urlsafe(32)))
        if os.name!="nt":
            os.chmod(self.path,0o600)
    @contextmanager
    def tx(self,write=True):
        with closing(sqlite3.connect(self.path,timeout=8,isolation_level=None)) as db:
            db.row_factory=sqlite3.Row
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
    def role_key(self,role):
        with self.tx(False) as db:
            return db.execute("SELECT token FROM roles WHERE role=?",(role,)).fetchone()[0]
    def session(self,fixture):
        sid,customer,sales=uid("S"),secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        with self.tx() as db:
            db.execute("INSERT INTO accounts VALUES(?,?,?,?)",(sid,customer,sales,canonical(seed("bookings.json")[fixture])))
        return dict(customer_key=customer,sales_key=sales,session_id=sid)
    def identity(self,key,allowed):
        with self.tx(False) as db:
            for r in ("customer","sales"):
                if r in allowed:
                    row=db.execute(f"SELECT sid FROM accounts WHERE {r}=?",(key,)).fetchone()
                    if row:return r,row[0]
            row=db.execute("SELECT role FROM roles WHERE token=?",(key,)).fetchone()
            if row and row[0] in allowed:return row[0],None
        raise PermissionError("This credential cannot access this operation")

def pack(db):
    return json.loads(db.execute("SELECT body FROM packs ORDER BY version DESC LIMIT 1").fetchone()[0])
def booking(db,sid):
    return json.loads(db.execute("SELECT booking FROM accounts WHERE sid=?",(sid,)).fetchone()[0])
def event(db,d,role,kind,data):
    return db.execute("INSERT INTO events(at,decision,actor,kind,data) VALUES(?,?,?,?,?)",(now(),d,role,kind,canonical(data))).lastrowid
def lookup(db,d,sid=None):
    row=db.execute("SELECT * FROM decisions WHERE id=?",(d,)).fetchone()
    check(row and (sid is None or row["sid"]==sid),"Unknown decision")
    return json.loads(row["payload"])
