import sqlite3
db = sqlite3.connect('vtuber.db')
user = db.execute("SELECT email, last_chat_date, updated_at FROM users WHERE email='hammamkiki2008@gmail.com'").fetchone()
print(user)
