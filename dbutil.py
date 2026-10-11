"""Outils communs : connexion SQLite robuste + verrou pour ne pas lancer 2 taches en meme temps."""
import os
import sqlite3
import time

DB_PATH = "offers.db"
LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task.lock")
STALE_SECONDS = 45 * 60  # un verrou plus vieux que 45 min est considere comme abandonne


def connect(path=DB_PATH):
    """Connexion qui attend jusqu'a 30 s au lieu d'echouer avec 'database is locked'."""
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")  # lecture et ecriture en parallele
    except sqlite3.Error:
        pass
    return conn


def acquire(owner):
    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, owner.encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(LOCK_PATH) > STALE_SECONDS:
                    os.remove(LOCK_PATH)
                    continue
            except OSError:
                pass
            return False
    return False


def release():
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def holder():
    try:
        with open(LOCK_PATH, encoding="utf-8") as f:
            return f.read().strip() or "inconnu"
    except OSError:
        return None