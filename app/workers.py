"""A QwenWork session can pick up short-lived, scoped planning tasks via MCP."""
import secrets
import threading
import time
from collections import OrderedDict


class PlanningBroker:
    def __init__(self, timeout=35):
        self.timeout = timeout
        self.cv = threading.Condition()
        self.jobs = OrderedDict()
        self.last_poll = float("-inf")

    def request(self, kind, payload):
        with self.cv:
            if time.monotonic()-self.last_poll > 26:
                return None
            job_id = secrets.token_urlsafe(18)
            job = {"kind":kind,"input":payload,"lease":None,"answer":None}
            self.jobs[job_id] = job
            self.cv.notify_all()
            try:
                self.cv.wait_for(lambda: job["answer"] is not None, timeout=self.timeout)
                return job["answer"]
            finally:
                self.jobs.pop(job_id, None)

    def next(self, wait_seconds):
        end = time.monotonic() + min(max(wait_seconds, 0), 18)
        with self.cv:
            while True:
                self.last_poll = time.monotonic()
                for job_id, job in self.jobs.items():
                    if job["lease"] is None:
                        job["lease"] = secrets.token_urlsafe(18)
                        return {"job_id":job_id,"lease":job["lease"],"kind":job["kind"],"input":job["input"]}
                left = end-time.monotonic()
                if left<=0:
                    return {"kind":"idle"}
                self.cv.wait(left)

    def submit(self, job_id, lease, answer):
        with self.cv:
            job = self.jobs.get(job_id)
            if not job or not job["lease"] or not secrets.compare_digest(job["lease"],lease) or job["answer"] is not None:
                return False
            job["answer"] = answer
            self.cv.notify_all()
            return True
