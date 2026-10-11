import re
import sqlite3
import sys

import numpy as np
from sentence_transformers import SentenceTransformer

DB_PATH = "offers.db"
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

# mots presents dans presque toutes les offres : ils faussent la similarite
GENERIC = re.compile(
    r"\b(stage|stagiaire|stagiaires|pfe|h/f|f/h|h-f|remunere|remunere|pre-embauche|"
    r"pré-embauche|rémunéré|mois|poste|postes|maroc)\b",
    re.I,
)


def clean_text(text):
    text = GENERIC.sub(" ", text or "")
    text = re.sub(r"[\(\)\|–\-/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def build_text(title, company, location, description, content):
    body = description or (content or "")[:1000]
    # le titre est repete pour lui donner plus de poids
    t = clean_text(title)
    return clean_text(f"{t}. {t}. {body}")[:1500]


def ensure_column(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(offers)")]
    if "embedding" not in cols:
        conn.execute("ALTER TABLE offers ADD COLUMN embedding BLOB")
        conn.commit()


def embed_missing(conn, model):
    """Calcule les vecteurs uniquement pour les offres qui n'en ont pas encore."""
    rows = conn.execute(
        "SELECT id, title, company, location, description, content "
        "FROM offers WHERE embedding IS NULL"
    ).fetchall()
    if not rows:
        print("Toutes les offres ont deja un vecteur.")
        return
    print(f"Calcul des vecteurs pour {len(rows)} offres...")
    texts = [build_text(*r[1:]) for r in rows]
    vecs = model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=True)
    for r, v in zip(rows, vecs):
        conn.execute(
            "UPDATE offers SET embedding = ? WHERE id = ?",
            (np.asarray(v, dtype=np.float32).tobytes(), r[0]),
        )
    conn.commit()


def search(conn, model, profile, top_k=5):
    q = model.encode([clean_text(profile)], normalize_embeddings=True)[0]
    rows = conn.execute(
        "SELECT source, title, company, location, url, embedding "
        "FROM offers WHERE embedding IS NOT NULL"
    ).fetchall()
    mat = np.vstack([np.frombuffer(r[5], dtype=np.float32) for r in rows])
    scores = mat @ q  # vecteurs normalises : produit scalaire = similarite cosinus
    order = np.argsort(-scores)[:top_k]
    for rank, i in enumerate(order, 1):
        source, title, company, location, url, _ = rows[i]
        print(f"{rank}. [{scores[i] * 100:.0f}%] {title}")
        print(f"   {source} | {company or '-'} | {location or '-'}")
        print(f"   {url}")


if __name__ == "__main__":
    profile = " ".join(sys.argv[1:]) or "data science machine learning python"
    conn = sqlite3.connect(DB_PATH)
    ensure_column(conn)
    model = SentenceTransformer(MODEL_NAME)
    embed_missing(conn, model)
    print(f"\nProfil : {profile}\n")
    search(conn, model, profile)
    conn.close()