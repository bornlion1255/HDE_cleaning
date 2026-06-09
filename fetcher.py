"""
fetcher.py — HDE API fetch for Streamlit.
Uses in-memory SQLite, returns pandas DataFrame.
Adapted from HDE/hde_fetch.py.
"""

import json
import re
import sqlite3
import time
from datetime import datetime
from typing import Callable

import pandas as pd
import requests

HDE_BASE = "https://qlean2.helpdeskeddy.com/api/v2"
DELAY    = 0.25  # 4 req/sec — safely under 300 RPM

DATE_FIELD_MAP = {
    "created": ("from_date_created", "to_date_created"),
    "updated": ("from_date_updated", "to_date_updated"),
    "closed":  ("from_date_closed",  "to_date_closed"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticket_field_defs (id INTEGER PRIMARY KEY, name TEXT, field_type TEXT);
CREATE TABLE IF NOT EXISTS user_field_defs   (id INTEGER PRIMARY KEY, name TEXT, field_type TEXT);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY, pid INTEGER, unique_id TEXT,
    date_created TEXT, date_updated TEXT, title TEXT, source TEXT,
    status_id TEXT, priority_id INTEGER, type_id INTEGER,
    department_id INTEGER, department_name TEXT,
    owner_id INTEGER, owner_name TEXT, owner_lastname TEXT, owner_email TEXT,
    user_id INTEGER, user_name TEXT, user_lastname TEXT, user_email TEXT,
    sla_date TEXT, sla_flag INTEGER, ticket_lock INTEGER, freeze INTEGER,
    freeze_date TEXT, deleted INTEGER, viewed_by_staff INTEGER, viewed_by_client INTEGER,
    rate TEXT, rate_comment TEXT, rate_date TEXT, tags TEXT, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS ticket_custom_fields (
    ticket_id INTEGER, field_id INTEGER, field_type TEXT, field_value TEXT,
    PRIMARY KEY (ticket_id, field_id)
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT, lastname TEXT, email TEXT, phone TEXT,
    org_id TEXT, org_name TEXT, status TEXT, group_type TEXT, group_name TEXT, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS user_custom_fields (
    user_id INTEGER, field_id INTEGER, field_type TEXT, field_value TEXT,
    PRIMARY KEY (user_id, field_id)
);
CREATE TABLE IF NOT EXISTS ticket_posts (
    ticket_id INTEGER, post_id INTEGER, author_user_id INTEGER,
    is_client INTEGER, text TEXT, date_created TEXT,
    PRIMARY KEY (ticket_id, post_id)
);
"""

_TICKET_STD = [
    "id", "pid", "unique_id", "date_created", "date_updated",
    "title", "source", "status_id", "priority_id", "type_id",
    "department_id", "department_name",
    "owner_id", "owner_name", "owner_lastname", "owner_email",
    "user_id", "user_name", "user_lastname", "user_email",
    "sla_date", "sla_flag", "ticket_lock", "freeze", "freeze_date",
    "deleted", "viewed_by_staff", "viewed_by_client",
    "rate", "rate_comment", "rate_date", "tags",
]
_USER_BASE = ["user_phone", "user_org", "user_status", "user_group_type", "user_group_name"]
_POST_EXTRA = [
    "link_staff", "ticket_user_post_count", "ticket_staff_post_count",
    "first_response_sec", "avg_response_sec",
]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_get(auth: tuple) -> Callable:
    def _get(path: str, params: dict | None = None) -> dict:
        url = f"{HDE_BASE}{path}"
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, auth=auth, timeout=30,
                                 proxies={"http": None, "https": None})
                if r.status_code == 429:
                    time.sleep(60)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2)
        return {}
    return _get


def _normalize_cf(field_type: str, raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, dict):
        name = raw.get("name", {})
        if isinstance(name, dict):
            return name.get("ru") or name.get("en") or str(raw.get("id", ""))
        return str(raw.get("id", ""))
    if field_type == "checkbox":
        return "1" if raw else "0"
    return str(raw)


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = (text.replace("&lt;", "<").replace("&gt;", ">")
            .replace("&amp;", "&").replace("&nbsp;", " ").replace("&quot;", '"'))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%H:%M:%S %d.%m.%Y",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


# ── Fetch stages ──────────────────────────────────────────────────────────────

def _fetch_field_defs(conn: sqlite3.Connection, _get: Callable) -> None:
    for endpoint, table in [
        ("/custom_fields/",        "ticket_field_defs"),
        ("/users/custom_fields/",  "user_field_defs"),
    ]:
        page = 1
        while True:
            try:
                data = _get(endpoint, {"page": page}).get("data", {})
            except Exception:
                break
            if not data:
                break
            for fdef in data.values():
                name_data = fdef.get("name", {})
                if isinstance(name_data, dict):
                    first = next(iter(name_data.values()), "")
                    name = (first.get("ru") or first.get("en") if isinstance(first, dict)
                            else name_data.get("ru") or name_data.get("en") or str(fdef["id"]))
                else:
                    name = str(fdef["id"])
                conn.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)",
                             (fdef["id"], name, fdef.get("field_type")))
            if len(data) < 30:
                break
            page += 1
            time.sleep(DELAY)
    conn.commit()


def _fetch_tickets(conn: sqlite3.Connection, _get: Callable,
                   date_from: str, date_to: str, date_field: str,
                   on_status: Callable | None) -> tuple[set[int], int]:
    from_key, to_key = DATE_FIELD_MAP[date_field]
    params = {
        "order_by": "date_created{asc}",
        from_key:   f"{date_from} 00:00:00",
        to_key:     f"{date_to} 23:59:59",
    }
    fetched_at = datetime.now().isoformat()
    page, saved = 1, 0
    user_ids: set[int] = set()

    while True:
        params["page"] = page
        resp = _get("/tickets/", params)
        time.sleep(DELAY)

        raw        = resp.get("data", {})
        pagination = resp.get("pagination", {})
        tickets    = list(raw.values()) if isinstance(raw, dict) else raw
        if not tickets:
            break

        total_pages = int(pagination.get("total_pages", 1))
        if page == 1 and on_status:
            on_status(f"Найдено {pagination.get('total', '?')} заявок — загружаю...")

        for t in tickets:
            tid = t.get("id")
            if not tid:
                continue
            conn.execute(
                f"INSERT OR REPLACE INTO tickets VALUES "
                f"(:id,:pid,:unique_id,:date_created,:date_updated,:title,:source,"
                f":status_id,:priority_id,:type_id,:department_id,:department_name,"
                f":owner_id,:owner_name,:owner_lastname,:owner_email,"
                f":user_id,:user_name,:user_lastname,:user_email,"
                f":sla_date,:sla_flag,:ticket_lock,:freeze,:freeze_date,:deleted,"
                f":viewed_by_staff,:viewed_by_client,:rate,:rate_comment,:rate_date,"
                f":tags,:fetched_at)",
                {
                    "id": tid, "pid": t.get("pid"), "unique_id": t.get("unique_id"),
                    "date_created": t.get("date_created"), "date_updated": t.get("date_updated"),
                    "title": t.get("title"), "source": t.get("source"),
                    "status_id": t.get("status_id"), "priority_id": t.get("priority_id"),
                    "type_id": t.get("type_id"),
                    "department_id": t.get("department_id"),
                    "department_name": t.get("department_name"),
                    "owner_id": t.get("owner_id"), "owner_name": t.get("owner_name"),
                    "owner_lastname": t.get("owner_lastname"), "owner_email": t.get("owner_email"),
                    "user_id": t.get("user_id"), "user_name": t.get("user_name"),
                    "user_lastname": t.get("user_lastname"), "user_email": t.get("user_email"),
                    "sla_date": t.get("sla_date"), "sla_flag": t.get("sla_flag"),
                    "ticket_lock": t.get("ticket_lock"), "freeze": t.get("freeze"),
                    "freeze_date": str(t.get("freeze_date") or ""),
                    "deleted": t.get("deleted"),
                    "viewed_by_staff": t.get("viewed_by_staff"),
                    "viewed_by_client": t.get("viewed_by_client"),
                    "rate": str(t.get("rate") or ""), "rate_comment": str(t.get("rate_comment") or ""),
                    "rate_date": str(t.get("rate_date") or ""),
                    "tags": json.dumps(t.get("tags") or [], ensure_ascii=False),
                    "fetched_at": fetched_at,
                },
            )
            for cf in t.get("custom_fields") or []:
                conn.execute(
                    "INSERT OR REPLACE INTO ticket_custom_fields VALUES (?,?,?,?)",
                    (tid, cf.get("id"), cf.get("field_type", ""),
                     _normalize_cf(cf.get("field_type", ""), cf.get("field_value"))),
                )
            if t.get("user_id"):
                user_ids.add(t["user_id"])
            saved += 1

        conn.commit()
        if on_status:
            on_status(f"Заявки: страница {page}/{total_pages} ({saved} загружено)")
        if page >= total_pages:
            break
        page += 1

    return user_ids, saved


def _fetch_users(conn: sqlite3.Connection, _get: Callable,
                 user_ids: set[int], on_status: Callable | None) -> None:
    existing = {r[0] for r in conn.execute("SELECT id FROM users")}
    to_fetch = sorted(user_ids - existing)
    if not to_fetch:
        return

    for i, uid in enumerate(to_fetch, 1):
        try:
            resp = _get(f"/users/{uid}/")
            time.sleep(DELAY)
        except Exception:
            continue
        data = resp.get("data", [])
        user = data[0] if isinstance(data, list) and data else data
        if not isinstance(user, dict):
            continue
        org   = user.get("organization") or {}
        group = user.get("group") or {}
        grp_name = (group.get("name") or {}).get("ru", "") if isinstance(group, dict) else ""
        fetched_at = datetime.now().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                user.get("id"), user.get("name", ""), user.get("lastname", ""),
                user.get("email", ""), user.get("phone", ""),
                str(org.get("id", "") if isinstance(org, dict) else ""),
                org.get("name", "") if isinstance(org, dict) else str(org),
                user.get("status", ""),
                group.get("type", "") if isinstance(group, dict) else "",
                grp_name, fetched_at,
            ),
        )
        for cf in user.get("custom_fields") or []:
            conn.execute(
                "INSERT OR REPLACE INTO user_custom_fields VALUES (?,?,?,?)",
                (user.get("id"), cf.get("id"), cf.get("field_type", ""),
                 _normalize_cf(cf.get("field_type", ""), cf.get("field_value"))),
            )
        if i % 30 == 0:
            conn.commit()
            if on_status:
                on_status(f"Пользователи: {i}/{len(to_fetch)}")

    conn.commit()


def _fetch_posts(conn: sqlite3.Connection, _get: Callable,
                 ticket_ids: list[int], client_uids: dict[int, int],
                 on_status: Callable | None) -> None:
    total = len(ticket_ids)
    for i, tid in enumerate(ticket_ids, 1):
        client_uid = client_uids.get(tid)
        all_posts: list = []
        page = 1
        while True:
            try:
                resp = _get(f"/tickets/{tid}/posts/", {"page": page})
                time.sleep(DELAY)
            except Exception:
                break
            posts = resp.get("data", [])
            pagination = resp.get("pagination", {})
            if not posts:
                break
            all_posts.extend(posts)
            if page >= int(pagination.get("total_pages", 1)):
                break
            page += 1

        for p in all_posts:
            author_id = p.get("user_id")
            is_client = 1 if (author_id and author_id == client_uid) else 0
            conn.execute(
                "INSERT OR REPLACE INTO ticket_posts VALUES (?,?,?,?,?,?)",
                (tid, p.get("id"), author_id, is_client,
                 _strip_html(p.get("text") or ""), p.get("date_created")),
            )

        if i % 25 == 0:
            conn.commit()
            if on_status:
                on_status(f"Посты: {i}/{total} заявок обработано")

    conn.commit()


# ── DataFrame builder (port of export_csv) ────────────────────────────────────

def _build_df(conn: sqlite3.Connection) -> pd.DataFrame:
    ticket_fields = dict(conn.execute("SELECT id, name FROM ticket_field_defs ORDER BY id"))
    user_fields   = dict(conn.execute("SELECT id, name FROM user_field_defs   ORDER BY id"))

    tcf: dict[int, dict] = {}
    for tid, fid, _, val in conn.execute(
        "SELECT ticket_id, field_id, field_type, field_value FROM ticket_custom_fields"
    ):
        tcf.setdefault(tid, {})[fid] = val

    ucf: dict[int, dict] = {}
    for uid, fid, _, val in conn.execute(
        "SELECT user_id, field_id, field_type, field_value FROM user_custom_fields"
    ):
        ucf.setdefault(uid, {})[fid] = val

    users: dict[int, dict] = {}
    for row in conn.execute(
        "SELECT id, phone, org_name, status, group_type, group_name FROM users"
    ):
        users[row[0]] = {
            "user_phone":      row[1],
            "user_org":        row[2],
            "user_status":     row[3],
            "user_group_type": row[4],
            "user_group_name": row[5],
        }

    posts_by: dict[int, list] = {}
    for tid, author_id, is_client, text, date_created in conn.execute(
        "SELECT ticket_id, author_user_id, is_client, text, date_created "
        "FROM ticket_posts ORDER BY ticket_id, post_id ASC"
    ):
        posts_by.setdefault(tid, []).append({
            "author_user_id": author_id,
            "is_client": is_client,
            "text": text,
            "date_created": date_created,
        })

    tcf_ids  = sorted(ticket_fields.keys())
    ucf_ids  = sorted(user_fields.keys())
    extra_ucf = sorted({fid for d in ucf.values() for fid in d} - set(ucf_ids))
    ucf_ids  += extra_ucf

    tcf_cols = [f"tcf_{fid}_{ticket_fields[fid]}" for fid in tcf_ids]
    ucf_cols = [f"ucf_{fid}_{user_fields.get(fid, fid)}" for fid in ucf_ids]

    rows = []
    for row in conn.execute(
        f"SELECT {', '.join(_TICKET_STD)} FROM tickets ORDER BY date_created"
    ):
        tid = row[0]
        uid = row[_TICKET_STD.index("user_id")]
        u   = users.get(uid, {})

        posts     = posts_by.get(tid, [])
        user_cnt  = sum(1 for p in posts if p["is_client"])
        staff_cnt = sum(1 for p in posts if not p["is_client"])
        link      = f"https://qlean2.helpdeskeddy.com/ru/ticket/list/filter/id/{tid}/ticket/{tid}"

        created_str = row[_TICKET_STD.index("date_created")]
        first_staff = next((p for p in posts if not p["is_client"]), None)
        first_resp  = ""
        if first_staff and first_staff["date_created"] and created_str:
            t_dt = _parse_dt(created_str)
            s_dt = _parse_dt(first_staff["date_created"])
            if t_dt and s_dt:
                first_resp = max(0, int((s_dt - t_dt).total_seconds()))

        resp_times = []
        for idx, p in enumerate(posts):
            if p["is_client"] == 1:
                for nxt in posts[idx + 1:]:
                    if nxt["is_client"] == 0:
                        c_dt = _parse_dt(p["date_created"])
                        s_dt = _parse_dt(nxt["date_created"])
                        if c_dt and s_dt and (s_dt - c_dt).total_seconds() >= 0:
                            resp_times.append((s_dt - c_dt).total_seconds())
                        break
        avg_resp = round(sum(resp_times) / len(resp_times)) if resp_times else ""

        tcf_vals = [tcf.get(tid, {}).get(fid, "") for fid in tcf_ids]
        ucf_vals = [ucf.get(uid, {}).get(fid, "") for fid in ucf_ids]

        rows.append(
            list(row)
            + [u.get(c, "") for c in _USER_BASE]
            + [link, user_cnt, staff_cnt, first_resp, avg_resp]
            + tcf_vals
            + ucf_vals
        )

    columns = _TICKET_STD + _USER_BASE + _POST_EXTRA + tcf_cols + ucf_cols
    return pd.DataFrame(rows, columns=columns).fillna("")


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_all(
    email: str,
    api_key: str,
    date_from: str,
    date_to: str,
    date_field: str = "created",
    on_status: Callable | None = None,
) -> pd.DataFrame:
    """
    Fetch tickets from HDE API for the given period.
    Returns a pandas DataFrame identical in structure to tickets_flat.csv.
    on_status: optional callback(message: str) for progress reporting.
    """
    def log(msg: str) -> None:
        if on_status:
            on_status(msg)

    auth = (email, api_key)
    _get = _make_get(auth)

    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.commit()

    log("Загружаю структуру полей...")
    _fetch_field_defs(conn, _get)

    log("Загружаю заявки...")
    user_ids, n_tickets = _fetch_tickets(conn, _get, date_from, date_to, date_field, log)
    log(f"Заявки готовы: {n_tickets} шт. Загружаю пользователей...")

    _fetch_users(conn, _get, user_ids, log)

    ticket_ids   = [r[0] for r in conn.execute("SELECT id FROM tickets")]
    client_uids  = dict(conn.execute("SELECT id, user_id FROM tickets"))
    log(f"Загружаю посты для {len(ticket_ids)} заявок (это занимает время)...")
    _fetch_posts(conn, _get, ticket_ids, client_uids, log)

    log("Формирую таблицу данных...")
    df = _build_df(conn)
    conn.close()

    log(f"Готово: {len(df)} заявок × {len(df.columns)} колонок")
    return df
