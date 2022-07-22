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
            `id` INTEGER NOT NULL,
            `last_indexed_id` INTEGER,
            PRIMARY KEY(`id`)
        )
        """)
    c.execute(
        """
        CREATE TABLE "event" (
            `message_id` INTEGER NOT NULL,
            `channel_id` INTEGER NOT NULL,
            `guild_id` INTEGER NOT NULL,
            `username` TEXT NOT NULL,
            `snitch_name` TEXT,
            `namelayer_group` TEXT NOT NULL,
            `x` INTEGER NOT NULL,
            `y` INTEGER NOT NULL,
            `z` INTEGER NOT NULL,
            `t` INTEGER NOT NULL,
            PRIMARY KEY(`message_id`)
        )
        """
    )
    conn.commit()
    conn.close()

if not db_path.exists():
    create_db()
conn = sqlite3.connect(str(db_path))
conn.row_factory = Row
cur = conn.cursor()

def select(query, params=[]):
    return cur.execute(query, params).fetchall()

def execute(query, params):
    cur.execute(query, params)
    conn.commit()

def convert(rows, Class):
    intances = []
    for row in rows:
        kwargs = dict(zip(row.keys(), list(row)))
        intances.append(Class(**kwargs))
    return intances

## snitch channels

def get_snitch_channels(guild):
    rows = select("SELECT * FROM snitch_channel WHERE guild_id = ?", [guild.id])
    return convert(rows, SnitchChannel)

def add_snitch_channel(channel):
    execute("INSERT INTO snitch_channel (guild_id, id) VALUES (?, ?)",
        [channel.guild.id, channel.id])

def remove_snitch_channel(channel):
    execute("DELETE FROM snitch_channel WHERE id = ?", [channel.id])

def snitch_channel_exists(channel):
    rows = select("SELECT * FROM snitch_channel WHERE id = ?",
        [channel.id])
    return bool(rows)

def update_last_indexed(channel, message_id):
    execute("UPDATE snitch_channel SET last_indexed_id = ? WHERE id = ?",
        [message_id, channel.id])

## events

def add_event(message, event):
    # use the message's timestmap instead of trying to parse the event.
    # Some servers might have crazy snitch log formats, and the default format
    # doesn't even include the day/month/year, so we would be partially relying
    # on the message's timestamp by default anyway to tell us the calendar date.
    # Still, this might result in events which are a few seconds off from when
    # they actually occurred, or potentially more if kira got desynced or
    # backlogged.
    t = message.created_at.timestamp()
    execute("INSERT INTO event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [message.id, message.channel.id, message.guild.id, event.username,
         event.snitch_name, event.namelayer_group, event.x, event.y, event.z,
         t])

def event_exists(message_id):
    rows = select("SELECT * FROM event WHERE message_id = ?", [message_id])
    return bool(rows)
