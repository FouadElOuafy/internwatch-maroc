import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from functools import wraps

import numpy as np
from flask import (Flask, abort, flash, jsonify, redirect, render_template_string,
                   request, session, url_for)
from sentence_transformers import SentenceTransformer

from matching import DB_PATH, MODEL_NAME, clean_text, embed_missing, ensure_column
from dbutil import acquire, connect, holder, release
from notifier import add_subscriber, init_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")  # a changer !

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "internwatch-dev-key-change-me")
_model = None
PER_PAGE = 12

SKILLS = [
    "python", "java", "javascript", "sql", "react", "php", "laravel", "excel",
    "power bi", "machine learning", "intelligence artificielle", "data",
    "marketing", "comptabilité", "finance", "ressources humaines", "logistique",
    "maintenance", "qualité", "web",
]

# ---------------------------------------------------------------- LAYOUT
LAYOUT = """
<!doctype html><html lang="fr"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} | InternWatch Maroc</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
 body{background:#f4f6fb}
 .navbar-brand{font-weight:700;letter-spacing:.3px}
 .hero{background:linear-gradient(135deg,#0d6efd,#6610f2);color:#fff;border-radius:1rem}
 .kpi,.offer,.card-soft{border:0;border-radius:1rem;box-shadow:0 2px 10px rgba(0,0,0,.06)}
 .kpi .icon{width:48px;height:48px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:1.4rem}
 .offer{transition:transform .15s,box-shadow .15s}
 .offer:hover{transform:translateY(-2px);box-shadow:0 6px 18px rgba(0,0,0,.1)}
 pre.joblog{background:#10151f;color:#cfe3ff;border-radius:.75rem;padding:1rem;max-height:280px;overflow:auto;font-size:.82rem;white-space:pre-wrap}
 .chart-box{position:relative;height:260px}
</style></head><body>
<nav class="navbar navbar-expand-lg navbar-dark bg-dark mb-4">
 <div class="container">
  <a class="navbar-brand" href="{{ url_for('index') }}"><i class="bi bi-radar text-primary"></i> InternWatch Maroc</a>
  <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#nav"><span class="navbar-toggler-icon"></span></button>
  <div class="collapse navbar-collapse" id="nav">
   <ul class="navbar-nav me-auto">
    <li class="nav-item"><a class="nav-link {{ 'active' if request.endpoint=='index' }}" href="{{ url_for('index') }}"><i class="bi bi-briefcase"></i> Offres</a></li>
    <li class="nav-item"><a class="nav-link {{ 'active' if request.endpoint=='subscribe' }}" href="{{ url_for('subscribe') }}"><i class="bi bi-bell"></i> S'abonner</a></li>
    <li class="nav-item"><a class="nav-link {{ 'active' if request.endpoint=='stats' }}" href="{{ url_for('stats') }}"><i class="bi bi-bar-chart"></i> Statistiques</a></li>
   </ul>
   <ul class="navbar-nav">
    {% if session.get('admin') %}
     <li class="nav-item"><a class="nav-link {{ 'active' if request.endpoint=='admin' }}" href="{{ url_for('admin') }}"><i class="bi bi-speedometer2"></i> Dashboard admin</a></li>
     <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_logout') }}"><i class="bi bi-box-arrow-right"></i> Déconnexion</a></li>
    {% else %}
     <li class="nav-item"><a class="nav-link" href="{{ url_for('admin_login') }}"><i class="bi bi-shield-lock"></i> Admin</a></li>
    {% endif %}
   </ul>
  </div>
 </div>
</nav>
<main class="container pb-5">
 {% with msgs = get_flashed_messages(with_categories=true) %}
  {% for cat, m in msgs %}<div class="alert alert-{{ cat }} alert-dismissible fade show">{{ m }}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}
 {% endwith %}
 {{ body|safe }}
</main>
<footer class="text-center text-muted small pb-4">Projet Web Analytics &amp; Language Representation Learning</footer>
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
</body></html>
"""

