"""One browser conversation, stored itineraries, and automatic sales handoff."""
import hashlib
import json
import os
import re
import secrets
import time
from datetime import date
from uuid import uuid4

from pydantic import ValidationError

from .booking_intake import BookingIntake, next_field
from .chat_repository import (
    active, actual_offer, append, busy, dispatch, enqueue, initialize, itinerary, messages,
    new_trip, previous_question, trip_row,
)
from .compiler import Review, canonical, check, digest
from .contracts import Consent, Proposal, Goal
from .flight_links import fare_search_url
from .sales_desk import NEG, evidence_text, explain, goal_from_message, make_proposal, negotiation_text
from .store import allow_event_deletion, booking, event, lookup, now, uid
from .trip_intake import QUESTIONS, create_booking, flight_summary, human_date, money, quote_text, total
from .language_models import Planners
from .travel_search import TravelSearch
from .web_travel_search import WebTravelSearch


NEW_TRIP_COMMANDS = {
    "book another trip", "book a new trip", "new trip", "new booking", "new itinerary",
    "start over", "book another booking", "plan another trip", "start a booking",
}

HELP = ("Tell me your starting city or airport, destination, dates and number of adult travellers. I’ll validate the route, ask for cabin class and traveller name, and search for an airline quote. You can change the details before you confirm. "
        "You can also ask for a discount or a date change. I’ll save that request until your booking is confirmed.")


def norm(text):
    return re.sub(r"[’']", "", text.casefold()).strip(" .!?,\t\n")


def ask_why(text):
    return bool(re.search(r"\b(?:why|explain|explanation|audit|assumptions?|evidence|verification trace|decision trace|probabilities|reason(?:ing)?|how did you (?:decide|calculate)|how was .+ decided)\b", text, re.I))


