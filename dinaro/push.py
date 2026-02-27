"""Dinaro Web Push Notification helpers."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from pywebpush import webpush, WebPushException
from sqlalchemy import text

from database import engine, get_db_connection as get_connection

logger = logging.getLogger(__name__)

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CLAIMS = {"sub": os.environ.get("VAPID_CLAIM_EMAIL", "mailto:hello@thetimecost.com")}


def _get_subscriptions(family_id: int, user_type: str, user_id: int | None = None) -> list[dict]:
    conn = get_connection()
    try:
        if user_id is not None:
            rows = conn.execute(
                text(
                    "SELECT id, endpoint, p256dh, auth FROM push_subscriptions "
                    "WHERE family_id = :fid AND user_type = :ut AND user_id = :uid"
                ),
                {"fid": family_id, "ut": user_type, "uid": user_id},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    "SELECT id, endpoint, p256dh, auth FROM push_subscriptions "
                    "WHERE family_id = :fid AND user_type = :ut"
                ),
                {"fid": family_id, "ut": user_type},
            ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _remove_subscription(sub_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM push_subscriptions WHERE id = :id"), {"id": sub_id})


def _send_push(sub_row: dict, payload: dict) -> bool:
    if not VAPID_PRIVATE_KEY:
        return False

    subscription_info = {
        "endpoint": sub_row["endpoint"],
        "keys": {"p256dh": sub_row["p256dh"], "auth": sub_row["auth"]},
    }

    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
            ttl=86400,
        )
        return True
    except WebPushException as e:
        if hasattr(e, "response") and e.response is not None:
            if e.response.status_code in (404, 410):
                _remove_subscription(sub_row["id"])
                return False
        logger.warning("Push failed for sub %s: %s", sub_row["id"], e)
        return False
    except Exception as e:
        logger.warning("Push error: %s", e)
        return False


def notify_parents(family_id: int, title: str, body: str, url: str = "/dinaro/parent") -> None:
    subs = _get_subscriptions(family_id, "parent")
    payload = {"title": title, "body": body, "url": url, "icon": "/static/favicon.svg"}
    for sub in subs:
        _send_push(sub, payload)


def notify_child(family_id: int, child_id: int, title: str, body: str, url: str = "/dinaro/child") -> None:
    subs = _get_subscriptions(family_id, "child", user_id=child_id)
    payload = {"title": title, "body": body, "url": url, "icon": "/static/favicon.svg"}
    for sub in subs:
        _send_push(sub, payload)


def save_subscription(family_id: int, user_type: str, user_id: int, sub_json: dict) -> None:
    endpoint = sub_json["endpoint"]
    p256dh = sub_json["keys"]["p256dh"]
    auth = sub_json["keys"]["auth"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM push_subscriptions WHERE endpoint = :ep"), {"ep": endpoint})
        conn.execute(
            text(
                "INSERT INTO push_subscriptions "
                "(family_id, user_type, user_id, endpoint, p256dh, auth, created_at) "
                "VALUES (:fid, :ut, :uid, :ep, :p256dh, :auth, :now)"
            ),
            {"fid": family_id, "ut": user_type, "uid": user_id,
             "ep": endpoint, "p256dh": p256dh, "auth": auth, "now": now},
        )


def remove_subscription_by_endpoint(endpoint: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM push_subscriptions WHERE endpoint = :ep"), {"ep": endpoint})