# ---------------------------------------------------------------- PAGES UTILISATEUR
HOME = """
<div class="hero p-4 p-md-5 mb-4">
 <h1 class="fw-bold mb-2"><i class="bi bi-search"></i> Trouve ton stage au Maroc</h1>
 <p class="lead mb-4">Offres collectées automatiquement sur plusieurs sites et classées selon la ressemblance de sens avec ton profil.</p>
 <form method="get" class="row g-2">
  <div class="col-md-6"><input class="form-control form-control-lg" name="q" value="{{ q }}" placeholder="Ex : data science python à Casablanca"></div>
  <div class="col-md-3"><select name="source" class="form-select form-select-lg">
    <option value="">Tous les sites</option>
    {% for s in sources %}<option value="{{ s }}" {{ 'selected' if s==cur_source }}>{{ s }}</option>{% endfor %}
  </select></div>
  <div class="col-md-3 d-grid"><button class="btn btn-light btn-lg fw-semibold"><i class="bi bi-search"></i> Rechercher</button></div>
 </form>
 <div class="mt-3 small opacity-75">{{ k.total }} offres · {{ k.subs }} abonnés · dernière mise à jour : {{ k.last or '-' }}</div>
</div>

<h5 class="mb-3">{% if q %}Résultats les plus proches de ta recherche{% else %}Dernières offres détectées{% endif %}
 <span class="text-muted fs-6">({{ count }})</span></h5>

{% if not cards %}<div class="alert alert-secondary">Aucune offre trouvée.</div>{% endif %}
<div class="row g-3">
{% for c in cards %}
 <div class="col-md-6"><div class="card offer h-100"><div class="card-body">
  <div class="d-flex justify-content-between align-items-start gap-2">
   <h5 class="card-title mb-2"><a class="text-decoration-none" href="{{ url_for('go', row_id=c.id) }}" target="_blank">{{ c.title }}</a></h5>
   {% if c.score is not none %}<span class="badge text-bg-primary fs-6">{{ c.score }}%</span>{% endif %}
  </div>
  <div class="mb-2">
   <span class="badge text-bg-{{ 'success' if c.source.startswith('dream') else 'info' }}">{{ c.source }}</span>
   {% if c.contract %}<span class="badge text-bg-light border">{{ c.contract }}</span>{% endif %}
   {% if c.company %}<span class="badge text-bg-light border"><i class="bi bi-building"></i> {{ c.company }}</span>{% endif %}
   {% if c.location %}<span class="badge text-bg-light border"><i class="bi bi-geo-alt"></i> {{ c.location }}</span>{% endif %}
   {% if c.date %}<span class="badge text-bg-light border"><i class="bi bi-calendar"></i> {{ c.date }}</span>{% endif %}
  </div>
  <p class="card-text text-muted small mb-2">{{ c.snippet }}</p>
  {% if c.score is not none %}<div class="progress" style="height:6px"><div class="progress-bar" style="width:{{ c.score }}%"></div></div>{% endif %}
 </div></div></div>
{% endfor %}
</div>

{% if pages > 1 %}
<nav class="mt-4"><ul class="pagination justify-content-center">
 <li class="page-item {{ 'disabled' if page<=1 }}"><a class="page-link" href="{{ url_for('index', p=page-1, source=cur_source) }}">Précédent</a></li>
 <li class="page-item disabled"><span class="page-link">Page {{ page }} / {{ pages }}</span></li>
 <li class="page-item {{ 'disabled' if page>=pages }}"><a class="page-link" href="{{ url_for('index', p=page+1, source=cur_source) }}">Suivant</a></li>
</ul></nav>
{% endif %}
"""

