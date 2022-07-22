from pathlib import Path
import sqlite3
from sqlite3 import Row

from models import SnitchChannel

db_path = Path(__file__).parent / "snitchvis.db"

def create_db():
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE "snitch_channel" (
            `guild_id` INTEGER NOT NULL,
            `channel_id` INTEGER NOT NULL,
            PRIMARY KEY(`channel_id`)
        )""")
    conn.close()

if not db_path.exists():
    create_db()
conn = sqlite3.connect(str(db_path))
conn.row_factory = Row
cur = conn.cursor()

def select(query, params):
    return cur.execute(query, params).fetchall()

def execute(query, params):
    cur.execute(query, params)
    conn.commit()

## snitch channels

def get_snitch_channels(guild):
    rows = select("SELECT * FROM snitch_channel WHERE guild_id = ?", [guild.id])
    channels = []
    for row in rows:
        kwargs = dict(zip(row.keys(), list(row)))
        channels.append(SnitchChannel(**kwargs))
    return channels

def add_snitch_channel(channel):
    execute("INSERT INTO snitch_channel VALUES (?, ?)",
        [channel.guild.id, channel.id])

def remove_snitch_channel(channel):
    execute("DELETE FROM snitch_channel WHERE channel_id = ?", [channel.id])

def snitch_channel_exists(channel):
    rows = select("SELECT * FROM snitch_channel WHERE channel_id = ?",
        [channel.id])
    return bool(rows)
