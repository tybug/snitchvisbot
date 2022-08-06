from pathlib import Path
import sqlite3
from sqlite3 import Row
from collections import defaultdict
import inspect
import time

from models import SnitchChannel, Event, Snitch

db_path = Path(__file__).parent / "snitchvis.db"

def create_db():
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE snitch_channel (
            `guild_id` INTEGER NOT NULL,
            `id` INTEGER NOT NULL,
            `last_indexed_id` INTEGER,
            PRIMARY KEY(`id`)
        )
        """)
    c.execute(
        """
        CREATE TABLE event (
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
        # schema matches gjum's snitchmod snitches_v2 table, with a few of our
        # own rows added
        """
        CREATE TABLE snitch (
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
            notes TEXT
        )
        """
    )
    c.execute("""
        CREATE UNIQUE INDEX snitch_world_x_y_z_unique
        ON snitch(world, x, y, z);
    """)
    # junction table between snitch_channel and roles
    c.execute(
        """
        CREATE TABLE snitch_channel_allowed_roles (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL
        )
        """
    )
    # junction table between snitches and roles
    c.execute(
        """
        CREATE TABLE snitch_allowed_roles (
            snitch_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL
        )
        """
    )
    c.execute("""
        CREATE TABLE render_history (
            guild_id INTEGER NOT NULL,
            pixel_usage INTEGER NOT NULL,
            timestamp INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE guild (
            guild_id INTEGER NOT NULL,
            prefix TEXT,
            PRIMARY KEY(guild_id)
        )
    """)

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
    return execute(query, params, commit_=False).fetchall()

def execute(query, params, commit_=True):
    t1 = time.time()
    cur_ = cur.execute(query, params)
    t2 = time.time()
    # performance logging for sql queries
    print(f"[db] {t2 - t1} {query} {params}")

    if commit_:
        commit()
    return cur_

def convert(rows, Class):
    instances = []
    for row in rows:
        if isinstance(row, Row):
            values = list(row)
        else:
            values = row.values()

        kwargs = dict(zip(row.keys(), values))

        # Extraneous parameters not relevant to `Class` can sneak in via sql
        # joins. Filter this out to avoid errors on instantation.
        parameters = list(inspect.signature(Class.__init__).parameters)
        kwargs_ = {}
        for k, v in kwargs.items():
            if k in parameters:
                kwargs_[k] = v

        instances.append(Class(**kwargs_))
    return instances

## snitches

def get_snitches(guild, roles):
    role_filter = ("?, " * len(roles))[:-2]
    role_ids = [role.id for role in roles]
    rows = select(f"""
        SELECT * FROM snitch
        JOIN snitch_allowed_roles
        ON snitch.rowid = snitch_allowed_roles.snitch_id
        WHERE snitch.guild_id = ? AND
        snitch_allowed_roles.role_id IN ({role_filter})
        """,
        [guild.id, *role_ids]
    )
    return convert(rows, Snitch)

def add_snitch(guild, snitch, allowed_roles, commit=True):
    args = [
        guild.id, snitch.world, snitch.x, snitch.y, snitch.z, snitch.group_name,
        snitch.type, snitch.name, snitch.dormant_ts, snitch.cull_ts,
        snitch.first_seen_ts, snitch.last_seen_ts, snitch.created_ts,
        snitch.created_by_uuid, snitch.renamed_ts, snitch.renamed_by_uuid,
        snitch.lost_jalist_access_ts, snitch.broken_ts, snitch.gone_ts,
        snitch.tags, snitch.notes
    ]
    # ignore duplicate snitches
    cur = execute("INSERT OR IGNORE INTO snitch VALUES (?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", args, commit)
    rowcount = cur.rowcount

    for role in allowed_roles:
        execute("INSERT OR IGNORE INTO snitch_allowed_roles VALUES (?, ?)",
        [cur.lastrowid, role.id], commit)

    return rowcount

## snitch channels

def get_snitch_channels(guild):
    # if guild is none then ALL snitch channels will be returned

    guild_filter = "WHERE snitch_channel.guild_id = ?" if guild else ""
    rows = select(f"""
        SELECT * FROM snitch_channel
        JOIN snitch_channel_allowed_roles
        ON snitch_channel.id = snitch_channel_allowed_roles.channel_id
        {guild_filter}
        """,
        [guild.id] if guild else []
    )
    # manually aggregate allowed role ids into a list for our convert function
    # channel id to list of dictionaries (channel k/v dicts).
    new_rows = {}
    for row in rows:
        if row["channel_id"] not in new_rows:
            new_row = dict(zip(row.keys(), list(row)))
            new_row["allowed_roles"] = []
        else:
            new_row = new_rows[row["channel_id"]]
        new_row["allowed_roles"].append(int(row["role_id"]))
        new_rows[row["channel_id"]] = new_row

    return convert(new_rows.values(), SnitchChannel)