SUB = """
<div class="row g-4">
 <div class="col-lg-7">
  <div class="card card-soft"><div class="card-body p-4">
   <h3 class="mb-3"><i class="bi bi-bell text-primary"></i> Recevoir les nouvelles offres</h3>
   <form method="post">
    <div class="mb-3"><label class="form-label">Email</label><input name="email" type="email" class="form-control" required></div>
    <div class="mb-3"><label class="form-label">Ton profil (compétences, domaine)</label>
     <textarea name="profile" rows="3" class="form-control" required placeholder="ex : data science, machine learning, python"></textarea></div>
    <div class="mb-3"><label class="form-label">Sélectivité</label>
     <select name="threshold" class="form-select">
      <option value="0.50">Stricte : seulement les offres très proches</option>
      <option value="0.30" selected>Équilibrée</option>
      <option value="0.20">Large : plus d'offres, moins précises</option>
     </select></div>
    <button class="btn btn-primary"><i class="bi bi-check2-circle"></i> S'abonner</button>
    <a class="btn btn-link" href="{{ url_for('unsubscribe') }}">Se désabonner</a>
   </form>
  </div></div>
 </div>
 <div class="col-lg-5">
  <div class="card card-soft h-100"><div class="card-body p-4">
   <h5 class="mb-3">Comment ça marche ?</h5>
   <ol class="text-muted">
    <li class="mb-2">Le système collecte régulièrement les offres de plusieurs sites.</li>
    <li class="mb-2">Il repère uniquement les <b>nouvelles</b> offres.</li>
    <li class="mb-2">Il compare leur sens avec ton profil (embeddings).</li>
    <li>Tu reçois par email celles qui dépassent ton seuil, sans doublons.</li>
   </ol>
  </div></div>
 </div>
</div>
"""

UNSUB = """
<div class="row justify-content-center"><div class="col-md-6">
 <div class="card card-soft"><div class="card-body p-4">
  <h4 class="mb-3">Se désabonner</h4>
  <form method="post">
   <div class="mb-3"><label class="form-label">Email</label><input name="email" type="email" class="form-control" required></div>
   <button class="btn btn-outline-danger">Je me désabonne</button>
  </form>
 </div></div>
</div></div>
"""

CHART_JS = """
<script>
const D = {{ d|tojson }};
const PAL=['#0d6efd','#6610f2','#d63384','#fd7e14','#198754','#20c997','#0dcaf0','#ffc107','#6c757d','#dc3545'];
const L=x=>x.map(i=>i[0]), V=x=>x.map(i=>i[1]);
function mk(id,type,rows,label,horiz){
 const el=document.getElementById(id); if(!el) return;
 if(!rows||!rows.length){el.parentElement.innerHTML='<p class="text-muted small mb-0">Pas encore de données.</p>';return;}
 new Chart(el,{type:type,data:{labels:L(rows),datasets:[{label:label,data:V(rows),
  backgroundColor:type==='line'?'rgba(13,110,253,.15)':PAL,borderColor:'#0d6efd',borderRadius:6,fill:type==='line',tension:.3}]},
  options:{maintainAspectRatio:false,indexAxis:horiz?'y':'x',
   plugins:{legend:{display:type==='doughnut'}},
   scales:type==='doughnut'?{}:{x:{beginAtZero:true,ticks:{precision:0}},y:{beginAtZero:true,ticks:{precision:0}}}}});
}
mk('chSource','doughnut',D.per_source,'Offres');
mk('chDay','line',D.per_day,'Nouvelles offres');
mk('chCities','bar',D.cities,'Offres',true);
mk('chSkills','bar',D.skills,'Offres',true);
mk('chCats','bar',D.categories,'Offres',true);
</script>
"""

