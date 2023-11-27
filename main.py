from datetime import datetime, timedelta
from tempfile import NamedTemporaryFile, TemporaryDirectory
import sqlite3
from pathlib import Path
from asyncio import Queue
from concurrent.futures import ProcessPoolExecutor
from functools import partial
import gzip
from collections import defaultdict
import re
import traceback
import random

from discord import File
from discord.utils import utcnow
from discord.ext.tasks import loop
from snitchvis import (Event, InvalidEventException, SnitchVisRecord,
    create_users, snitches_from_events, Snitch, Config, SnitchVisImage)
from PyQt6.QtWidgets import QApplication

import db
import utils
import config
from models import KiraConfig, FakeMessage
from command import (command, Arg, channel, role, human_timedelta,
    human_datetime, bounds)
from client import Client

INVITE_LINK = ("https://discord.com/oauth2/authorize?client_id="
    "999808708131426434&permissions=0&scope=bot")

def run_snitch_vis(*args):
    vis = SnitchVisRecord(*args)
    vis.render()

def run_image_render(*args):
    vis = SnitchVisImage(*args)
    vis.render()

class Snitchvis(Client):
    # for reference, a 5 second video of 700 pixels at 30 fps is 70 million
    # pixels. A 60 second video of 1000 pixels at 30 fps is 1.8 billion pixels.
    PIXEL_LIMIT_VIDEO =    7_500_000_000
    # 500 billion pixels is roughly an hour of 1080p @ 60fps.
    PIXEL_LIMIT_DAY   =  500_000_000_000
    # number of maximum concurrent renders allowed per guild
    MAXIMUM_CONCURRENT_RENDERS = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # there's a potential race condition when indexing messages on startup,
        # where we spend x seconds indexing channels before some channel c,
        # but than at y < x seconds a new message comes in to channel c which
        # gets processed and causes the last indexed id to be set to a very high
        # id, causing us not to index the messages we didn't see while we were
        # down when self.index_channel gets called on c.
        # To prevent this, we'll stop indexing new messages at all while
        # indexing channels on startup, and instead stick the new messages into
        # a queue. This queue will be processed in the order the messages were
        # received once we're done indexing the channels and can be assured we
        # won't mess up our last_indexed_id.
        self.defer_indexing = False
        self.indexing_queue = Queue()
        # channel id to datetime
        self.livemap_last_uploaded = {}
        # channel id to list of datetimes
        self.livemaps_refresh_at = {}
        # currently updating livemap ids, so we don't double-update on quick
        # successive snitch hits
        self.livemap_updating_channels = []
        # guilds which we're currently indexing, so we don't double-index
        self.indexing_guilds = []
        # guild id to number of currently running renders. used to limit number
        # of concurrent renders to prevent abuse
        self.concurrent_renders = defaultdict(int)
        self.help_order = [
            self.render,
            self.channel_add,
            self.channel_remove,
            self.channel_list,
            self.set_livemap_channel,
            self.create_command,
            self.list_commands,
            self.import_snitches,
            self.add_kira_config,
            self.events,
            self.permissions,
            self.index,
            self.full_reindex,
            self.set_prefix,
            self.tutorial,
            self.invite,
            self.help
        ]

        self.default_kira_config = KiraConfig(None, "`[%TIME%]` `[%GROUP%]` "
            "**%PLAYER%** %ACTION% at %SNITCH% (%X%,%Y%,%Z%) %PING%",
            "is", "logged in", "logged out", "HH:mm:ss")

    async def on_ready(self):
        await super().on_ready()
        print("connected to discord")
        # avoid last_indexed_id getting set to a wrong value by incoming
        # messages while we index channels
        self.defer_indexing = True
        # index any messages sent while we were down
        for channel in db.get_snitch_channels(None):
            c = self.get_channel(channel.id)
            if c is None:
                # we were kicked from the guild or the channel was deleted
                continue
            permissions = c.permissions_for(c.guild.me)
            if not permissions.read_messages:
                print(f"Couldn't index {c} / {c.id} (guild {c.guild} / "
                    f"{c.guild.id}) without read_messages permission")
                continue
            await self.index_channel(channel, c)
        db.commit()

        # index messages in the order we received them now that it's safe to do
        # so. New messages might get added to the queue while we're in the
        # middle of processing these, so it's important to continuously poll the
        # queue.
        while not self.indexing_queue.empty():
            message = await self.indexing_queue.get()
            snitch_channel = db.get_snitch_channel(message.channel.id)

            # consider the following:
            # * bot starts and on_ready is called. current snitch channels:
            #   [A B C].
            # * self.defer_indexing = True
            # * snitch channels A B are indexed, but while they're indexed a
            #   new message M is sent to C. Since we're deferring indexing, M
            #   is added to indexing_queue.
            # * snitch channel C is indexed, which also indexes M.
            # * all snitch channels are indxed now and we move on to indexing
            #   messages in indexing_queue. M has already been indexed, but
            #   it's still in the indexing_queue. It gets indexed for the second
            #   time and causes a pk violation.
            #
            # There are several ways to solve this, but the simplest is to
            # not index any messages earlier than our last_indexed_id.
            # A more robust solution may be to remove messages from
            # indexing_queue when they get indexed by index_channel.
            if snitch_channel and message.id <= snitch_channel.last_indexed_id:
                continue

            await self.maybe_index_message(message)

        # now that we've indexed the channels and fully processed the queue, we
        # can go back to indexing new messages normally.
        self.defer_indexing = False

        # on_ready can be called multiple times by discordpy, not just when
        # the bot starts. Avoid an error by starting an already-started task.
        if not self.check_outdated_livemaps.is_running(): # pylint: disable=no-member
            self.check_outdated_livemaps.start() # pylint: disable=no-member

    async def on_message(self, message):
        await super().on_message(message)
        if not self.defer_indexing:
            await self.maybe_index_message(message)
        else:
            self.indexing_queue.put_nowait(message)

    async def maybe_index_message(self, message):
        snitch_channel = db.get_snitch_channel(message.channel.id)
        # only index messages in snitch channels which have been fully indexed
        # by `.index` already. If someone adds a snitch channel with
        # `.add-channel #snitches`, and then a snitch ping is immediately sent
        # in that channel, we don't want to update the last indexed id (or
        # index the message at all) until the channel has been fully indexed
        # manually.
        if not snitch_channel or not snitch_channel.last_indexed_id:
            return

        # this will retrieve kira configs on every snitch message...we probably
        # want to cache these? but eh, the db hits are so cheap for now and I
        # don't want to deal with cache invalidation.
        kira_configs = db.get_kira_configs(message.guild.id)
        try:
            event = self.parse_event(message.content, kira_configs)
        except InvalidEventException:
            return

        db.add_event(message, event)
        db.update_last_indexed(message.channel.id, message.id)

        # update all the livemaps of the guild
        lm_channel = db.get_livemap_channel(message.guild.id)
        if not lm_channel:
            return
        await self.update_livemap_channel(lm_channel)

    @loop(seconds=10)
    async def check_outdated_livemaps(self):
        try:
            now = utcnow()
            for channel_id, refresh_at in self.livemaps_refresh_at.copy().items():
                # if any of the datetimes in `refresh_at` have passed - no matter
                # how many - we'll refresh the livemap. Afterwards, we'll remove
                # them from the list so we don't refresh on them again.
                future_dts = [dt for dt in refresh_at if now < dt]
                if future_dts == refresh_at:
                    continue

                self.livemaps_refresh_at[channel_id] = future_dts
                lm_channel = db.get_livemap_channel_from_channel(channel_id)
                if not lm_channel:
                    # I think this can happen if the livemap channel is deleted /
                    # changed while livemaps_refresh_at still has entries for that
                    # channel.
                    continue
                await self.update_livemap_channel(lm_channel, refresh=False)
        except Exception as e:
            err = "".join(traceback.format_exception(e))
            await self.error_log_channel.send(f"Ignoring exception in event "
                f"loop `check_outdated_livemaps`: \n```\n{err}\n```")

    async def update_livemap_channel(self, lm_channel, refresh=True):
        channel_id = lm_channel.channel_id

        # avoid infinite refresh chains
        if refresh:
            refresh_at = []
            # generate a new livemap every minute seconds for the next 10
            # minutes, so we get the nice fade effect even if there aren't any
            # new events. Also generate a new livemap 12 seconds from now to
            # clean up any missed events from debounce.
            refresh_at.append(utcnow() + timedelta(seconds=12))
            for i in range(1, 10 + 1):
                dt = utcnow() + timedelta(seconds=i * 60)
                refresh_at.append(dt)

            self.livemaps_refresh_at[channel_id] = refresh_at

        if channel_id in self.livemap_last_uploaded:
            last_uploaded = self.livemap_last_uploaded[channel_id]
            # debounce of 10 seconds so people don't get annoyed at the image
            # they're looking at getting deleted every 2 seconds
            if last_uploaded > utcnow() - timedelta(seconds=10):
                return

        await self.update_livemap(lm_channel)
        self.livemap_last_uploaded[channel_id] = utcnow()


    async def update_livemap(self, lm_channel):
        # we're currently updating the livemap for this channel already, don't
        # duplicate events
        if lm_channel.channel_id in self.livemap_updating_channels:
            return
        self.livemap_updating_channels.append(lm_channel.channel_id)

        try:
            await self._update_livemap(lm_channel)
        finally:
            # make sure we don't block out a guild from livemap updates on
            # errors. Errors can happen even when it's not our fault. For
            # instance, here's an error I got when trying to upload a file:
            #
            # ```
            # discord.errors.DiscordServerError: 503 Service Unavailable (error
            # code: 0): upstream connect error or disconnect/reset before
            # headers. reset reason: connection termination
            # ```
            #
            # since livemaps run so often, it's important they're robust to
            # rare discord errors.
            self.livemap_updating_channels.remove(lm_channel.channel_id)

    async def _update_livemap(self, lm_channel):
        channel = self.get_channel(lm_channel.channel_id)
        if not channel:
            # livemap channel was deleted or we were kicked from the guild,
            # ignore
            return

        guild = channel.guild
        # for now we'll just render all events to the livemap, eventually we may
        # want to support different livemap channels with granular role-based
        # snitch vision
        start = (utcnow() - timedelta(minutes=10)).timestamp()
        events = db.get_events(guild.id, "all", start=start)

        # use all events to construct snitches instead of the filtered subset
        # above
        all_events = db.get_events(guild.id, "all", convert_t=False)
        snitches = snitches_from_events(all_events)
        # reclaim memory.
        del all_events
        snitches |= set(db.get_snitches(guild.id, "all"))
        users = create_users(events)

        with TemporaryDirectory() as d:
            output_file = str(Path(d) / "livemap.jpg")

            config = Config(snitches=snitches, events=events, users=users)
            f = partial(run_image_render, output_file, config)

            with ProcessPoolExecutor() as pool:
                await self.loop.run_in_executor(pool, f)

            livemap_file = File(output_file)
            await channel.send(file=livemap_file)

            # upload a log to our log category if we have one
            log_channel = db.get_livemap_log_channel(guild.id)
            if log_channel:
                log_channel = self.get_channel(log_channel.log_channel_id)
                log_file = File(output_file)
                await log_channel.send(file=log_file)

    async def index_channel(self, channel, discord_channel, *, update_message=None):
        print(f"Indexing channel {discord_channel} / {discord_channel.id}, "
            f"guild {discord_channel.guild} / {discord_channel.guild.id}")
        events = []
        last_id = channel.last_indexed_id
        kira_configs = db.get_kira_configs(discord_channel.guild.id)

        async for message_ in discord_channel.history(limit=None):
            # don't index past the last indexed message id (if we have such
            # an id stored)
            if last_id and message_.id <= last_id:
                break

            try:
                event = self.parse_event(message_.content, kira_configs)
            except InvalidEventException:
                continue
            events.append([message_, event])

            if len(events) % 1_000 == 0:
                content = f"Indexing {discord_channel.mention}... added {len(events):,} new events so far"
                if update_message:
                    await update_message.edit(content=content)

        last_messages = [m async for m in discord_channel.history(limit=1)]

        # only update if the channel has messages
        if last_messages:
            last_message = last_messages[0]
            db.update_last_indexed(channel.id, last_message.id, commit=False)

        for (message_, event) in events:
            # caller is responsible for committing
            db.add_event(message_, event, commit=False)

        return events

    def parse_event(self, raw_event, kira_configs):
        # we'll try all the available configs in order. If none of them match
        # the event, we'll raise an InvalidEventException.
        # Always try the default kira config first.
        for kira_config in [self.default_kira_config] + kira_configs:
            snitch = kira_config.snitch_format
            enter = kira_config.snitch_enter_message
            login = kira_config.snitch_login_message
            logout = kira_config.snitch_logout_message
            time = kira_config.time_format

            try:
                event = Event.parse(raw_event, snitch, enter, login, logout,
                    time)
            except InvalidEventException:
                # this config didn't work, try the next one
                continue

            return event

        raise InvalidEventException("all kira configs failed")

    async def export_to_sql(self, path, snitches, events):
        conn = sqlite3.connect(str(path))
        c = conn.cursor()

        c.execute(
            """
            CREATE TABLE event (
                `username` TEXT NOT NULL,
                `snitch_name` TEXT,
                `namelayer_group` TEXT NOT NULL,
                `x` INTEGER NOT NULL,
                `y` INTEGER NOT NULL,
                `z` INTEGER NOT NULL,
                `t` INTEGER NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE snitch (
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
        conn.commit()

        for snitch in snitches:
            args = [
                snitch.world, snitch.x, snitch.y, snitch.z,
                snitch.group_name, snitch.type, snitch.name,
                snitch.dormant_ts, snitch.cull_ts, snitch.first_seen_ts,
                snitch.last_seen_ts, snitch.created_ts,
                snitch.created_by_uuid, snitch.renamed_ts,
                snitch.renamed_by_uuid, snitch.lost_jalist_access_ts,
                snitch.broken_ts, snitch.gone_ts, snitch.tags, snitch.notes
            ]
            # ignore duplicate snitches
            c.execute("INSERT OR IGNORE INTO snitch VALUES (?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", args)

        for event in events:
            args = [
                event.username, event.snitch_name, event.namelayer_group,
                event.x, event.y, event.z, event.t.timestamp()
            ]
            c.execute("INSERT INTO event VALUES (?, ?, ?, ?, ?, ?, ?)", args)
        conn.commit()


    @command("tutorial",
        help="Walks you through an initial setup of snitchvis."
    )
    async def tutorial(self, message):
        await message.channel.send("To set up snitchvis, you'll need to do two "
            "things:\n* add snitch channels, so snitchvis knows where to look "
            "for snitch events (pings/logins/logouts)"
            "\n* index snitch channels so snitchvis actually has the events "
            "stored. This is a separate command because it can take a long "
            "time to retrieve all the snitch messages from discord due to "
            "ratelimiting.")
        await message.channel.send("To add a snitch channel, do something like "
            "`.add-channel #snitches citizens governor` (see also `.add-channel "
            "--help`). The arguments after the channel should be names of "
            "discord roles which you want to be able to render events from "
            "this channel. If you mess up the roles when adding a "
            "snitch channel, you can use `.remove-channel` to remove it, then "
            "re-add it with the correct roles.")
        await message.channel.send("Once you've added all your snitch "
            "channels, tell snitchvis to store events in those channels with "
            "`.index`. Indexing is only necessary whenever you add a new "
            "snitch channel - snitchvis automatically tracks new messages for "
            "existing channels.")
        await message.channel.send("Once `.index` finishes, you can render "
            "videos with `.render` (or `.r` for short). "
            "When run with no arguments, it looks for the most recent event, "
            "then renders the past 30 minutes of previous events. This is "
            "meant to be a quick way to take a look at your most recent snitch "
            "hits.")
        await message.channel.send("`.r` has a large number of options, and "
            "you "
            "should take some time to read through `.r --help` and try them "
            "out. To give the very short version, you can: "
            "\n* filter by time (`-p/--past 1d12h`, `--start 07/01/22`, `--end "
            "08/30/22`)"
            "\n* filter by users (`-u/--users gregy165`) or nl groups "
            "(`-g/--groups boundary-snitches`)"
            "\n* change duration (`-d/--duration 20`), fps (`--fps 30`), or "
            "quality (`-s/--size 1000`)"
            "\n* change rendering mode (`-m/--mode heatmap`)"
            "\nAnd more. All of this is documented in `.r --help`.")


    @command("add-channel",
        args=[
            Arg("channel", convert=channel, help="The "
                "channel to add. Use a proper channel mention "
                "(eg #snitches) to specify a channel."),
            Arg("roles", nargs="+", convert=role, help="The roles "
                "which will be able to render events from this channel. Use "
                "the name of the role (don't ping the role). Use the name "
                "`everyone` to grant all users access to render the snitches. "
                "Surround role in quotes to specify roles with spaces in them.")
        ],
        help_short="Adds a snitch channel, viewable by the specified roles.",
        help="Adds a snitch channel, viewable by the specified roles.\n\n"
            "Example: `.add-channel #snitches citizen \"lieutenant governor\" "
            "governor`",
        permissions=["manage_guild"],
        aliases=["channel add", "channel-add", "add channel"]
    )
    async def channel_add(self, message, channel, roles):
        if db.snitch_channel_exists(channel.id):
            await message.channel.send(f"{channel.mention} is already a "
                "snitch channel. If you would like to change which roles "
                f"have access to {channel.mention}, first remove it "
                "(`.remove-channel`) then re-add it (`.add-channel`) with "
                "the desired roles.")
            return
        db.add_snitch_channel(channel, roles)

        await message.channel.send(f"Added {channel.mention} to snitch "
            f"channels, accessible by {utils.role_str(roles)}")


    @command("remove-channel",
        args=[
            Arg("channels", nargs="+", convert=channel, help="The "
                "channels to remove. Use a proper channel mention "
                "(eg #snitches) to specify a channel.")
        ],
        help="Removes a snitch channel.",
        permissions=["manage_guild"],
        aliases=["channel remove", "channel-remove", "remove channel"]
    )
    async def channel_remove(self, message, channels):
        for channel in channels:
            db.remove_snitch_channel(channel.id)

        await message.channel.send(f"Removed {utils.channel_str(channels)} "
            "from snitch channels.")


    @command("list-channels",
        help="Lists the current snitch channels.",
        permissions=["manage_guild"],
        aliases=[
            "channel list", "channel-list", "channels list", "list-channel",
            "list channels"
        ]
    )
    async def channel_list(self, message):
        channels = db.get_snitch_channels(message.guild.id)
        if not channels:
            await message.channel.send("No snitch channels set. You can add "
                "snitch channels with `.add-channel`.")
            return

        m = "Current snitch channels:\n"
        for channel in channels:
            m += f"\n{utils.channel_accessible(message.guild, channel)}"
        await message.channel.send(m)


    @command("index",
        help="Indexes messages in the current snitch channels.",
        permissions=["manage_guild"]
    )
    async def index(self, message):
        channels = db.get_snitch_channels(message.guild.id)

        if not channels:
            await message.channel.send("No snitch channels to index. Use "
                "`.add-channel #channel` to add snitch channels.")
            return

        # running multiple concurrent index commands is NOT safe and will result
        # in either silently setting last_indexed_message_id to the wrong value,
        # or a unique constraint failure on insertion of anything but the final
        # command.
        if message.guild.id in self.indexing_guilds:
            await message.channel.send("Indexing is already in progress for "
                "this server. Please be patient - due to discord api "
                "limitations, this process could take hours, depending how "
                "many snitch hits you have stored.")
            return

        await message.channel.send("Indexing the following snitch channels: "
            f"{utils.channel_str(channels)}. This could take a LONG time "
            "(hours) if you have lots of snitch hits stored.")

        for channel in channels:
            # make sure we can read all the snitch channels
            c = channel.to_discord(message.guild)
            permissions = c.permissions_for(message.guild.me)
            if not permissions.read_messages:
                await message.channel.send("Snitchvis doesn't have permission "
                    f"to read messages in {channel.mention}. Either give "
                    "snitchvis enough permissions to read messages there, or "
                    "remove it from the list of snitch channels (with "
                    "`.remove-channel`).")
                return

        self.indexing_guilds.append(message.guild.id)
        try:
            for channel in channels:
                update_message = await message.channel.send(f"Indexing {channel.mention}...")
                c = channel.to_discord(message.guild)
                events = await self.index_channel(channel, c, update_message=update_message)
                db.commit()

                await update_message.edit(content=f"Finished indexing {channel.mention} "
                    f"({len(events):,} new events added)")

            await message.channel.send("Finished indexing snitch channels")
        finally:
            # ensure that indexing_guilds doesn't get stuck in an inconsistent
            # state. Despite my best efforts, it is possible for pk violation
            # errors to occur when indexing here. We probably need to defer
            # indexing for a specific guild while an .index command is running
            # in that guild.
            self.indexing_guilds.remove(message.guild.id)

    @command("full-reindex",
        args=[
            Arg("-y", store_boolean=True, help="Pass to confirm you would like "
            "to reindex the server.")
        ],
        help="Drops all currently indexed snitches and re-indexes from "
            "scratch. This can help with some rare issues. You probably don't "
            "want to do this unless you know what you're doing, or have been "
            "advised to do so by tybug.",
        help_short="Drops all currently indexed snitches and re-indexes from "
            "scratch.",
        permissions=["manage_guild"]
    )
    async def full_reindex(self, message, y):
        if not y:
            await message.channel.send("This command will delete all currently "
                "indexed snitches and will re-index from scratch. This can "
                "help with some rare issues. You probably don't want to do "
                "this unless you know what you're doing, or have been advised "
                "to do so by tybug. If you're sure you would like to reindex, "
                "run `.full-reindex -y`.")
            return
        await message.channel.send("Dropping all events and resetting last "
            "indexed ids")
        # drop all events
        db.execute("DELETE FROM event WHERE guild_id = ?", [message.guild.id])
        # reset last indexed id so indexing works from scratch again
        db.execute("UPDATE snitch_channel SET last_indexed_id = null "
            "WHERE guild_id = ?", [message.guild.id])
        # finally, reindex.
        await self.index(message)

    @command("render",
        args=[
            Arg("-p", "--past", convert=human_timedelta, help="How far in the "
                "past to look for events. Specify in human-readable form, ie "
                "-p 1y2mo5w2d3h5m2s (\"1 year 2 months 5 weeks 2 days 3 hours 5 "
                "minutes 2 seconds ago\"), or any combination thereof, eg "
                "-p 1h30m (\"1 hour 30 minutes ago\"). Use the special value "
                "\"all\" to render all events."),
            Arg("--start", convert=human_datetime, help="The start date of "
                "events to include. Use the format `mm/dd/yyyy` or `mm/dd/yy`, "
                "eg 7/18/2022 or 12/31/21. If --start is passed but not "
                "--end, *all* events after the passed start date will be "
                "rendered.", convert_mode="together", nargs="*"),
            Arg("--end", convert=human_datetime, help="The end date of "
                "events to include. Use the format `mm/dd/yyyy` or `mm/dd/yy`, "
                "eg 7/18/2022 or 12/31/21. If --end is passed but not "
                "--start, *all* events before the passed end date will be "
                "rendered.", convert_mode="together", nargs="*"),
            Arg("-s", "--size", default=700, convert=int, help="The resolution "
                "of the render, in pixels. Defaults to 700. Higher values take "
                "longer to render."),
            Arg("--fps", default=20, convert=int, help="The frames per "
                "second of the render. Defaults to 20. Higher values take "
                "longer to render."),
            Arg("-d", "--duration", default=5, convert=int, help="The length "
                "of the output video, in seconds. Defaults to 5 seconds. "
                "Higher values take longer to render."),
            Arg("-u", "--users", nargs="*", default=[], help="If passed, only "
                "events by these users will be rendered."),
            Arg("-g", "--groups", nargs="*", default=[], help="If passed, only "
                "events from snitches on these namelayer groups will be "
                "rendered."),
            Arg("-f", "--fade", default=1.5, convert=float, help="How many seconds "
                "events will remain on screen for. Fade is limited to a "
                "minimum of 0.5s. Defaults to 1.5s."),
            Arg("-b", "--bounds", nargs="*", convert=bounds,
                convert_mode="together", help="Sets what area of the world "
                "will be visualized. "
                "This will override the automatic detection, which tries to "
                "include all events without making the area too large. Format "
                "is "
                "-b/--bounds x1 z1 x2 z2, where (x1, z1) is the bottom left "
                "corner and (x2, z2) is the top right corner of the desired "
                "area."),
            Arg("-a", "--all-snitches", default=False, store_boolean=True,
                help="If passed, all known snitches will be rendered, not "
                "just the snitches pinged by the relevant events."),
            Arg("-op", "--only-pinged", help="Only render snitches which were "
                "pinged at least once in the render.", store_boolean=True,
                default=False),
            Arg("-m", "--mode", choices=["line", "box", "heatmap"],
                default="box", help="One of heatmap, line, or box. What mode "
                "to render in. The heatmap mode "
                "renders an aggregate heatmap of events instead of drawing "
                "individual users. The line mode draws "
                "lines between snitch events. This option is "
                "experimental and may not look good. Defaults to box."),
            Arg("-o", "--opacity", help="The opacity of the background "
                "terrain map, "
                "between 0 and 1. Higher is more visible. Defaults to 0.15.",
                convert=float, default=0.15),
            Arg("--anonymize", help="Randomizes snitch locations within a "
                "certain number of blocks. Defaults to 10 blocks. Pass "
                "`--anonymize n` to change anonymization radius, eg "
                "`--anonymize 20`.",
                convert=int, const=10, nargs="?"),
            Arg("-hp", "--heatmap-percentage", convert=float, default=20,
                help="What percentage of the "
                "video duration the heatmap should look backwards for events "
                "for. For instance, with `-hp 30` the render will only "
                "consider events in the most recent 30% of the video when "
                "rendering the heatmap. With `-hp 100`, the heatmap will be "
                "static for the entire video (because it always considers all "
                "of the events). Defaults to 20."),
            Arg("-hs", "--heatmap-scale", choices=["linear", "weighted"],
                default="linear", help="What scale "
                "to use for the heatmap colors. One of \"linear\" or "
                "\"weighted\". Defaults to linear. In linear mode, heatmap "
                "brightness scale linearly with the number of hits. In "
                "weighted mode, snitches with a low number of hits are made "
                "more visible. This can help if you have a few very high "
                "frequency snitches."),
            # TODO work on svis file format
            Arg("--export", choices=["sql", "svis"],
                help="Export the events matching the specified "
                "criteria to either an sql database, or an .svis file (for use "
                "in the Snitch Vis desktop application). Pass `--export sql` "
                "for the former and `--export svis` for the latter.")
        ],
        help="Renders snitch events to a video.",
        aliases=["r"]
    )
    async def render(self, message, past, start, end, size, fps, duration, users,
        groups, fade, bounds, all_snitches, only_pinged, mode, opacity,
        anonymize, heatmap_percentage, heatmap_scale, export
    ):
        NO_EVENTS = ("No events match those criteria. Try adding snitch "
            "channels with `.add-channel #channel`, indexing with `.index`, or "
            "adjusting your parameters to include more snitch events.")

        if heatmap_percentage < 1:
            await message.channel.send("Cannot use a heatmap percentage lower "
                "than 1%, as it can be very expensive to calculate the "
                "maximum hits for small heatmap time chunks. Please choose a "
                "value greater than or equal to 1.")
            return

        if self.concurrent_renders[message.guild.id] >= self.MAXIMUM_CONCURRENT_RENDERS:
            await message.channel.send("You are already running "
                f"{self.MAXIMUM_CONCURRENT_RENDERS} renders at the same time; "
                "please wait for one to finish before starting a new one.")
            return

        if (past is not None) and (start != [] or end != []):
            await message.channel.send("`-p/--past` is incomptaible with "
                "`--start` and `--end`. You cannot pass both.")
            return

        if past:
            end = utcnow().timestamp()
            if past == "all":
                # conveniently, start of epoch is 0 ms
                start = 0
            else:
                start = end - past.total_seconds()
        else:
            if not start and not end:
                # neither set
                # slightly special behavior: instead of going back in the past
                # `x` ms, go back to the most recent event (however long ago
                # that may be) and *then* go back `x` ms and grab all those
                # events.
                event = db.most_recent_event(message.guild.id)
                # if the guild doesn't have any events at all yet, complain and
                # exit.
                if not event:
                    await message.channel.send(NO_EVENTS)
                    return
                end = event.t.timestamp()
                # TODO make adjustable instead of hardcoding 30 minutes, not
                # sure what parameter name to use though (--past-adjusted?)
                start = end - (30 * 60)
            elif start and not end:
                # only start set. Set end to current date
                start = start.timestamp()
                end = utcnow().timestamp()
            elif end and not start:
                # only end set. Set start to beginning of time
                start = 0
                end = end.timestamp()
            else:
                # both set
                start = start.timestamp()
                end = end.timestamp()

        if end < start:
            await message.channel.send("End date can't be before start date.")
            return

        # TODO warn if no events by the specified users are in the events filter
        events = db.get_events(message.guild.id, message.author.roles,
            start=start, end=end, users=users, groups=groups)

        if not events:
            await message.channel.send(NO_EVENTS)
            return

        # use all events this author has access to to construct snitches,
        # instead of just the events returned by the filter
        all_events = db.get_events(message.guild.id, message.author.roles,
            convert_t=False)
        snitches = snitches_from_events(all_events)
        # reclaim some memory since we don't need all_events anymore.
        # TODO we may want to load events one by one
        # instead of holding them all in memory at once.
        del all_events
        # if the guild has any snitches uploaded (via .import-snitches), use
        # those as well, even if they've never been pinged.
        # Only retrieve snitches which the author has access to via their roles
        snitches |= set(db.get_snitches(message.guild.id, message.author.roles))
        users = create_users(events)

        if anonymize is not None:
            # associate each unique (x, y) with a specific randomzied x offset
            # and y offset. We want every event (and snitch) at the same
            # location to be anonymized in the same way so that they all line
            # up. We want the x and y offset to be uncoupled so we get full
            # randomness in the (-anonymize, anonymize) square.
            offsets = defaultdict(
                lambda: (
                    random.randint(-anonymize, anonymize),
                    random.randint(-anonymize, anonymize)
                )
            )

            def _anonymize(obj):
                (offset_x, offset_y) = offsets[(obj.x, obj.y)]
                obj.x += offset_x
                obj.y += offset_y

            # anonymize both events and snitches - we can't just anonymize
            # events because snitches can come straight from the database as
            # well and not be created from existing events. We'll just modify
            # after all creation has taken place.
            for event in events:
                _anonymize(event)
            for snitch in snitches:
                _anonymize(snitch)

        if only_pinged:
            # filter snitches to only those with at least one associated
            # event.
            event_locs = set((e.x, e.y, e.z) for e in events)
            snitches = [s for s in snitches if (s.x, s.y, s.z) in event_locs]

        if export == "sql":
            await message.channel.send("Exporting specified events to a "
                "database...")
            with TemporaryDirectory() as d:
                d = Path(d)
                p = d / "snitchvis_export.sqlite"
                zipped_p = d / "snitchvis_export.sqlite.gz"
                await self.export_to_sql(p, snitches, events)

                # compress with gzip
                with open(p, "rb") as f_in:
                    with gzip.open(zipped_p, "wb") as f_out:
                        f_out.writelines(f_in)

                sql_file = File(zipped_p)
                # 8mb in bytes
                if zipped_p.stat().st_size >= 8_000_000:
                    await message.channel.send("The sql file is over 8mb in "
                        "size and can't be uploaded, sorry! Please contact "
                        "tybug and ask for a manual export.")
                else:
                    await message.channel.send(file=sql_file)
            return
        if export == "svis":
            await message.channel.send("Exporting to a .svis file is not "
                "implemented yet.")
            return

        multiplier = db.get_guild_multiplier(message.guild.id)
        num_pixels = duration * fps * (size * size)
        if num_pixels > self.PIXEL_LIMIT_VIDEO * multiplier:
            await message.channel.send("The requested render would require too "
                "many server resources to generate (and would probably be over "
                "discord's 8mb file size limit). Decrease either the render "
                "size (`-s/--size`), fps (`--fps`), or duration "
                "(`-d/--duration`)."
                "\n\nI'm happy to increase render limits for servers that "
                "promise not to abuse it. You can request increased render "
                "limits by contacting tybug.")
            return

        start = (utcnow() - timedelta(days=1)).timestamp()
        end = utcnow().timestamp()
        usage = db.get_pixel_usage(message.guild.id, start, end)
        if usage > self.PIXEL_LIMIT_DAY * multiplier:
            await message.channel.send("You've rendered more than 500 billion "
                "pixels in the past 24 hours. I have limited server resources "
                "and cannot allow servers to render more than this (already "
                "extremely high) limit per day. You will have to wait up to "
                "24 hours for your usage to decrease before being able to "
                "render again.")
            return

        with TemporaryDirectory() as d:
            output_file = Path(d) / "render.mp4"

            m = await message.channel.send("rendering video...")

            # seconds to ms
            duration *= 1000
            fade *= 1000

            # if we run this in the default executor (ThreadPoolExecutor), we
            # get a pretty bad memory leak. We spike to ~700mb on a default
            # settings visualization (5 seconds / 20 fps / 700 pixels), which is
            # normal enough, but then
            # instead of returning to the baseline 70mb, we return to 350mb or
            # so after rendering. It's not a true memory leak though because
            # subsequent renders don't always increase memory: if you continue
            # to render at default settings, it'll return to 350mb pretty much
            # every time. If you then render something larger (-s 1000 or so),
            # it'll spike to 1200mb (again, normal) but then return to 500mb or
            # so instead of 350mb. It's like it sticks to a high water mark or
            # something. But it's not just that because memory usage does also
            # go up non insignificant amounts at random intervals when you
            # render.
            # I'm not sure what's leaking - the obvious culprits are the ffmpeg
            # pipe, the images, qbuffers, or the world pixmap. But all of those
            # should be getting cleaned up when `SnitchVisRecord` gets gc'd, so
            # I dunno.
            # This memory leak is something I definitely should look into and
            # fix at some point, but I don't want to right now, so the temporary
            # fix is sticking the visualization into a separate process and
            # letting 100% of its memory get returned to the OS when it exits,
            # since its only job is writing to an output mp4.
            # We are taking a slight hit on the event pickling, but hopefully
            # it's not too bad.
            config_ = Config(snitches=snitches, events=events, users=users,
                show_all_snitches=all_snitches, mode=mode,
                heatmap_percentage=heatmap_percentage,
                heatmap_scale=heatmap_scale, bounds=bounds,
                world_map_opacity=opacity)
            f = partial(run_snitch_vis, duration, size, fps, fade,
                str(output_file), config_)

            self.concurrent_renders[message.guild.id] += 1
            with ProcessPoolExecutor() as pool:
                await self.loop.run_in_executor(pool, f)
            self.concurrent_renders[message.guild.id] -= 1

            vis_file = File(output_file)
            if output_file.stat().st_size >= 8_000_000:
                await message.channel.send("The resulting render was over 8mb "
                    "in size and couldn't be uploaded. To decrease file sizes, "
                    "you should use lower values for `-s/--size`, `--fps`, "
                    "and/or `-d/--duration`.")
                await m.delete()
            else:
                await message.channel.send(file=vis_file)
                await m.delete()

                # don't log tests by myself
                if message.author.id != config.AUTHOR_ID and self.command_log_channel:
                    vis_file = File(output_file)
                    await self.command_log_channel.send(file=vis_file)

        db.add_render_history(message.guild.id, num_pixels,
            utcnow().timestamp())

    @command("import-snitches",
        args=[
            Arg("-g", "--groups", nargs="+", help="Only snitches in the "
                "database which are reinforced to one of these groups will be "
                "imported. If you really want to import all snitches in the "
                "database, pass `-g all`."),
            Arg("-r", "--roles", nargs="+", convert=role, help="Users with at "
                "least one of these roles will be able to render the "
                "imported snitches. Use the name of the role (don't ping the "
                "role). Use the name `everyone` to grant all users access to "
                "the snitches. Surround role in quotes to specify roles "
                "with spaces in them.")
        ],
        help="Imports snitches from a SnitchMod database.\n"
            "You will likely have to use this command multiple times on the "
            "same database if you have a tiered hierarchy of snitch groups; "
            "for instance, you might run `.import-snitches -g mta-citizens "
            "mta-shops -r citizen` to import snitches citizens can render, "
            "and then `.import-snitches -g mta-cabinet -r cabinet` to import "
            "snitches only cabinet members can render.",
        help_short="Imports snitches from a SnitchMod database.",
        permissions=["manage_guild"]
    )
    async def import_snitches(self, message, groups, roles):
        attachments = message.attachments
        if not attachments:
            await message.channel.send("You must upload a `snitch.sqlite` file "
                "in the same message as the `.import-snitches` command.")
            return

        with NamedTemporaryFile() as f:
            attachment = attachments[0]
            await attachment.save(f.name)
            conn = sqlite3.connect(f.name)
            cur = conn.cursor()

            for group in groups:
                if group == "all":
                    continue
                # case insensitive group compare. nl groups are case insensitive
                # in game.
                row = cur.execute("SELECT COUNT(*) FROM snitches_v2 WHERE "
                    "group_name = ? COLLATE NOCASE", [group]).fetchone()
                if row[0] == 0:
                    await message.channel.send("No snitches on namelayer "
                        f"group `{group}` found in this database. If the "
                        "group name is correct, omit it and re-run to "
                        "avoid this error.")
                    await message.channel.send("Import aborted. You may "
                        "safely re-run this import with different "
                        "arguments.")
                    return

            await message.channel.send("Importing snitches from snitchmod "
                "database...")

            snitches_added = 0
            if any(group == "all" for group in groups):
                group_filter = "1"
                # don't pass ["all"] if our filter doesn't have any params
                groups_params = []
            else:
                # match case insensitive compare above.
                group_filter = f"group_name COLLATE NOCASE IN ({('?, ' * len(groups))[:-2]})"
                groups_params = groups

            rows = cur.execute("SELECT * FROM snitches_v2 WHERE "
                f"{group_filter}", groups_params).fetchall()

            for row in rows:
                snitch = Snitch.from_snitchmod(row)
                # batch commit for speed
                rowcount = db.add_snitch(message.guild.id, snitch, roles,
                    commit=False)
                snitches_added += rowcount

            db.commit()

        await message.channel.send(f"Added {snitches_added} new snitches.")

    @command("permissions",
        help="Lists what snitch channels you have "
        "have permission to render events from. This is based on your discord "
        "roles and how you set up the snitch channels (see `.list-channels`).",
        help_short="Lists what snitch channels you have permission to render "
            "events from."
    )
    async def permissions(self, message):
        # tells the command author what snitch channels they can view.
        snitch_channels = db.get_snitch_channels(message.guild.id)

        channels = set()
        for role in message.author.roles:
            for channel in snitch_channels:
                if role.id in channel.allowed_roles:
                    channels.add(channel)

        if not channels:
            await message.channel.send("You can't render any events.")
            return

        await message.channel.send("You can render events from the "
            f"following channels: {utils.channel_str(channels)}")

    @command("events", help="Lists the most recent events for the specified "
        "snitch or snitches.",
        args=[
            Arg("-n", "--name", help="List events for snitches with the "
                "specified name."),
            Arg("-l", "--location", help="List events for snitches at this "
                "location. Format is `-l/--location x y z` or "
                "`-l/--location x z`. The two parameter version is a "
                "convenience to avoid having to specify a y level; snitches at "
                "all y levels at that (x, z) location will be searched for "
                "events.", nargs="*")
        ],
        # TODO temporary until fix permissions
        permissions=["manage_guild"]
    )
    async def events(self, message, name, location):
        # explicitly allow empty name, useful for searching for unnamed snitches
        if not (bool(name) or name == "") ^ bool(location):
            await message.channel.send("Exactly one of `-n/--name` or "
                "`-l/--location` must be passed.\nRun `.events --help` for "
                "more information.")
            return

        if name is not None:
            events = db.select("""
                SELECT * FROM event
                WHERE guild_id = ? AND snitch_name = ?
                ORDER BY t DESC LIMIT 10
            """, [message.guild.id, name])
        elif location:
            if len(location) == 2:
                x, z = location
                y = None
            elif len(location) == 3:
                x, y, z = location
            else:
                await message.channel.send(f"Invalid location "
                    f"`{' '.join(location)}`. Must be in the form "
                    "`-l/--location x y z` or `-l/--location x z`")
                return

            try:
                x = int(x)
            except ValueError:
                await message.channel.send(f"Invalid x coordinate `{x}`")
                return

            try:
                y = int(y) if y else None
            except ValueError:
                await message.channel.send(f"Invalid y cooridnate `{y}`")
                return

            try:
                z = int(z)
            except ValueError:
                await message.channel.send(f"Invalid z coordinate `{z}`")
                return

            # swap y and z because that's what the db expects
            if y is not None:
                events = db.select("""
                    SELECT * FROM event
                    WHERE guild_id = ? AND x = ? AND y = ? AND z = ?
                    ORDER BY t DESC LIMIT 10
                """, [message.guild.id, x, z, y])
            else:
                events = db.select("""
                    SELECT * FROM event
                    WHERE guild_id = ? AND x = ? AND y = ?
                    ORDER BY t DESC LIMIT 10
                """, [message.guild.id, x, z])

        if not events:
            await message.channel.send("No events match those criteria.")
            return

        messages = []
        for event in events:
            t = datetime.fromtimestamp(event["t"]).strftime('%Y-%m-%d %H:%M:%S')
            group = event["namelayer_group"]
            username = event["username"]
            snitch_name = event["snitch_name"]
            x = event["x"]
            y = event["y"]
            z = event["z"]

            messages.append(f"`[{t}]` `[{group}]` **{username}** is at "
                f"{snitch_name} ({x},{z},{y})")
        await message.channel.send("10 most recent events matching those "
            "criteria (most recent first):\n" + "\n".join(messages))

    @command("help", help="Displays available commands.")
    async def help(self, message):
        text_by_command = {}
        for command in self.commands:
            # don't show aliases in help (yet, we probably want a separate
            # section or different display method for them)
            if command.alias:
                continue
            # don't show author-only commands in help message, unless I'm the
            # one running .help
            if "author" in command.permissions and message.author.id != config.AUTHOR_ID:
                continue
            # TODO display custom prefixes if set
            prefix = self.default_prefix if command.use_prefix else ""
            text_by_command[command.function] = (f"  {prefix}{command.name}: "
                f"{command.help_short}")

        # sort by order in self.help_order, and send to end if not present
        # (we don't care about ordering if we didn't specify it, as long as it's
        # kicked to the end)
        def _key(command):
            if command in self.help_order:
                return self.help_order.index(command)
            return 999

        command_texts = [text_by_command[command] for command in
            sorted(text_by_command, key=_key)]

        await message.channel.send("```\n" + "\n".join(command_texts) + "```\n")

    @command("snitchvissetprefix",
        help="Sets a new prefix for snitchvis. The default prefix is `.`.",
        args=[
            Arg("prefix", help="The new prefix to use. Must be a single "
            "character.")
        ],
        use_prefix=False,
        permissions=["manage_guild"]
    )
    async def set_prefix(self, message, prefix):
        if len(prefix) != 1:
            await message.channel.send("New prefix must be a single character.")
            return

        db.set_guild_prefix(message.guild.id, prefix)
        # update cached prefix immediately, this updates on bot restart normally
        self.prefixes[message.guild.id] = prefix

        await message.channel.send(f"Successfully set prefix to `{prefix}`.")

    @command("set-livemap-channel",
        args=[
            Arg("channel", convert=channel, help="What channel to upload the "
                "livemap to.")
        ],
        help="Sets the channel to upload an always-up-to-date snitch "
            "events image to. A new image is uploaded whenever there are new "
            "snitch events.",
        help_short="Sets the channel to upload an always-up-to-date snitch "
            "events image to.",
        permissions=["manage_guild"]
    )
    async def set_livemap_channel(self, message, channel):
        guild = message.guild
        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages:
            await message.channel.send("I don't have permission to send "
                "messages in that channel. Please adjust permissions and try "
                "again.")
            return

        db.set_livemap_channel(guild.id, channel.id)
        await message.channel.send(f"Set livemap channel to {channel.mention}.")

        lm_channel = db.get_livemap_channel_from_channel(channel.id)
        await self.update_livemap(lm_channel)

        if not self.livemap_log_category:
            return

        log_channel = db.get_livemap_log_channel(guild.id)
        if log_channel:
            print(f"deleting livemap log channel {log_channel} to make way for "
                "a new one")
            log_channel = self.get_channel(log_channel.log_channel_id)
            await log_channel.delete()

        log_channel = await self.livemap_log_category.create_text_channel(
            f"{guild.name}-{guild.id}"
        )
        db.set_livemap_log_channel(guild.id, lm_channel.channel_id,
            log_channel.id)

    @command("create-command",
        args=[
            Arg("command", help="The name to use to run this new command. "
                "Don't include the bot prefix (which is `.` by default)."),
            Arg("command_text", help="This text will be run when you run this "
                "custom command, as if you had run it yourself. "
                "You can reference existing commands and pass arguments as "
                "usual.")
        ],
        help_short="Creates a custom command, which can call other commands and "
            "pass arguments.",
        help="Create a custom command, which can call other commands and "
            "pass arguments.\n\n"
            "For instance, if you wanted to have a render command which "
            "always created a high quality render, you might do "
            "`.create-command rhq "
            "render --size 1200`. Now, whenever you type `.rhq`, it will be "
            "as if you had typed `.render --size 1200`. You can also pass "
            "additional arguments to your custom command like `.rhq --fade 3`, "
            "which will become `.render --size 1200 --fade 3`.\n\n"
            "You can call any existing command and can specify any arguments "
            "you want in your custom command. Custom commands cannot call "
            "other custom commands, only existing base commands.\n\n"
            "Examples:\n"
            "* `.create-command rhq render --size 1200 --fps 30\n`"
            "* `.create-command render render -d 30` - can create custom "
            "commands with the same name as existing commands. This makes all "
            "renders 30 seconds long\n"
            "* `.create-command h help` - I don't know why you would do this, "
            "but you can\n"
            "* `.create-command city render --bounds 1700 650 2000 300` "
            "- shorthand command to render a specific area, now you just have "
            "to type `.city`",
        parse=False,
        permissions=["manage_guild"]
    )
    async def create_command(self, message, args):
        if len(args) == 0:
            await message.channel.send("Missing parameter for `command`.\n"
                "Run `.create-command --help` for more information.")
            return
        if len(args) == 1:
            await message.channel.send("Missing parameter for `command_text`.\n"
                "Run `.create-command --help` for more information.")
            return

        new_command = args[0]
        # manually add prefix so our command_matches command works
        command_text = "." + " ".join(args[1:])

        command_matches = False
        for command in self.commands:
            if self.command_matches(message.guild.id, command, command_text):
                command_matches = True

        if not command_matches:
            await message.channel.send("No existing command found matching "
                f"`{command_text}`. The first part of this argument must be "
                "an existing command.")
            return

        if db.command_exists(message.guild.id, new_command):
            db.update_command(message.guild.id, new_command, command_text)
            await message.channel.send(f"Updated existing command "
            f"`.{new_command}`. It will expand to `{command_text}` when run.")
        else:
            db.add_command(message.guild.id, new_command, command_text)
            await message.channel.send(f"Added new command `.{new_command}`. "
                f"When you run it, it will expand to `{command_text}`.")

    @command("commands",
        help="View all custom commands for this server.",
        permissions=["manage_guild"]
    )
    async def list_commands(self, message):
        commands = db.get_commands(message.guild.id)
        if not commands:
            await message.channel.send("No custom commands yet. Create one "
                "with `.create-command`.")
            return

        text = "Current custom commands:\n"
        for command in commands:
            text += f"\n`.{command.command}` - runs `{command.command_text}`"
        await message.channel.send(text)

    @command("add-kira-config",
        help_short="Adds a kira config to the list of known formats.",
        help="Adds a kira config to the list of known formats. Use this if you "
            "have modified your kira snitch message format from the default. "
            "To use, run "
            "`!kira relayconfig <config_name>` first, then `.add-kira-config`. "
            "Snitchvis will look for a recent config message by kira to parse."
    )
    async def add_kira_config(self, message):
        config_message = None

        # look for a kira config message in the recent past
        async for m in message.channel.history(limit=10):
            if m.author.id != config.KIRA_ID:
                continue

            result = re.search("Relay config \*\*.*?\*\* is owned by .*?",
                m.content)
            if not result:
                continue

            config_message = m
            # only use most recent config message
            break

        if not config_message:
            await message.channel.send("Could not find a recent kira config "
                "message in this channel. Please use "
                "`!kira relayconfig <config_name>` "
                "immediately before running `.add-kira-config`.")
            return

        def search(pattern):
            result = re.search(pattern, config_message.content)
            if not result:
                return None
            return result.group(1)

        name = search("Relay config \*\*(.*)\*\* is owned by")
        snitch_f = search("Format used for snitch alerts \(snitchformat\): "
            "`` (.*) ``")
        enter_f = search("Format used for entering a snitch range "
            "\(snitchentermessage\): `` (.*) ``")
        login_f = search("logins within a snitch range \(snitchloginmessage\): "
            "`` (.*) ``")
        # yes, kira has a typo (should be snitchlogoutmessage)
        logout_f = search("Format used for logouts within a snitch range "
            "\(snitchloginmessage\): `` (.*) ``")
        time_f = search("Time format used for the time stamps of messages "
            "\(timeformat\): `` (.*) ``")

        if any(v is None for v in [name, snitch_f, enter_f, login_f, time_f]):
            await message.channel.send("Could not find all the required "
                "information from the kira config message. Either kira has "
                "changed its message format, or something weird is going "
                f"on. Contact <@{config.AUTHOR_ID}> for help if the "
                "problem persists.")
            return

        if db.kira_config_exists(message.guild.id, name):
            verbed = "updated"
            db.update_kira_config(message.guild.id, name, snitch_f, enter_f,
                login_f, logout_f, time_f)
        else:
            verbed = "added"
            db.add_kira_config(message.guild.id, name, snitch_f, enter_f,
                login_f, logout_f, time_f)

        await message.channel.send(f"Succesfully {verbed} config for "
            f"`{name}`:\n\n"
            f"snitchformat: ``{snitch_f}``\n"
            f"snitchentermessage: ``{enter_f}``\n"
            f"snitchloginmessage: ``{login_f}``\n"
            f"snitchlogoutmessage: ``{logout_f}``\n"
            f"timeformat: ``{time_f}``\n")

    @command("invite",
        help="Sends the invite link for snitchvis."
    )
    async def invite(self, message):
        await message.channel.send(INVITE_LINK)

    @command("set-pixel-multiplier",
        args=[
            Arg("guild_id", convert=int, help="The guild id to set the pixel "
                "limit multipler of"),
            Arg("multiplier", convert=int, help="The pixel limit multiplier "
                "to set the guild to")
        ],
        help="Set the pixel limit multiplier for a guild.",
        permissions=["author"]
    )
    async def set_pixel_multiplier(self, message, guild_id, multiplier):
        db.set_guild_multiplier(guild_id, multiplier)
        await message.channel.send("Updated mutliplier for guild "
            f"`{guild_id}` to `{multiplier}`.")

    @command("as",
        args=[
            Arg("user_id", help="What user to run this command as."),
            Arg("guild_id", help="What guild to run this command in."),
            Arg("command", help="The command to run as the given user in the "
                "given guild.")
        ],
        parse=False,
        help="issue a command as another user/guild.",
        permissions=["author"]
    )
    async def as_(self, message, args):
        user_id = int(args[0])
        guild_id = int(args[1])
        command = " ".join(args[2:])

        guild = self.get_guild(guild_id)
        if not guild:
            await message.channel.send(f"Invalid guild {guild_id}.")
            return

        author = await guild.fetch_member(user_id)
        if not author:
            await message.channel.send(f"Invalid user {user_id}.")
            return

        message = FakeMessage(author, message.channel, guild, command)
        await self.maybe_handle_command(message, message.content,
            override_testing_ignore=True)

# we can only have one qapp active at a time, but we want to be able to
# be rendering multiple snitch logs at the same time (ie multiple .r
# commands, potentially in different servers). We'll keep a master qapp
# active at the top level, but never exec it, which is enough to let us
# draw on qimages and generate videos with SnitchVisRecord and
# FrameRenderer.
# https://stackoverflow.com/q/13215120 for platform/minimal args
qapp = QApplication(['-platform', 'minimal'])

if __name__ == "__main__":
    client = Snitchvis()
    client.run(config.TOKEN)

## next priorities
# * change --randomize to snap to a grid to avoid probabilistic attacks on
#   snitch locations
# * add a `server` column to events / snitches
# * enable (opacity) players when they have any events on screen and disable
#   when they don't, helps people narrow down exactly who is on screen at any
#   given moment
# * handle overlapping events on the same snitch
# * fix permissions on .events, currently returns results for all events,
#   need to limit to just the events the user has access to
# * -c/--context n render option to expand the bounding box by n blocks, for
#   when you want to see more context. MIN_BOUNDING_BOX_SIZE helps with this but
#   isn't a perfect solution
# * tiny pop-in / ease animation for new events? hard to see where new events
#   are on big maps sometimes. could get annoying though
# * make lines mode in visualizer actually worth using - highlight single
#   events, distinguish actual events and the lines, add arrows to indicate
#   directionality
# * add notification on certain users pinging snitches
# * break after a certain distance for line mode? avoids crazy spider web

## nice to have eventually
# * pad the first event like we did the last event? less of a concern but
#   will probably make it look nicer due to seeing the fade in
# * .visits
# * maybe add "centered at (x, y)" coordinates to info text, can be confusing
#   where the vis is sometimes. Might need a different solution to this (coords at
#   corners? gets cluttered though...)
# * need padding for visible snitches, we care about the *snitch field*
#   being visible, not the snitch itself being visible
#   https://discord.com/channels/993250058801774632/993536931189244045/1002667797907775598
# * add regex filtering to .events --name, seems useful (--name-regex?)
