import sqlite3

conn = sqlite3.connect('instance/database.db')
conn.row_factory = sqlite3.Row

print('=== TABLES ===')
for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(t['name'])

print('\n=== USER_DOWNLOADS SCHEMA ===')
for col in conn.execute("PRAGMA table_info(user_downloads)"):
    print(col[1], col[2])

print('\n=== LAST 10 ROWS IN USER_DOWNLOADS ===')
for row in conn.execute("SELECT * FROM user_downloads ORDER BY id DESC LIMIT 10"):
    print(dict(row))

print('\n=== INGESTION_LOGS LAST 10 ROWS ===')
for row in conn.execute("SELECT * FROM ingestion_logs ORDER BY id DESC LIMIT 10"):
    print(dict(row))