STATS = """
<h3 class="mb-3"><i class="bi bi-bar-chart text-primary"></i> Statistiques</h3>
<div class="row g-3">
 <div class="col-md-4"><div class="card card-soft"><div class="card-body"><h6><span class="badge text-bg-primary">Web Content</span> Offres par site</h6><div class="chart-box"><canvas id="chSource"></canvas></div></div></div></div>
 <div class="col-md-8"><div class="card card-soft"><div class="card-body"><h6><span class="badge text-bg-primary">Web Content</span> Villes les plus actives</h6><div class="chart-box"><canvas id="chCities"></canvas></div></div></div></div>
 <div class="col-md-6"><div class="card card-soft"><div class="card-body"><h6><span class="badge text-bg-primary">Web Content</span> Compétences et domaines demandés</h6><div class="chart-box"><canvas id="chSkills"></canvas></div></div></div></div>
 <div class="col-md-6"><div class="card card-soft"><div class="card-body"><h6><span class="badge text-bg-warning">Web Structure</span> Offres par catégorie du site</h6><div class="chart-box"><canvas id="chCats"></canvas></div></div></div></div>
 <div class="col-md-6"><div class="card card-soft"><div class="card-body"><h6><span class="badge text-bg-success">Web Usage</span> Offres les plus cliquées</h6>
   {% for n,c in d.clicks %}<div class="d-flex justify-content-between border-bottom py-1 small"><span>{{ n }}</span><b>{{ c }}</b></div>{% else %}<p class="text-muted small mb-0">Pas encore de clics.</p>{% endfor %}
 </div></div></div>
 <div class="col-md-6"><div class="card card-soft"><div class="card-body"><h6><span class="badge text-bg-success">Web Usage</span> Recherches les plus fréquentes</h6>
   {% for n,c in d.searches %}<div class="d-flex justify-content-between border-bottom py-1 small"><span>{{ n }}</span><b>{{ c }}</b></div>{% else %}<p class="text-muted small mb-0">Pas encore de recherches.</p>{% endfor %}
 </div></div></div>
</div>
""" + CHART_JS

# ---------------------------------------------------------------- ADMIN
LOGIN = """
<div class="row justify-content-center"><div class="col-md-5">
 <div class="card card-soft"><div class="card-body p-4">
  <h4 class="mb-3"><i class="bi bi-shield-lock text-primary"></i> Connexion administrateur</h4>
  <form method="post">
   <div class="mb-3"><label class="form-label">Mot de passe</label><input name="password" type="password" class="form-control" required autofocus></div>
   <button class="btn btn-primary w-100">Se connecter</button>
  </form>
 </div></div>
</div></div>
"""

