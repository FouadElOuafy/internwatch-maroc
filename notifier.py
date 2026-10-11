import os
import re
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer

from dbutil import connect
from matching import MODEL_NAME, clean_text, embed_missing, ensure_column

MAX_OFFERS_PER_EMAIL = 10


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            profile TEXT NOT NULL,
            threshold REAL DEFAULT 0.30,
            active INTEGER DEFAULT 1,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            subscriber_id INTEGER,
            offer_row_id INTEGER,
            similarity REAL,
            sent_at TEXT,
            PRIMARY KEY (subscriber_id, offer_row_id)
        )
        """
    )
    conn.commit()


def add_subscriber(conn, email, profile, threshold=0.30):
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO subscribers (email, profile, threshold, active, created_at)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(email) DO UPDATE SET
            profile = excluded.profile, threshold = excluded.threshold, active = 1
        """,
        (email, profile, threshold, now),
    )
    conn.commit()
    print(f"Abonne enregistre : {email} (seuil {threshold:.0%})")


def list_subscribers(conn):
    for r in conn.execute("SELECT id, email, threshold, active, profile FROM subscribers"):
        print(f"#{r[0]} {r[1]} | seuil {r[2]:.0%} | actif={r[3]} | profil : {r[4][:70]}")


def build_message(email, picks):
    n = len(picks)
    subject = f"{n} nouvelle(s) offre(s) de stage pour votre profil"
    lines, html = [], ["<h2>InternWatch Maroc</h2><p>Offres pertinentes pour votre profil :</p><ol>"]
    for score, o in picks:
        title, company, location, url, source = o
        lines.append(f"- [{score:.0%}] {title} | {company or '-'} | {location or '-'}\n  {url}")
        html.append(
            f'<li><b><a href="{url}">{title}</a></b> ({score:.0%})<br>'
            f"{company or '-'} | {location or '-'} | {source}</li>"
        )
    html.append("</ol>")
    return subject, "\n".join(lines), "".join(html)


def deliver(email, subject, text, html, send):
    if not send:
        Path("outbox").mkdir(exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9]+", "_", email)
        path = Path("outbox") / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.html"
        path.write_text(f"<html><body><h3>{subject}</h3>{html}</body></html>", encoding="utf-8")
        print(f"   [TEST] email ecrit dans {path}")
        return True
    user, pwd = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pwd:
        print("   ERREUR : variables GMAIL_USER et GMAIL_APP_PASSWORD non definies")
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, email
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pwd.replace(" ", ""))
            s.send_message(msg)
        print(f"   email envoye a {email}")
        return True
    except Exception as e:
        print(f"   ECHEC d'envoi : {e}")
        return False


def run(conn, send=False):
    ensure_column(conn)
    model = SentenceTransformer(MODEL_NAME)
    embed_missing(conn, model)

    offers = conn.execute(
        "SELECT id, title, company, location, url, source, embedding "
        "FROM offers WHERE embedding IS NOT NULL"
    ).fetchall()
    mat = np.vstack([np.frombuffer(o[6], dtype=np.float32) for o in offers])

    subs = conn.execute(
        "SELECT id, email, profile, threshold FROM subscribers WHERE active = 1"
    ).fetchall()
    if not subs:
        print("Aucun abonne actif.")
        return

    for sub_id, email, profile, threshold in subs:
        q = model.encode([clean_text(profile)], normalize_embeddings=True)[0]
        scores = mat @ q
        already = {
            r[0]
            for r in conn.execute(
                "SELECT offer_row_id FROM notifications WHERE subscriber_id = ?", (sub_id,)
            )
        }
        order = np.argsort(-scores)
        picks, ids = [], []
        for i in order:
            if scores[i] < threshold:
                break
            if offers[i][0] in already:
                continue
            picks.append((float(scores[i]), offers[i][1:6]))
            ids.append((offers[i][0], float(scores[i])))
            if len(picks) >= MAX_OFFERS_PER_EMAIL:
                break

        print(f"{email} : {len(picks)} offre(s) nouvelle(s) au-dessus du seuil {threshold:.0%}")
        if not picks:
            continue
        subject, text, html = build_message(email, picks)
        if deliver(email, subject, text, html, send):
            now = datetime.now().isoformat(timespec="seconds")
            conn.executemany(
                "INSERT OR IGNORE INTO notifications VALUES (?, ?, ?, ?)",
                [(sub_id, oid, sim, now) for oid, sim in ids],
            )
            conn.commit()


if __name__ == "__main__":
    args = sys.argv[1:]
    conn = connect()
    init_db(conn)
    if args[:1] == ["add"] and len(args) >= 3:
        thr = float(args[3]) if len(args) > 3 else 0.30
        add_subscriber(conn, args[1], args[2], thr)
    elif args[:1] == ["list"]:
        list_subscribers(conn)
    elif args[:1] == ["run"]:
        run(conn, send="--send" in args)
    else:
        print('Usage : python notifier.py add EMAIL "profil" [seuil] | list | run [--send]')
    conn.close()