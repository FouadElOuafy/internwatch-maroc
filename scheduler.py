"""Planificateur : relance periodiquement scraping + notifications.

Exemples :
    python scheduler.py --now                 (cycle immediat, puis toutes les 6 h)
    python scheduler.py --hours 2 --send      (toutes les 2 h, emails reels)
    python scheduler.py --minutes 10 --now    (pour la demo devant le prof)
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler

from dbutil import acquire, holder, release

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "scheduler.log")
KEYWORDS = ("BILAN BASE", "item_scraped_count", "nouvelle", "ERREUR", "ECHEC", "email", "Traceback")


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(label, cmd):
    log(f"> {label}")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env)
        lines = (p.stdout + "\n" + p.stderr).strip().splitlines()
        for ln in [x for x in lines if any(k in x for k in KEYWORDS)][-8:]:
            log("   " + ln.strip())
        log(f"   code retour {p.returncode}")
    except Exception as e:  # noqa: BLE001
        log(f"   ERREUR : {e}")


def cycle(send):
    if not acquire("scheduler"):
        log(f"Cycle ignore : une autre tache est en cours ({holder()}).")
        return
    try:
        log("=== Cycle demarre ===")
        py = sys.executable
        for spider in ("jobsquare", "dreamjob"):
            run(f"scraping {spider}", [py, "-m", "scrapy", "crawl", spider, "-s", "LOG_LEVEL=INFO"])
        run("notifications", [py, "notifier.py", "run"] + (["--send"] if send else []))
        log("=== Cycle termine ===")
    finally:
        release()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6, help="intervalle en heures (defaut 6)")
    ap.add_argument("--minutes", type=float, default=0, help="intervalle en minutes (prioritaire)")
    ap.add_argument("--send", action="store_true", help="envoi reel des emails (sinon dossier outbox)")
    ap.add_argument("--now", action="store_true", help="lancer un cycle tout de suite")
    a = ap.parse_args()

    # fuseau explicite : evite le decalage "0 != 3600" detecte sur certains PC Windows
    sched = BlockingScheduler(timezone=datetime.now().astimezone().tzinfo)
    every = f"{a.minutes:g} min" if a.minutes else f"{a.hours:g} h"
    log(f"Planificateur actif : un cycle toutes les {every} ({'envoi reel' if a.send else 'mode test'}). Ctrl+C pour arreter.")
    try:
        if a.now:
            cycle(a.send)  # premier cycle lance directement, sans passer par l'horloge
        kw = {"minutes": a.minutes} if a.minutes else {"hours": a.hours}
        sched.add_job(cycle, "interval", args=[a.send], max_instances=1, coalesce=True, **kw)
        log(f"Prochain cycle dans {every}.")
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log("Planificateur arrete.")