ADMIN = """
<div class="d-flex justify-content-between align-items-center mb-3">
 <h3 class="mb-0"><i class="bi bi-speedometer2 text-primary"></i> Dashboard administrateur</h3>
 <span class="text-muted small">Dernière offre vue : {{ k.last or '-' }}</span>
</div>

<div class="row g-3 mb-3">
 {% for icon,color,label,val in [('briefcase','primary','Offres',k.total),('stars','success','Nouvelles (24 h)',k.new24),
   ('people','info','Abonnés actifs',k.subs),('envelope-check','warning','Offres envoyées',k.sent),
   ('cursor','danger','Clics',k.clicks),('search','secondary','Recherches',k.searches)] %}
 <div class="col-6 col-lg-2"><div class="card kpi"><div class="card-body d-flex align-items-center gap-3">
  <div class="icon bg-{{ color }}-subtle text-{{ color }}"><i class="bi bi-{{ icon }}"></i></div>
  <div><div class="fs-4 fw-bold">{{ val }}</div><div class="text-muted small">{{ label }}</div></div>
 </div></div></div>
 {% endfor %}
</div>

<div class="card card-soft mb-3"><div class="card-body">
 <h5 class="mb-3"><i class="bi bi-gear"></i> Actions</h5>
 <div class="d-flex flex-wrap gap-2 align-items-center">
  <form method="post" action="{{ url_for('admin_run', task='scrape') }}"><button class="btn btn-primary" {{ 'disabled' if job.running }}><i class="bi bi-cloud-download"></i> Lancer le scraping (2 sites)</button></form>
  <form method="post" action="{{ url_for('admin_run', task='notify') }}" class="d-flex gap-2 align-items-center">
   <button class="btn btn-success" {{ 'disabled' if job.running }}><i class="bi bi-send"></i> Envoyer les notifications</button>
   <div class="form-check"><input class="form-check-input" type="checkbox" name="real" id="real"><label class="form-check-label small" for="real">envoi réel (sinon test dans le dossier outbox)</label></div>
  </form>
 </div>
 <pre class="joblog mt-3" id="joblog">{{ job.log }}</pre>
 <div class="small text-muted" id="jobstate">{% if job.running %}Tâche en cours : {{ job.name }} (depuis {{ job.started }}){% else %}Aucune tâche en cours.{% endif %}</div>
</div></div>

<div class="row g-3 mb-3">
 <div class="col-md-4"><div class="card card-soft"><div class="card-body"><h6>Offres par site</h6><div class="chart-box"><canvas id="chSource"></canvas></div></div></div></div>
 <div class="col-md-8"><div class="card card-soft"><div class="card-body"><h6>Nouvelles offres détectées par jour</h6><div class="chart-box"><canvas id="chDay"></canvas></div></div></div></div>
 <div class="col-md-6"><div class="card card-soft"><div class="card-body"><h6>Villes</h6><div class="chart-box"><canvas id="chCities"></canvas></div></div></div></div>
 <div class="col-md-6"><div class="card card-soft"><div class="card-body"><h6>Compétences et domaines</h6><div class="chart-box"><canvas id="chSkills"></canvas></div></div></div></div>
</div>

<div class="card card-soft mb-3"><div class="card-body">
 <h5 class="mb-3"><i class="bi bi-people"></i> Abonnés ({{ subs|length }})</h5>
 <div class="table-responsive"><table class="table table-hover align-middle small">
  <thead><tr><th>Email</th><th>Profil</th><th>Seuil</th><th>Reçues</th><th>Statut</th><th></th></tr></thead>
  <tbody>
  {% for s in subs %}
   <tr><td>{{ s.email }}</td><td class="text-muted">{{ s.profile[:60] }}</td><td>{{ (s.threshold*100)|round|int }}%</td><td>{{ s.sent }}</td>
    <td>{% if s.active %}<span class="badge text-bg-success">actif</span>{% else %}<span class="badge text-bg-secondary">inactif</span>{% endif %}</td>
    <td class="text-end">
     <form method="post" action="{{ url_for('admin_sub', sub_id=s.id, action='toggle') }}" class="d-inline"><button class="btn btn-sm btn-outline-secondary">{{ 'Désactiver' if s.active else 'Activer' }}</button></form>
     <form method="post" action="{{ url_for('admin_sub', sub_id=s.id, action='delete') }}" class="d-inline" onsubmit="return confirm('Supprimer cet abonné ?')"><button class="btn btn-sm btn-outline-danger"><i class="bi bi-trash"></i></button></form>
    </td></tr>
  {% else %}<tr><td colspan="6" class="text-muted">Aucun abonné.</td></tr>{% endfor %}
  </tbody></table></div>
</div></div>

<div class="card card-soft"><div class="card-body">
 <h5 class="mb-3"><i class="bi bi-envelope"></i> Dernières notifications envoyées</h5>
 <div class="table-responsive"><table class="table table-sm align-middle small">
  <thead><tr><th>Date</th><th>Abonné</th><th>Offre</th><th>Similarité</th></tr></thead><tbody>
  {% for n in notifs %}<tr><td>{{ n.sent_at }}</td><td>{{ n.email }}</td><td>{{ n.title[:70] }}</td><td>{{ (n.similarity*100)|round|int }}%</td></tr>
  {% else %}<tr><td colspan="4" class="text-muted">Aucune notification.</td></tr>{% endfor %}
 </tbody></table></div>
</div></div>

<script>
let wasRunning = {{ 'true' if job.running else 'false' }};
setInterval(async () => {
 try {
  const r = await fetch('{{ url_for("admin_job") }}'); const j = await r.json();
  document.getElementById('joblog').textContent = j.log;
  document.getElementById('jobstate').textContent = j.running ? 'Tâche en cours : ' + j.name + ' (depuis ' + j.started + ')' : 'Aucune tâche en cours.';
  if (wasRunning && !j.running) { location.reload(); }
  wasRunning = j.running;
 } catch(e) {}
}, 3000);
</script>
""" + CHART_JS