def add_snitch_channel(channel, roles):
    execute("INSERT INTO snitch_channel (guild_id, id) VALUES (?, ?)",
        [channel.guild.id, channel.id], commit_=False)

    for role in roles:
        execute("INSERT INTO snitch_channel_allowed_roles VALUES (?, ?, ?)",
            [channel.guild.id, channel.id, role.id], commit_=False)

    # batch commit in case we're adding a ton of roles/channels, probably not
    # necessary
    commit()

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

def allowed_roles(snitch_channel):
    rows = select("SELECT * FROM snitch_channel_allowed_roles WHERE "
        "channel_id = ?", [snitch_channel.id])
    return [row["role_id"] for row in rows]

def get_snitch_channel(channel):
    rows = select("SELECT * FROM snitch_channel WHERE id = ?",
        [channel.id])
    if not rows:
        return None

    # jump through some hoops because "sqlite3.Row doesn't support item
    # assignment"
    row = rows[0]
    row = dict(zip(row.keys(), list(row)))
    row["allowed_roles"] = allowed_roles(channel)
    return convert([row], SnitchChannel)[0]

def update_last_indexed(channel, message_id, commit=True):
    return execute("UPDATE snitch_channel SET last_indexed_id = ? WHERE id = ?",
        [message_id, channel.id], commit_=commit)

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

def get_events(guild, author, start_date, end_date, users, groups):
    # compare case insensitive
    users = [user.lower() for user in users]

    # XXX be careful no sql injection can happen here
    if users:
        qs = '?, ' * len(users)
        qs = qs[:-2] # remove trailing `, `
        user_filter = f"LOWER(username) IN ({qs})"
    else:
        # if no users are passed, all events are fair game
        user_filter = "1"

    if groups:
        qs = '?, ' * len(groups)
        qs = qs[:-2]
        group_filter = f"LOWER(namelayer_group) IN ({qs})"
    else:
        # if no users are passed, all events are fair game
        group_filter = "1"


    snitch_channels = get_snitch_channels(guild)
    channel_ids = set()

    # TODO can we do this filtering based on allowed roles entirely in sql?
    # Probably not worth it until/if it becomes a performance concern.

    # build a dict to avoid potentially cubic behavior. This is still quadratic,
    # but hopefully no more than a few hundred iterations at worst.
    role_to_channels = defaultdict(set)
    for channel in snitch_channels:
        for role in channel.allowed_roles:
            role_to_channels[role].add(channel.id)

    # for each role, add any snitch channels that role gives them permission to
    # view.
    for role in author.roles:
        channels = role_to_channels[role.id]
        channel_ids |= (channels)

    if channel_ids:
        qs = '?, ' * len(channel_ids)
        qs = qs[:-2]
        channel_filter = f"channel_id IN ({qs})"
    else:
        # if the author doesn't have permission to view any channels, don't
        # return any events
        channel_filter = "0"

    rows = select(
        f"""
        SELECT * FROM event
        WHERE guild_id = ?
        AND t >= ? AND t <= ?
        AND {user_filter}
        AND {channel_filter}
        AND {group_filter}
        """,
        [guild.id, start_date, end_date, *users, *channel_ids, *groups]
    )
    return convert(rows, Event)

def get_all_events(guild):
    rows = select("SELECT * FROM event WHERE guild_id = ?", [guild.id])
    return convert(rows, Event)

## render history

def get_pixel_usage(guild, start, end):
    rows = select("""
        SELECT * FROM render_history
        WHERE guild_id = ? AND timestamp > ? AND timestamp < ?
    """, [guild.id, start, end])

    pixel_usage = 0
    for row in rows:
        pixel_usage += row["pixel_usage"]

    return pixel_usage

def add_render_history(guild, pixel_usage, timestamp):
    execute("INSERT INTO render_history VALUES (?, ?, ?)",
        [guild.id, pixel_usage, timestamp])

## guilds

def create_new_guild(guild):
    # default to null prefix. ignore error on guilds we're rejoining for a
    # second time, they already have a proper row
    execute("INSERT OR IGNORE INTO guild VALUES (?, null)", [guild.id])

def get_snitch_prefix(guild):
    rows = select("SELECT * FROM guild WHERE guild_id = ?", [guild.id])

    if not rows:
        print("attempted to retrieve the prefix of a guild that doesn't exist. "
            "This should never happen")
        return None

    return rows[0]["prefix"]

def set_guild_prefix(guild, prefix):
    execute("UPDATE guild SET prefix = ? WHERE guild_id = ?",
        [prefix, guild.id])
