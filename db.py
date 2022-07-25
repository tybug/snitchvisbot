from pathlib import Path
import sqlite3
from sqlite3 import Row

from models import SnitchChannel, Event, Snitch

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
    c.execute(
        # schema matches gjum's snitchmod snitches_v2 table
        """
        CREATE TABLE snitches (
            guild_id INT,
            world TEXT,
            x INT,
            y INT,
            z INT,
            group_name TEXT,
            type TEXT,
            name TEXT,
            dormant_ts BIGINT,
            cull_ts BIGINT,
            first_seen_ts BIGINT,
            last_seen_ts BIGINT,
            created_ts BIGINT,
            created_by_uuid TEXT,
            renamed_ts BIGINT,
            renamed_by_uuid TEXT,
            lost_jalist_access_ts BIGINT,
            broken_ts BIGINT,
            gone_ts BIGINT,
            tags TEXT,
            notes TEXT,
            PRIMARY KEY (world,x,y,z))
        """
    )
    conn.commit()
    conn.close()

if not db_path.exists():
    create_db()
conn = sqlite3.connect(str(db_path))
conn.row_factory = Row
cur = conn.cursor()

def commit():
    conn.commit()

def select(query, params=[]):
    return cur.execute(query, params).fetchall()

def execute(query, params, commit_=True):
    cur_ = cur.execute(query, params)
    if commit_:
        commit()
    return cur_

def convert(rows, Class):
    intances = []
    for row in rows:
        kwargs = dict(zip(row.keys(), list(row)))
        intances.append(Class(**kwargs))
    return intances

## snitches

def get_snitches(guild):
    rows = select("SELECT * FROM snitches WHERE guild_id = ?", [guild.id])
    return convert(rows, Snitch)

def add_snitch(guild, snitch, commit=True):
    args = [
        guild.id, snitch.world, snitch.x, snitch.y, snitch.z, snitch.group_name,
        snitch.type, snitch.name, snitch.dormat_ts, snitch.cull_ts,
        snitch.first_seen_ts, snitch.last_seen_ts, snitch.created_ts,
        snitch.created_by_uuid, snitch.renamde_ts, snitch.renamed_by_uuid,
        snitch.lost_jalist_access_ts, snitch.broken_ts, snitch.gone_ts,
        snitch.tags, snitch.notes
    ]
    # ignore duplicate snitches
    return execute("INSERT OR IGNORE INTO snitches VALUES (?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", args, commit)

## snitch channels

def get_snitch_channels(guild):
    rows = select("SELECT * FROM snitch_channel WHERE guild_id = ?", [guild.id])
    return convert(rows, SnitchChannel)

def add_snitch_channel(channel):
    return execute("INSERT INTO snitch_channel (guild_id, id) VALUES (?, ?)",
        [channel.guild.id, channel.id])

def remove_snitch_channel(channel):
    return execute("DELETE FROM snitch_channel WHERE id = ?", [channel.id])

def snitch_channel_exists(channel):
    rows = select("SELECT * FROM snitch_channel WHERE id = ?",
        [channel.id])
    return bool(rows)

def is_snitch_channel(channel):
    rows = select("SELECT * FROM snitch_channel WHERE id = ?",
        [channel.id])
    return bool(rows)

def get_snitch_channel(channel):
    rows = select("SELECT * FROM snitch_channel WHERE id = ?",
        [channel.id])
    if not rows:
        return None
    return convert(rows, SnitchChannel)[0]

def update_last_indexed(channel, message_id):
    return execute("UPDATE snitch_channel SET last_indexed_id = ? WHERE id = ?",
        [message_id, channel.id])

## events

def add_event(message, event, commit=True):
    # use the message's timestmap instead of trying to parse the event.
    # Some servers might have crazy snitch log formats, and the default format
    # doesn't even include the day/month/year, so we would be partially relying
    # on the message's timestamp by default anyway to tell us the calendar date.
    # Still, this might result in events which are a few seconds off from when
    # they actually occurred, or potentially more if kira got desynced or
    # backlogged.
    t = message.created_at.timestamp()
    return execute("INSERT INTO event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [message.id, message.channel.id, message.guild.id, event.username,
         event.snitch_name, event.namelayer_group, event.x, event.y, event.z,
         t], commit)

def event_exists(message_id):
    rows = select("SELECT * FROM event WHERE message_id = ?", [message_id])
    return bool(rows)

def most_recent_event(guild):
    rows = select("SELECT * FROM event WHERE guild_id = ? ORDER BY t DESC "
        "LIMIT 1", [guild.id])
    if not rows:
        return None

    return convert(rows, Event)[0]

def get_events(guild, start_date, end_date, users):
    # compare case insensitive
    users = [user.lower() for user in users]

    # XXX be careful no sql injection can happen here
    if users:
        qs = '?, ' * len(users)
        qs = qs[:-2] # remove trailing `, `
        user_filter = f"LOWER(username) IN ({qs})"
    else:
        # always true
        user_filter = "1"

    rows = select(
        f"""
        SELECT * FROM event
        WHERE guild_id = ? AND t >= ? AND t <= ? AND {user_filter}
        """,
        [guild.id, start_date, end_date, *users]
    )
    return convert(rows, Event)

def get_all_events(guild):
    rows = select("SELECT * FROM event WHERE guild_id = ?", [guild.id])
    return convert(rows, Event)