# ---------------------------------------------------------------- OUTILS
def render_page(title, body_tpl, **ctx):
    body = render_template_string(body_tpl, **ctx)
    return render_template_string(LAYOUT, title=title, body=body)


def db():
    conn = connect()
    conn.row_factory = sqlite3.Row
    return conn


def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def ensure_embeddings():
    """Calcule les vecteurs des offres nouvelles (apres un scraping par exemple)."""
    c = connect()
    missing = c.execute("SELECT COUNT(*) FROM offers WHERE embedding IS NULL").fetchone()[0]
    if missing:
        embed_missing(c, get_model())
    c.close()


def init():
    c = connect()
    ensure_column(c)
    init_db(c)
    c.execute(
        """CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT,
            offer_row_id INTEGER, query TEXT, created_at TEXT)"""
    )
    c.commit()
    c.close()


def log_event(kind, offer_row_id=None, query=None):
    c = connect()
    c.execute(
        "INSERT INTO usage_events (event_type, offer_row_id, query, created_at) VALUES (?,?,?,?)",
        (kind, offer_row_id, query, datetime.now().isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def make_card(r, score=None):
    text = r["description"] or r["content"] or ""
    snippet = re.sub(r"\s+", " ", text).strip()[:190]
    return {
        "id": r["id"], "title": r["title"], "source": r["source"],
        "company": r["company"], "location": r["location"], "contract": r["contract"],
        "date": r["date_text"], "snippet": snippet + ("..." if len(text) > 190 else ""),
        "score": None if score is None else int(round(score * 100)),
    }


def kpis(conn):
    one = lambda sql, p=(): conn.execute(sql, p).fetchone()[0]
    since = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    return {
        "total": one("SELECT COUNT(*) FROM offers"),
        "new24": one("SELECT COUNT(*) FROM offers WHERE first_seen >= ?", (since,)),
        "subs": one("SELECT COUNT(*) FROM subscribers WHERE active=1"),
        "sent": one("SELECT COUNT(*) FROM notifications"),
        "clicks": one("SELECT COUNT(*) FROM usage_events WHERE event_type='click'"),
        "searches": one("SELECT COUNT(*) FROM usage_events WHERE event_type='search'"),
        "last": one("SELECT MAX(last_seen) FROM offers"),
    }


def collect_stats(conn):
    def q(sql, p=()):
        return [[(r[0] if r[0] not in (None, "") else "-"), r[1]] for r in conn.execute(sql, p).fetchall()]

    d = {
        "per_source": q("SELECT source, COUNT(*) FROM offers GROUP BY source ORDER BY 2 DESC"),
        "per_day": q("SELECT substr(first_seen,1,10), COUNT(*) FROM offers GROUP BY 1 ORDER BY 1"),
        "categories": q("SELECT category, COUNT(*) FROM offers WHERE category IS NOT NULL GROUP BY category ORDER BY 2 DESC LIMIT 10"),
        "clicks": q(
            "SELECT substr(o.title,1,60), COUNT(*) FROM usage_events e JOIN offers o ON o.id=e.offer_row_id "
            "WHERE e.event_type='click' GROUP BY o.id ORDER BY 2 DESC LIMIT 6"),
        "searches": q(
            "SELECT query, COUNT(*) FROM usage_events WHERE event_type='search' "
            "GROUP BY query ORDER BY 2 DESC LIMIT 6"),
    }
    city_count, skill_count = {}, {}
    for r in conn.execute("SELECT title, description, content, location FROM offers"):
        city = (r["location"] or "").split(",")[0].strip()
        if city:
            city_count[city] = city_count.get(city, 0) + 1
        text = f"{r['title']} {r['description'] or ''} {(r['content'] or '')[:1500]}".lower()
        for sk in SKILLS:
            if re.search(r"\b" + re.escape(sk) + r"\b", text):
                skill_count[sk] = skill_count.get(sk, 0) + 1
    d["cities"] = [list(x) for x in sorted(city_count.items(), key=lambda x: -x[1])[:8]]
    d["skills"] = [list(x) for x in sorted(skill_count.items(), key=lambda x: -x[1])[:10]]
    return d


# ---------------------------------------------------------------- TACHES ADMIN
JOB = {"running": False, "name": "", "started": "", "log": "Aucune tâche lancée."}
LOCK = threading.Lock()
KEYWORDS = ("BILAN BASE", "item_scraped_count", "nouvelle", "ERREUR", "ECHEC", "email", "Traceback", "Error")


def run_job(name, steps):
    with LOCK:
        if JOB["running"]:
            return False
        if not acquire("dashboard: " + name):
            JOB["log"] = f"Refuse : une autre tache est en cours ({holder()})."
            return False
        JOB.update(running=True, name=name, started=datetime.now().strftime("%H:%M:%S"),
                   log=f"{name} démarré...\n")

    def target():
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        for label, cmd in steps:
            JOB["log"] += f"\n> {label}\n"
            try:
                p = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", env=env)
                lines = (p.stdout + "\n" + p.stderr).strip().splitlines()
                keep = [ln for ln in lines if any(k in ln for k in KEYWORDS)]
                JOB["log"] += "\n".join((keep or lines)[-12:]) + f"\n(code retour {p.returncode})\n"
            except Exception as e:  # noqa: BLE001
                JOB["log"] += f"Erreur : {e}\n"
        JOB["log"] += "\nTerminé."
        release()
        JOB["running"] = False

    threading.Thread(target=target, daemon=True).start()
    return True


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*a, **kw)
    return wrapper


# ---------------------------------------------------------------- ROUTES UTILISATEUR
@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    source = request.args.get("source", "").strip()
    try:
        page = max(int(request.args.get("p", "1")), 1)
    except ValueError:
        page = 1
    conn = db()
    sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM offers ORDER BY 1")]
    k = kpis(conn)
    cols = "id, source, title, company, location, contract, date_text, description, content"
    where, params = ("WHERE source = ?", [source]) if source else ("", [])
    pages = 1

    if q:
        ensure_embeddings()
        rows = conn.execute(
            f"SELECT {cols}, embedding FROM offers {where}{' AND' if where else 'WHERE'} embedding IS NOT NULL",
            params).fetchall()
        cards, count = [], 0
        if rows:
            mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
            s = mat @ get_model().encode([clean_text(q)], normalize_embeddings=True)[0]
            cards = [make_card(rows[i], float(s[i])) for i in np.argsort(-s)[:20]]
            count = len(cards)
        log_event("search", query=q.lower())
    else:
        count = conn.execute(f"SELECT COUNT(*) FROM offers {where}", params).fetchone()[0]
        pages = max((count + PER_PAGE - 1) // PER_PAGE, 1)
        page = min(page, pages)
        rows = conn.execute(
            f"SELECT {cols} FROM offers {where} ORDER BY first_seen DESC, id DESC LIMIT ? OFFSET ?",
            params + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
        cards = [make_card(r) for r in rows]
    conn.close()
    return render_page("Offres", HOME, q=q, cur_source=source, sources=sources, cards=cards,
                       count=count, page=page, pages=pages, k=k)


@app.route("/go/<int:row_id>")
def go(row_id):
    conn = db()
    r = conn.execute("SELECT url FROM offers WHERE id=?", (row_id,)).fetchone()
    conn.close()
    if not r:
        abort(404)
    log_event("click", offer_row_id=row_id)
    return redirect(r["url"])


@app.route("/subscribe", methods=["GET", "POST"])
def subscribe():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        profile = request.form.get("profile", "").strip()
        try:
            thr = float(request.form.get("threshold", "0.30"))
        except ValueError:
            thr = 0.30
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or not profile:
            flash("Email ou profil invalide.", "danger")
        else:
            c = connect()
            add_subscriber(c, email, profile, thr)
            c.close()
            flash(f"Abonnement enregistré pour {email}.", "success")
        return redirect(url_for("subscribe"))
    return render_page("S'abonner", SUB)


@app.route("/unsubscribe", methods=["GET", "POST"])
def unsubscribe():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        c = connect()
        n = c.execute("UPDATE subscribers SET active=0 WHERE email=?", (email,)).rowcount
        c.commit()
        c.close()
        flash("Tu es désabonné." if n else "Cet email n'est pas inscrit.", "success" if n else "warning")
        return redirect(url_for("unsubscribe"))
    return render_page("Se désabonner", UNSUB)


@app.route("/stats")
def stats():
    conn = db()
    d = collect_stats(conn)
    conn.close()
    return render_page("Statistiques", STATS, d=d)


# ---------------------------------------------------------------- ROUTES ADMIN
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Mot de passe incorrect.", "danger")
    return render_page("Connexion admin", LOGIN)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin():
    conn = db()
    k = kpis(conn)
    d = collect_stats(conn)
    subs = conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM notifications n WHERE n.subscriber_id=s.id) AS sent "
        "FROM subscribers s ORDER BY s.id").fetchall()
    notifs = conn.execute(
        "SELECT n.sent_at, s.email, o.title, n.similarity FROM notifications n "
        "JOIN subscribers s ON s.id=n.subscriber_id JOIN offers o ON o.id=n.offer_row_id "
        "ORDER BY n.sent_at DESC LIMIT 15").fetchall()
    conn.close()
    return render_page("Dashboard admin", ADMIN, k=k, d=d, subs=subs, notifs=notifs, job=JOB)


@app.route("/admin/job")
@admin_required
def admin_job():
    return jsonify(JOB)


@app.route("/admin/run/<task>", methods=["POST"])
@admin_required
def admin_run(task):
    py = sys.executable
    if task == "scrape":
        steps = [("Scraping Jobsquare", [py, "-m", "scrapy", "crawl", "jobsquare", "-s", "LOG_LEVEL=INFO"]),
                 ("Scraping Dreamjob", [py, "-m", "scrapy", "crawl", "dreamjob", "-s", "LOG_LEVEL=INFO"])]
        name = "Scraping"
    elif task == "notify":
        cmd = [py, "notifier.py", "run"] + (["--send"] if request.form.get("real") else [])
        steps = [("Détection et envoi des notifications", cmd)]
        name = "Notifications"
    else:
        abort(404)
    if not run_job(name, steps):
        flash("Une tâche est déjà en cours (dashboard ou planificateur). Réessaie dans quelques minutes.", "warning")
    return redirect(url_for("admin"))


@app.route("/admin/sub/<int:sub_id>/<action>", methods=["POST"])
@admin_required
def admin_sub(sub_id, action):
    c = connect()
    if action == "toggle":
        c.execute("UPDATE subscribers SET active = 1 - active WHERE id=?", (sub_id,))
    elif action == "delete":
        c.execute("DELETE FROM notifications WHERE subscriber_id=?", (sub_id,))
        c.execute("DELETE FROM subscribers WHERE id=?", (sub_id,))
    c.commit()
    c.close()
    return redirect(url_for("admin"))


if __name__ == "__main__":
    init()
    app.run(debug=False)