from datetime import datetime
from tempfile import NamedTemporaryFile
import sqlite3

from discord import Client, File
from discord.utils import remove_markdown
from snitchvis import (Event, InvalidEventException, SnitchVisRecord,
    create_users, snitches_from_events, Snitch)

import db
import utils
from utils import human_timedelta, ArgParser
from secret import TOKEN

INVITE_URL = ("https://discord.com/oauth2/authorize?client_id="
    "999808708131426434&permissions=0&scope=bot")

class MyClient(Client):
    async def on_message(self, message):
        if message.content == ".setup":
            await self.setup(message)
        if message.content.startswith(".channel add"):
            await self.channel_add(message)
        if message.content.startswith(".channel remove"):
            await self.channel_remove(message)
        if message.content.startswith(".channel list"):
            await self.channel_list(message)
        if message.content == ".index":
            await self.index(message)
        if message.content.startswith(".v"):
            await self.visualize(message)
        if message.content == ".import-snitches":
            await self.import_snitches(message)

        await self.maybe_index_message(message)

    async def setup(self, message):
        await message.channel.send("Looking for snitch channels...")
        snitch_channels = set()

        for channel in message.guild.text_channels:
            # almost all snitch channels will have every message as a snitch
            # ping, but give us some headroom just in case by searching back 5
            # messages.
            async for message in channel.history(limit=5):
                # remove backticks and bold formatting to avoid confusing our
                # event parser
                content = remove_markdown(message.content)
                try:
                    Event.parse(content)
                except InvalidEventException:
                    continue

                snitch_channels.add(channel)

        if not snitch_channels:
            await message.channel.send("Couldn't find any snitch channels. "
                "Make sure Snitchvis can see the snitch channels you want it "
                "to have access to, and can read the message history of those "
                "channels.")
            await message.channel.send("Try re-running `.setup` after "
                "adjusting snitchvis' permissions. You can also add channels "
                "manually with `.channel add #channel`.")
            return

        for channel in snitch_channels:
            # just ignore duplicate snitch channels if the user runs setup
            # multiple times, won't hurt anything and they can always remove it
            # manually
            if db.snitch_channel_exists(channel):
                continue
            db.add_snitch_channel(channel)

        channel_str = utils.channel_str(snitch_channels)
        await message.channel.send("Identified the following snitch channels: "
            f"{channel_str}. If you expected Snitchvis to find more channels, "
            "make sure it has the \"read message\" and \"read message "
            "history\" permissions for those channels.")
        await message.channel.send("You can add or remove snitch channels "
            "monitored by snitchvis with `.channel add #channel` and "
            "`.channel remove #channel` respectively. Please do so now if "
            "snitchvis didn't find the right snitch channels. You can list the "
            "current snitch channels with `.channel list`.")
        await message.channel.send("Once you're satisfied with the list of "
            "snitch channels, run `.index` to index the snitch pings in those "
            "channels.")

    async def channel_add(self, message):
        channels = message.channel_mentions

        if not channels:
            await message.channel.send("Please mention (`#channel`) one or "
                "more snitch channels in your command.")
            return

        for channel in channels:
            db.add_snitch_channel(channel)

        new_channels = db.get_snitch_channels(message.guild)
        await message.channel.send(f"Added {utils.channel_str(channels)} to "
            f"snitch channels.\n{utils.snitch_channels_str(new_channels)}")

    async def channel_remove(self, message):
        channels = message.channel_mentions

        if not channels:
            await message.channel.send("Please mention (`#channel`) one or "
                "more snitch channels in your command.")
            return

        for channel in channels:
            db.remove_snitch_channel(channel)

        new_channels = db.get_snitch_channels(message.guild)
        await message.channel.send(f"Removed {utils.channel_str(channels)} "
            "from snitch channels.\n"
            f"{utils.snitch_channels_str(new_channels)}")

    async def channel_list(self, message):
        channels = db.get_snitch_channels(message.guild)
        m = utils.snitch_channels_str(channels)
        await message.channel.send(m)

    async def index(self, message):
        channels = db.get_snitch_channels(message.guild)

        if not channels:
            await message.channel.send("No snitch channels to index. Use "
                "`.channel add #channel` to add snitch channels.")
            return

        await message.channel.send("Indexing the following snitch channels: "
            f"{utils.channel_str(channels)}. This could take a LONG time if "
            "they have lots of messages in them.")

        for channel in channels:
            await message.channel.send(f"Indexing {channel.mention}...")

            events = []
            # convert to an actual channel object so we can retrieve history
            c = channel.to_discord(message.guild)
            last_id = channel.last_indexed_id
            async for message_ in c.history(limit=None):
                # don't index past the last indexed message id (if we have such
                # an id stored)
                if last_id and message_.id <= last_id:
                    break

                content = remove_markdown(message_.content)
                try:
                    event = Event.parse(content)
                except InvalidEventException:
                    continue
                events.append([message_, event])

            last_messages = await c.history(limit=1).flatten()
            # only update if the channel has messages
            if last_messages:
                last_message = last_messages[0]
                db.update_last_indexed(channel, last_message.id)

            for (message_, event) in events:
                # batch commit for speed
                db.add_event(message_, event, commit=False)
            db.commit()

            await message.channel.send(f"Added {len(events)} new events from "
                f"{channel.mention}")

        await message.channel.send("Finished indexing snitch channels")

    async def maybe_index_message(self, message):
        snitch_channel = db.get_snitch_channel(message.channel)
        # only index messages in snitch channels which have been fully indexed
        # by `.index` already. If someone adds a snitch channel with
        # `.channel add #snitches`, and then a snitch ping is immediately sent
        # in that channel, we don't want to update the last indexed id (or
        # index the message at all) until the channel has been fully indexed
        # manually.
        if not snitch_channel or not snitch_channel.last_indexed_id:
            return

        content = remove_markdown(message.content)
        try:
            event = Event.parse(content)
        except InvalidEventException:
            return

        db.add_event(message, event)
        db.update_last_indexed(message.channel, message.id)

    async def visualize(self, message):
        NO_EVENTS = ("No events match those criteria. Try adding snitch "
            "channels with `.channel add #channel`, indexing with `.index`, or "
            "adjusting your parameters to include more snitch events.")
        # TODO make defaults for these parameters configurable
        parser = ArgParser(message, exit_on_error=False)
        parser.add_arg("-a", "--all-snitches", action="store_true",
            default=False)
        parser.add_arg("-s", "--size", default=500, type=int)
        parser.add_arg("-f", "--fps", default=20, type=int)
        parser.add_arg("-d", "--duration", default=5, type=int)
        parser.add_arg("-u", "--users", nargs="*", default=[])
        parser.add_arg("-p", "--past", type=human_timedelta)
        # TODO add converter for human readable datetime strings, eg
        # 06/05/2022
        parser.add_arg("--start")
        parser.add_arg("--end")
        parser.add_arg("--fade", default=10, type=float)

        args = message.content.split(" ")[1:]
        args = await parser.parse_args(args)
        # error handling done by argparser
        if not args:
            return

        if args.past:
            end = datetime.utcnow().timestamp()
            if args.past == "all":
                # conveniently, start of epoch is 0 ms
                start = 0
            else:
                start = end - args.past
        else:
            if args.start and args.end:
                # TODO
                pass
            else:
                # slightly special behavior: instead of going back in the past
                # `x` ms, go back to the most recent event (however long ago
                # that may be) and *then* go back `x` ms and grab all those
                # events.
                event = db.most_recent_event(message.guild)
                # if the guild doesn't have any events at all yet, complain and
                # exit.
                if not event:
                    await message.channel.send(NO_EVENTS)
                    return
                end = event.t.timestamp()
                # TODO make adjustable instead of hardcoding 30 minutes, not
                # sure what parameter name to use though (--past-adjusted?)
                start = end - (30 * 60)

        snitch_channels = db.get_snitch_channels(message.guild)
        channel_ids = []
        for channel in snitch_channels:
            channel = channel.to_discord(message.guild)
            permissions = channel.permissions_for(message.author)
            # only retrieve events for channels this user has access to
            if not permissions.read_messages:
                continue

            channel_ids.append(channel.id)

        events = db.get_events(message.guild, start, end, args.users,
            channel_ids)
        # TODO warn if no events by the specified users are in the events filter

        if not events:
            await message.channel.send(NO_EVENTS)
            return

        all_events = db.get_all_events(message.guild)
        # use all known events to construct snitches
        snitches = snitches_from_events(all_events)
        # if the guild has any snitches uploaded (via .import-snitches), use
        # those as well, even if they've never been pinged
        snitches |= set(db.get_snitches(message.guild))
        users = create_users(events)
        size = args.size
        fps = args.fps
        duration = args.duration * 1000
        show_all_snitches = args.all_snitches
        event_fade_percentage = args.fade
        output_file = "out.mp4"

        def run_snitch_vis():
            vis = SnitchVisRecord(snitches, events, users, size, fps,
                duration, show_all_snitches, event_fade_percentage, output_file)
            vis.exec()

        m = await message.channel.send("rendering video...")
        # TODO does this incur an overhead compared to running it syncly?
        # probably not, but worth a check.
        await self.loop.run_in_executor(None, run_snitch_vis)
        vis_file = File(output_file)
        await message.channel.send(file=vis_file)
        await m.delete()

    async def import_snitches(self, message):
        attachments = message.attachments
        if not attachments:
            await message.channel.send("You must upload a snitch.sqlite file "
                "as part of the `.import-snitches` command")
            return

        await message.channel.send("Importing snitches from snitchmod "
            "database...")

        with NamedTemporaryFile() as f:
            attachment = attachments[0]
            await attachment.save(f.name)
            conn = sqlite3.connect(f.name)
            cur = conn.cursor()
            rows = cur.execute("SELECT * FROM snitches_v2").fetchall()

            snitches_added = 0
            for row in rows:
                snitch = Snitch.from_snitchmod(row)
                # batch commit for speed
                cur = db.add_snitch(message.guild, snitch, commit=False)
                snitches_added += cur.rowcount

            db.commit()

        await message.channel.send(f"Added {snitches_added} new snitches.")

client = MyClient()
client.run(TOKEN)

# TODO reindex known snitch channels on bot restart, careful to immediately
# retrieve last_indexed_id so it doesn't get updated by an incoming message and
# cause us to lose all messages between the last indexed message and the
# incoming message (messages might have come in while the bot was down that are
# waiting to be indexed).

# TODO "lines" mode in visualizer which draws colored lines between events
# instead of highlighted boxes (what to do about single events? probably just a
# colored square, ie a line between itself and itself) and breaks up events by a
# time period, ~1hr. Also draws arrows on the lines to indicate directionality.

# TODO add "centered at (x, y)" coordinates to info text, can be confusing where
# the vis is sometimes