class ChatDesk:
    def __init__(self, service, broker=None):
        self.service, self.store = service,service.store
        self.planners = Planners(broker)
        self.travel = TravelSearch() if os.environ.get('AG_TRAVEL_MODE','web')=='amadeus' else WebTravelSearch(allow_demo_fares=True)
        self.broker = self.planners.broker
        with self.store.tx() as db:
            initialize(db)

    @staticmethod
    def hash_key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def identify(self, token):
        if not token or len(token)>256:
            return None
        with self.store.tx(False) as db:
            row = db.execute("SELECT id FROM web_chats WHERE token_hash=?", (self.hash_key(token),)).fetchone()
            return row[0] if row else None

    def start(self):
        token, thread = secrets.token_urlsafe(32), uid("W")
        with self.store.tx() as db:
            db.execute("INSERT INTO web_chats(id,token_hash) VALUES(?,?)",(thread,self.hash_key(token)))
        return thread,token

    def state(self, thread):
        with self.store.tx() as db:
            for waiting in db.execute("SELECT * FROM web_trips WHERE thread=? AND sid IS NOT NULL AND queue!='[]'",(thread,)).fetchall():
                dispatch(db,waiting)
            row = db.execute("SELECT revision FROM web_chats WHERE id=?",(thread,)).fetchone()
            chosen=active(db,thread)
            history = messages(db, thread, chosen["id"] if chosen else None)
            pending = bool(db.execute("SELECT 1 FROM web_sales j JOIN web_trips t ON t.id=j.trip WHERE t.thread=? AND j.status IN ('queued','working')",(thread,)).fetchone())
            expires = db.execute("""SELECT min(g.expires) FROM web_trips t JOIN grants g ON g.decision=t.decision
                WHERE t.thread=? AND t.offer IS NOT NULL AND g.used=0 AND g.expires>?""",(thread,time.time())).fetchone()[0]
            return {"revision":row[0],"messages":history,"itinerary":itinerary(db,active(db,thread)),
                    "trips":[{"id":t["id"],"destination":(booking(db,t["sid"]) if t["sid"] else json.loads(t["draft"])).get("destination","New itinerary"),"phase":t["phase"]} for t in db.execute("SELECT * FROM web_trips WHERE thread=? ORDER BY rowid DESC",(thread,)).fetchall()],
                    "working":pending,"offer_expires_at":expires}

    def migration(self, thread, customer_key):
        """Attach only an authenticated v1 session's existing booked fixture to an empty chat."""
        role, sid = self.store.identity(customer_key, ("customer",))
        with self.store.tx() as db:
            if active(db,thread):
                return
            b = booking(db,sid)
            if b["kind"] != "booked" or db.execute("SELECT 1 FROM web_trips WHERE sid=?", (sid,)).fetchone():
                return
            trip = new_trip(db,thread)
            db.execute("UPDATE web_trips SET sid=?,phase='booked' WHERE id=?",(sid,trip["id"]))
            append(db,thread,"assistant",f"I found your existing {b['package']} booking, departing {human_date(b['departure'])}. You can manage it here or ask me to book another trip.")
            db.execute("UPDATE web_chats SET revision=revision+1 WHERE id=?",(thread,))

    def turn(self, thread, body):
        text = body.message.strip()
        check(bool(text), "Please enter a message.")
        # Model calls run outside SQLite write transactions.
        with self.store.tx(False) as db:
            if self.replayed(db,thread,body.nonce,text):
                return
            trip = active(db,thread)
            phase = trip["phase"] if trip else None
            draft = json.loads(trip["draft"]) if trip else {}
            trip_id = trip["id"] if trip else None
            if trip and phase == "draft" and "_awaiting" not in draft:
                draft["_awaiting"] = previous_question(db, thread, trip_id)
        n = norm(text)
        start = bool(re.search(r"\b(?:new itinerary|new trip|another (?:trip|itinerary|booking)|start over|start a booking|new booking)\b", n))
        if phase=="booked" and re.search(r"\b(?:to|from)\b",n):
            reserve = re.search(r"\b(?:book|plan|reserve)\s+(?:(?:me|us|a|an|my|our|new)\s+)*(?:trip|itinerary|holiday|vacation|flight|ticket|from|to)\b",n)
            if reserve or (not NEG.search(text) and re.search(r"^(?:i want to |please )?(?:book|plan|reserve)\b",n)):
                start = True
        bare_start = n in NEW_TRIP_COMMANDS
        cmd = self.command_kind(n)
        ordinary = not (ask_why(n) or cmd or n in ("yes", "sure", "ok") or bare_start)
        today = date.today()
        intake = None
        if cmd == "refresh_quote" and phase in ("draft", "review"):
            intake = BookingIntake(self.planners, self.travel).refresh(draft)
        elif ordinary and (phase != "booked" or start):
            intake = BookingIntake(self.planners, self.travel).interpret(text, {} if start else draft, today)
        with self.store.tx() as db:
            if self.replayed(db,thread,body.nonce,text):
                return
            row = db.execute("SELECT revision FROM web_chats WHERE id=?", (thread,)).fetchone()
            check(row and row[0]==body.revision, "Your conversation was updated. Review the latest reply and send your message again.")
            trip = active(db,thread)
            db.execute("INSERT INTO web_turns(thread,nonce,message) VALUES(?,?,?)",(thread,body.nonce,text))
            append(db,thread,"user",text)
            answer = self.respond(db, thread, trip, text, n, start, intake, today)
            chosen=active(db,thread)
            chosen_id=chosen["id"] if chosen else None
            db.execute("UPDATE web_messages SET trip=? WHERE seq=(SELECT max(seq) FROM web_messages WHERE thread=?)",(chosen_id,thread))
            db.execute("UPDATE web_turns SET trip=? WHERE thread=? AND nonce=?",(chosen_id,thread,body.nonce))
            confirmed = chosen and chosen["phase"] == "booked" and phase == "review" and chosen_id == trip_id
            photo = booking(db, chosen["sid"]).get("photo") if confirmed else None
            append(db, thread, "assistant", answer, photo=photo)
            db.execute("UPDATE web_chats SET revision=revision+1 WHERE id=?", (thread,))

    @staticmethod
    def replayed(db, thread, nonce, text):
        row = db.execute("SELECT message FROM web_turns WHERE thread=? AND nonce=?",(thread,nonce)).fetchone()
        check(not row or row[0]==text,"This message identifier has already been used.")
        return bool(row)

    @staticmethod
    def command_kind(n):
        if n in ("help","how do bookings work","how does booking work","how does this work","what can you do","what can i say","hi","hello","hey"):
            return "help"
        if re.fullmatch(r"(?:show |list |see |view )?(?:all )?my (?:trips|bookings|itineraries)",n):
            return "list"
        if re.fullmatch(r"(?:open|show|switch to|view) (?:trip |booking )?nt-[a-f\d]{8}",n):
            return "switch"
        if n in ("show my itinerary","my itinerary","show my booking","show itinerary","booking details"):
            return "view"
        if n in ("refresh my quote", "refresh quote", "refresh my fare", "refresh fare"):
            return "refresh_quote"
        if (
            re.search(r"\b(?:airlines?|seats?|seat maps?|seat availability)\b", n)
            and re.match(r"^(?:show|view|what|which|who|are|do i have|can i see|tell me)\b", n)
            and not re.search(r"\b(?:change|switch|update|prefer|sit|upgrade|reserve|select)\b", n)
        ):
            return "flights"
        if n in ("confirm booking","confirm my booking","book it","confirm my trip","confirm the booking","reserve it"):
            return "confirm"
        if n in ("accept offer","accept the offer","accept this offer","accept exact terms","i accept the offer"):
            return "accept"
        if n in ("decline offer","decline the offer","decline this offer","reject offer"):
            return "decline"
        return None

    def respond(self, db, thread, trip, text, n, start, intake, today):
        cmd = self.command_kind(n)
        if ask_why(n):
            if trip and trip["phase"]=="review":
                draft = json.loads(trip["draft"])
                fare = draft.get("fare") or {}
                if fare.get("demo"):
                    count = draft["travellers"]
                    return (f"The simulated return fare uses the {draft['cabin'].replace('_',' ').lower()} rate for this route’s distance band. "
                            f"{money(fare['per_person_cents'])} per adult × {count} = {money(total(draft))}. "
                            "The airline list describes published route service. Use “Compare current fares” for advertised prices.")
                return quote_text(draft)
            if trip and trip["decision"]:
                d = lookup(db,trip["decision"],trip["sid"])
                if re.search(r"\b(?:audit|assumptions?|evidence|verification trace|decision trace|probabilities)\b",n):
                    saved=db.execute("SELECT body FROM packs WHERE version=?",(d["pack_version"],)).fetchone()
                    return evidence_text(d,json.loads(saved[0]))
                e = db.execute("SELECT data FROM events WHERE decision=? AND kind='mock transaction' ORDER BY id DESC LIMIT 1",(d["decision_id"],)).fetchone()
                return explain(d,json.loads(e[0]) if e else None)
            return explain(None,None)
        if cmd=="help":
            return HELP
        if cmd=="list":
            rows = db.execute("SELECT * FROM web_trips WHERE thread=? ORDER BY rowid DESC",(thread,)).fetchall()
            if not rows:
                return "Your trip list is empty. Tell me where you’d like to go."
            result=[]
            for r in rows:
                v=itinerary(db,r)
                result.append(f"• {v['id']} · {v['destination'] or v['package'] or 'New itinerary'} · {v['phase']}")
            return "Your itineraries\n"+"\n".join(result)+"\n\nSay “Open "+rows[0]["id"]+"” to choose a trip."
        if cmd=="switch":
            ref=n.split()[-1].upper()
            found=trip_row(db,thread,ref)
            if not found:
                return "I couldn’t find that trip in your conversation. Say “List my trips” for your booking references."
            db.execute("UPDATE web_chats SET active=? WHERE id=?",(ref,thread))
            return "I’ve opened "+ref+". Your itinerary shows its latest details."
        if cmd=="view":
            if not trip:
                return "Your itinerary is empty. Tell me where you’re starting and where you’d like to go."
            return self.describe_itinerary(itinerary(db, trip), json.loads(trip["draft"]))
        if cmd == "refresh_quote":
            if not trip:
                return "Tell me your starting city and destination so I can start an itinerary."
            if trip["phase"] == "booked":
                return "This demo booking is confirmed. Say “Show my itinerary” for your saved total."
            return self.save_intake(db, trip, intake)
        if cmd == "flights":
            if not trip:
                return "Tell me your starting city and destination so I can look up the route."
            if trip["sid"]:
                return flight_summary(booking(db, trip["sid"]), confirmed=True)
            draft = json.loads(trip["draft"])
            if not draft.get("origin_location") or not draft.get("destination_location"):
                return "I can show airline details once I have both cities."
            return flight_summary(draft)
        if cmd in ("accept","decline"):
            return self.handle_offer(db,thread,trip,cmd)
        if cmd=="confirm" or (n in ("yes","sure","ok") and trip and trip["phase"]=="review"):
            return self.confirm(db, thread, trip, today)
        if n in ("yes","sure","ok","confirm") and trip and trip["phase"]=="booked":
            return "To accept the exact change I offered, reply “Accept offer”." if actual_offer(db,trip) else "What would you like to do with this booking?"
        if not trip or start:
            trip = new_trip(db,thread)
            if n in NEW_TRIP_COMMANDS:
                db.execute("UPDATE web_trips SET draft=? WHERE id=?",
                           (canonical({"_awaiting": "origin"}), trip["id"]))
                return QUESTIONS["origin"]
        if trip["phase"] == "booked":
            neg = text
            previous = trip["clarification"]
            if previous and re.fullmatch(r"(?:earlier|later)[.!]?", text.strip(), re.I):
                neg = re.sub(r"\bback\b", "", previous, flags=re.I) + " " + text
            elif previous and re.fullmatch(r"(?:SGD|S\$|\$)?\s*\d+(?:\.\d+)?\s*(?:%|SGD|dollars?)?", text.strip(), re.I):
                neg = previous + ". " + text
            enqueue(db,trip,neg)
            db.execute("UPDATE web_trips SET clarification=NULL WHERE id=?",(trip["id"],))
            dispatch(db,trip_row(db,thread,trip["id"]))
            return ("I’ve saved that request. Accept or decline the current offer first." if actual_offer(db,trip)
                    else "I’m checking your request against this booking. I’ll bring the result back here.")
        if intake is None:
            draft = json.loads(trip["draft"])
            awaiting = draft.get("_awaiting") or next_field(draft)
            return QUESTIONS.get(awaiting, "Say “Refresh my quote” to review your itinerary.")
        return self.save_intake(db, trip, intake)

    @staticmethod
    def fare_pending(draft):
        if fare_search_url(draft):
            return ("I have the trip details. I couldn’t verify an SGD return fare. Use “Compare current fares” "
                    "in your itinerary or say “Refresh my quote” to search again.")
        return "I need a sourced fare before confirming. Say “Refresh my quote” to search again."

    @staticmethod
    def describe_itinerary(view, draft):
        route = view["package"] or f"{view['origin'] or 'Origin to confirm'} to {view['destination'] or 'destination to confirm'}"
        lines = [f"{view['id']} · {view['phase'].title()}", route]
        if view["departure"]:
            lines.append(f"Departs {human_date(view['departure'])}")
        if view["return_date"]:
            lines.append(f"Returns {human_date(view['return_date'])}")
        party = []
        if view["travellers"]:
            count = view["travellers"]
            party.append(f"{count} traveller" if count == 1 else f"{count} travellers")
        if view["cabin"]:
            party.append(view["cabin"].replace("_", " ").capitalize() + " class")
        if view["lead_name"]:
            party.append(f"Under {view['lead_name']}")
        if party:
            lines.append(" · ".join(party))
        if view.get("airline_preference"):
            lines.append("Airline preference: " + view["airline_preference"])
        if view["seat_preference"]:
            lines.append(f"Seat preference: {view['seat_preference']}")

        fare = draft.get("fare") or {}
        if view["phase"] != "booked" and fare and fare.get("expires", 0) <= time.time():
            lines.append("The earlier fare expired. Say “Refresh my quote” to search again.")
        elif view["price_cents"] is not None:
            label = "Simulated demo total" if view["fare_demo"] else "Estimated total" if view["fare_estimate"] else "Current total"
            lines.append(f"{label}: {money(view['price_cents'])}")
        if view["phase"] != "booked":
            awaiting = next_field(draft)
            if awaiting:
                lines.append(QUESTIONS[awaiting])
            elif view["price_cents"] is None:
                lines.append(ChatDesk.fare_pending(draft))
        return "\n".join(lines)

    @staticmethod
    def save_intake(db, trip, intake):
        draft = intake.draft
        draft["_awaiting"] = intake.awaiting
        if intake.negotiation:
            enqueue(db, trip, intake.negotiation)
        ready = not intake.awaiting and not intake.errors and bool(draft.get("fare"))
        fingerprint = digest([draft, total(draft)]) if ready else None
        db.execute(
            "UPDATE web_trips SET phase=?,draft=?,quote_hash=? WHERE id=?",
            ("review" if ready else "draft", canonical(draft), fingerprint, trip["id"]),
        )
        parts = []
        if intake.negotiation:
            parts.append("I’ve saved your negotiation request. I’ll check it after you confirm the standard booking.")
        parts.extend(dict.fromkeys(intake.errors))
        if ready:
            parts.append(quote_text(draft))
        elif intake.awaiting:
            parts.append(QUESTIONS[intake.awaiting])
        elif not intake.errors:
            parts.append(ChatDesk.fare_pending(draft))
        return "\n\n".join(parts)

    def confirm(self, db, thread, trip, today):
        if not trip or trip["phase"]=="draft":
            draft = json.loads(trip["draft"]) if trip else {}
            awaiting = next_field(draft)
            return QUESTIONS[awaiting] if awaiting else self.fare_pending(draft)
        if trip["phase"]=="booked":
            return "This booking is already confirmed. You can ask for a change or start another trip."
        draft=json.loads(trip["draft"])
        if not draft.get("fare") or draft["fare"].get("expires",0) < time.time():
            return "That quote or estimate expired. Say ‘Refresh my quote’ to search again before confirming."
        check(trip["quote_hash"]==digest([draft,total(draft)]),"The quote changed. Review the updated price first.")
        try:
            b=create_booking(trip["id"], draft, today)
        except ValueError as e:
            return str(e)
        sid=uid("S")
        db.execute("INSERT INTO accounts VALUES(?,?,?,?)",(sid,secrets.token_urlsafe(32),secrets.token_urlsafe(32),canonical(b)))
        db.execute("UPDATE web_trips SET phase='booked',sid=? WHERE id=?",(sid,trip["id"]))
        fare = draft["fare"]
        event(db,trip["id"],"booking","standard booking confirmed",{"trip_id":trip["id"],"amount_cents":b["paid_cents"],"fare_source":fare.get("source"),"version":1})
        dispatch(db,trip_row(db,thread,trip["id"]))
        total_label = "simulated" if fare.get("demo") else "estimated" if fare.get("estimate") else "quoted"
        msg=f"Booked. Your demo reservation to {b['destination']} is {trip['id']}. The {total_label} total is {money(b['paid_cents'])}."
        msg += "\n\n" + flight_summary(b, confirmed=True)
        if json.loads(trip["queue"]):
            msg += "\n\nYour saved request is now with the negotiation agent. I’ll post the decision here."
        return msg

    def handle_offer(self,db,thread,trip,action):
        offer=actual_offer(db,trip)
        if not offer:
            if trip and trip["offer"]:
                db.execute("UPDATE web_trips SET offer=NULL WHERE id=?",(trip["id"],))
                dispatch(db,trip_row(db,thread,trip["id"]))
                return "Those terms expired or the booking changed. Ask me to check the terms again."
            return "There is no open offer to accept or decline."
        if action=="accept":
            try:
                result=self.service.execute(trip["sid"],Consent(**offer,consent=True),transaction=db)
                msg=result["message"]
            except Review:
                msg="Those terms are no longer current. Please ask me to check the request again."
        else:
            db.execute("UPDATE grants SET used=1 WHERE decision=? AND sid=?",(offer["decision_id"],trip["sid"]))
            event(db,offer["decision_id"],"customer","offer declined",{})
            msg="Offer declined. Your itinerary keeps its current terms."
        db.execute("UPDATE web_trips SET offer=NULL WHERE id=?",(trip["id"],))
        dispatch(db,trip_row(db,thread,trip["id"]))
        return msg

    def work(self, thread):
        # Claims survive page reloads. A crashed worker's job is eligible again when the lease expires.
        while True:
            with self.store.tx() as db:
                row=db.execute("""SELECT j.*, t.sid FROM web_sales j JOIN web_trips t ON t.id=j.trip
                    WHERE t.thread=? AND (j.status='queued' OR (j.status='working' AND j.deadline<?))
                    ORDER BY j.rowid LIMIT 1""",(thread,time.time())).fetchone()
                if not row:
                    return
                j=dict(row)
                lease=uuid4().hex
                db.execute("UPDATE web_sales SET status='working',lease=?,deadline=? WHERE id=?",(lease,time.time()+240,j["id"]))
                b=booking(db,j["sid"])
                from .store import pack
                policy=pack(db)
                retained=policy["records"]["credit_retention"]["value"]
                gain=policy["profile"]["minimum_gain_cents"]
                multiplier=policy["profile"]["loss_multiplier"]
                threshold = -retained * multiplier + gain
                if policy["profile"]["floor_cents"] is not None:
                    threshold = max(threshold, policy["profile"]["floor_cents"])
                b["credit_headroom_cents"] = max(0, (-threshold) // multiplier - b.get("credit_cents", 0))
            result=None
            clarify=None
            try:
                goal,clarify=goal_from_message(j["message"],b)
                if clarify:
                    reply=clarify
                else:
                    if goal.type == "unknown":
                        for completion in self.planners.answers("interpret",{
                            "message":j["message"], "departure":b["departure"], "currency":b["currency"],
                            "itinerary":{key:b.get(key) for key in ("origin","destination","departure","return_date","cabin","lead_name","travellers","seat_preference","airline_preference")},
                            "paid_cents":b.get("paid_cents"), "schema":Goal.model_json_schema()}):
                            try:
                                parsed=Goal.model_validate(completion.answer.get("goal"))
                                if parsed.type not in ("credit","amend","amend_upgrade","upgrade","itinerary"):
                                    continue
                                if parsed.type=="itinerary" and not (b.get("self_service") and parsed.changes and parsed.changes.model_dump(exclude_none=True)):
                                    continue
                                if parsed.type=="credit" and not (0 < (parsed.discount_cents or 0) <= b.get("paid_cents",0)):
                                    continue
                                if parsed.type.startswith("amend") and not parsed.days:
                                    continue
                                goal=parsed
                                break
                            except ValidationError:
                                continue
                    proposal=make_proposal(goal,b,j["request"])
                    source="Website local planner"
                    if goal.type!="unknown":
                        for completion in self.planners.answers("sales",{"message":j["message"],"request_id":j["request"],
                            "booking":{k:b[k] for k in ("booking_id","currency","departure","slots","upgrade_prices_cents","credit_headroom_cents")} | {"seat_preference": b.get("seat_preference")},
                            "goal":goal.model_dump(),"schema":Proposal.model_json_schema()}):
                            try:
                                proposed=Proposal.model_validate(completion.answer)
                                if (proposed.goal==goal and proposed.booking_id==b["booking_id"]
                                    and proposed.request_id==j["request"] and not proposed.needs_clarification
                                    and (goal.type!="itinerary" or all(c.plan==proposal.candidate_strategies[0].plan for c in proposed.candidate_strategies))
                                    and (goal.type!="credit" or all(
                                        any(candidate.plan == option.plan for candidate in proposed.candidate_strategies)
                                        for option in proposal.candidate_strategies
                                    ))):
                                    proposal,source=proposed,completion.source
                                    break
                            except ValidationError:
                                pass
                    result=self.service.evaluate(j["sid"],proposal,source,decision_id=j["decision"],travel=self.travel)
                    reply=result["message"]
                    if result["status"]=="NEEDS_REVIEW" and goal.type!="unknown":
                        with self.store.tx(False) as db:
                            evidence=lookup(db,result["decision_id"],j["sid"])
                        reply=("I couldn’t complete the verification for this change, so I don’t have an offer to share yet. The verifier is unavailable."
                               if result["mode"]=="Exact preview" and evidence["models"] else
                               (evidence["reason"] if goal.type=="itinerary" and not evidence["models"] else "I couldn’t verify those terms. You can ask me to check again or try a different change."))
                    if result.get("terms_digest"):
                        reply+="\n\nReply “Accept offer” to apply these exact terms, or “Decline offer” to keep your booking."
            except (Review,ValueError,KeyError) as exc:
                reply="I couldn’t verify this request. Your booked terms are still in effect."
                import logging
                logging.getLogger(__name__).warning("Sales evaluation failed for %s (%s)",j["id"],type(exc).__name__)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Sales job %s failed",j["id"])
                reply="The agency couldn’t finish this check. Your booked terms are still in effect. You can request another review."
            with self.store.tx() as db:
                if not db.execute("UPDATE web_sales SET status='done',lease=NULL WHERE id=? AND status='working' AND lease=?",(j["id"],lease)).rowcount:
                    continue
                offer=None
                if result and result.get("terms_digest"):
                    offer=canonical({k:result[k] for k in ("decision_id","terms_digest")})
                db.execute("UPDATE web_trips SET offer=?,decision=?,clarification=? WHERE id=?",
                           (offer,result["decision_id"] if result else None,j["message"] if clarify else None,j["trip"]))
                current=active(db,thread)
                if current and current["id"]!=j["trip"]:
                    reply=f"For {j['trip']}\n"+reply
                append(db,thread,"assistant",reply,j["trip"])
                db.execute("UPDATE web_chats SET revision=revision+1 WHERE id=?",(thread,))
                dispatch(db,trip_row(db,thread,j["trip"]))

    def select(self,thread,trip_id):
        with self.store.tx() as db:
            check(trip_row(db,thread,trip_id),"Unknown booking")
            db.execute("UPDATE web_chats SET active=?,revision=revision+1 WHERE id=?",(trip_id,thread))

    def delete(self,thread,trip_id):
        with self.store.tx() as db:
            trip=trip_row(db,thread,trip_id)
            check(trip,"Unknown booking")
            check(not busy(db,trip_id),"Please wait for this booking's active review to finish before deleting it.")
            decisions=[r[0] for r in db.execute("SELECT id FROM decisions WHERE sid=?",(trip["sid"],))]
            with allow_event_deletion(db):
                for decision in [trip_id,*decisions]:
                    db.execute("DELETE FROM events WHERE decision=?",(decision,))
            for table,column,value in (("grants","sid",trip["sid"]),("decisions","sid",trip["sid"]),("requests","sid",trip["sid"]),("accounts","sid",trip["sid"]),("web_sales","trip",trip_id),("web_messages","trip",trip_id),("web_turns","trip",trip_id)):
                db.execute("DELETE FROM "+table+" WHERE "+column+"=?",(value,))
            db.execute("DELETE FROM web_trips WHERE id=?",(trip_id,))
            db.execute("DELETE FROM web_messages WHERE thread=? AND instr(body,?)>0",(thread,trip_id))
            db.execute("DELETE FROM web_turns WHERE thread=? AND instr(message,?)>0",(thread,trip_id))
            # Remove unassignable legacy history as it may contain the erased booking.
            db.execute("DELETE FROM web_messages WHERE thread=? AND trip IS NULL",(thread,))
            db.execute("DELETE FROM web_turns WHERE thread=? AND trip IS NULL",(thread,))
            if not active(db,thread):
                other=db.execute("SELECT id FROM web_trips WHERE thread=? ORDER BY rowid DESC LIMIT 1",(thread,)).fetchone()
                db.execute("UPDATE web_chats SET active=? WHERE id=?",(other[0] if other else None,thread))
            db.execute("UPDATE web_chats SET revision=revision+1 WHERE id=?",(thread,))
