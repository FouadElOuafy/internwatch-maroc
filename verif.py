import sqlite3

c = sqlite3.connect("offers.db")
print("offres par site :", c.execute("SELECT source, COUNT(*) FROM offers GROUP BY source").fetchall())
print("abonnes actifs :", c.execute("SELECT COUNT(*) FROM subscribers WHERE active=1").fetchone()[0])
print("notifications :", c.execute("SELECT COUNT(*) FROM notifications").fetchone()[0])
print("publicites :", c.execute("SELECT COUNT(*) FROM offers WHERE content LIKE ?", ("%agit pas d%une offre%",)).fetchone()[0])