import sqlite3

c = sqlite3.connect("offers.db")
print("Offres par source :", c.execute("SELECT source, COUNT(*) FROM offers GROUP BY source").fetchall())
print("Publicites restantes :", c.execute("SELECT COUNT(*) FROM offers WHERE content LIKE '%agit pas d%une offre%'").fetchone()[0])
print("Exemples :")
for r in c.execute("SELECT source, title FROM offers ORDER BY id LIMIT 5"):
    print("  ", r)