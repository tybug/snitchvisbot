from discord import Client
from discord.utils import remove_markdown
from snitchvis import Event, InvalidEventException

import db
import utils
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
            return

        for channel in snitch_channels:
            # just ignore duplicate snitch channels if the user runs setup
            # multiple times, won't hurt anything and they can always remove it
            # manually
            if db.snitch_channel_exists(channel):
                continue
            db.add_snitch_channel(channel)

        channel_str = utils.channel_str(snitch_channels)
        await message.channel.send("Found the following snitch channels: "
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
        for channel in channels:
            db.add_snitch_channel(channel)

        new_channels = db.get_snitch_channels(message.guild)
        await message.channel.send(f"Added {utils.channel_str(channels)} to "
            f"snitch channels.\n{utils.snitch_channels_message(new_channels)}")

    async def channel_remove(self, message):
        channels = message.channel_mentions
        for channel in channels:
            db.remove_snitch_channel(channel)

        new_channels = db.get_snitch_channels(message.guild)
        await message.channel.send(f"Removed {utils.channel_str(channels)} "
            "from snitch channels.\n"
            f"{utils.snitch_channels_message(new_channels)}")

    async def channel_list(self, message):
        channels = db.get_snitch_channels(message.guild)
        m = utils.snitch_channels_message(channels)
        await message.channel.send(m)

    async def index(self, message):
        pass

client = MyClient()
client.run(TOKEN)

# TODO reindexing known snitch channels (automatically / on command)
