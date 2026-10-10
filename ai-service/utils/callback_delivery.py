"""One durable, coalescing notification per job, committed with its result.

MongoDB is authoritative. Delivery only asks Express to reconcile the saved
snapshot; newer snapshots supersede undelivered intermediate progress events.
"""
import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument
import requests
from utils.internal_security import post_progress

LOG = logging.getLogger("callbacks")
MAX_DELIVERIES = 8
DELIVERY_LEASE_SECONDS = 60  # Greater than the HTTP request timeout.


def now_utc():
    return datetime.now(timezone.utc)


def event_update(fields, *, now=None, unset=None):
    now = now or now_utc()
    update = {"$set": {**fields, "updatedAt": now,
        "delivery": {"state": "pending", "attempts": 0, "nextAt": now}},
        "$inc": {"progressVersion": 1}}
    if unset:
        update["$unset"] = unset
    return update


def deliver_one(jobs, *, now=None, send=post_progress):
    now = now or now_utc()
    token = uuid.uuid4().hex
    job = jobs.find_one_and_update({"$or": [
        {"delivery.state": "pending", "delivery.nextAt": {"$lte": now}},
        {"delivery.state": "sending", "delivery.leaseUntil": {"$lte": now}},
    ]}, {"$set": {"delivery.state": "sending", "delivery.token": token,
                   "delivery.leaseUntil": now + timedelta(seconds=DELIVERY_LEASE_SECONDS)},
         "$inc": {"delivery.attempts": 1}},
        sort=[("delivery.nextAt", 1), ("_id", 1)], return_document=ReturnDocument.AFTER,
        projection={"_id": 1, "execution.attemptId": 1, "progressVersion": 1, "delivery": 1})
    if job is None:
        return False
    payload = {"job_id": str(job["_id"]), "attempt_id": job.get("execution", {}).get("attemptId"),
               "sequence": job["progressVersion"]}
    # A response to an old event must never clear a new event's pending marker.
    owned = {"_id": job["_id"], "progressVersion": payload["sequence"], "delivery.token": token}
    attempts = job["delivery"]["attempts"]
    retryable, category, retry_after = True, "invalid_ack", 0
    if attempts <= MAX_DELIVERIES:
        try:
            response = send(payload)
            ack = response.json()
            if (ack.get("received") is True and ack.get("attempt_id") == payload["attempt_id"]
                    and ack.get("sequence") == payload["sequence"]):
                jobs.update_one(owned, {"$set": {"delivery.state": "delivered", "delivery.deliveredAt": now},
                    "$unset": {"delivery.token": "", "delivery.leaseUntil": "", "delivery.lastError": ""}})
                return True
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            retryable = status in {408, 425, 429} or 500 <= status <= 599
            category = f"http_{status}"
            if status in {429, 503}:
                try:
                    retry_after = min(300, max(0, int(exc.response.headers.get("Retry-After", "0"))))
                except (TypeError, ValueError):
                    pass
        except (requests.Timeout, requests.ConnectionError):
            category = "transport"
        except (ValueError, AttributeError, requests.RequestException, RuntimeError):
            category = "invalid_ack_or_redirect"
            # Redirect destinations are never followed; configuration needs repair.
            retryable = False
    blocked = not retryable or attempts >= MAX_DELIVERIES
    delay = max(retry_after, min(60, 2 ** min(attempts - 1, 6)) + random.uniform(0, 1))
    jobs.update_one(owned, {"$set": {"delivery.state": "blocked" if blocked else "pending",
        "delivery.lastError": category, "delivery.nextAt": now + timedelta(seconds=delay)},
        "$unset": {"delivery.token": "", "delivery.leaseUntil": ""}})
    LOG.warning("callback delivery %s job=%s sequence=%s category=%s", "blocked" if blocked else "retry", job["_id"], payload["sequence"], category)
    return True


def delivery_loop(jobs, stop):
    while not stop.is_set():
        try:
            delivered = deliver_one(jobs)
        except Exception:
            # A DB outage must not terminate the relay. The persisted lease makes
            # an interrupted delivery eligible again after restart/lease expiry.
            LOG.exception("callback relay unavailable")
            delivered = False
        if not delivered:
            stop.wait(2)


def retry_blocked(jobs):
    """Operator-triggered retry after repairing the cause; never rerun AI work."""
    return jobs.update_many({"delivery.state": "blocked"}, {"$set": {
        "delivery.state": "pending", "delivery.attempts": 0, "delivery.nextAt": now_utc()}}).modified_count
