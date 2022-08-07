import inspect

from discord import Client as _Client

from command import Command
import db

class Client(_Client):
    def __init__(self, default_prefix, log_channel, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_prefix = default_prefix
        # guild id to prefix. cache snitch prefix to avoid unecessary db hits
        self.prefixes = {}
        self.commands = []
        self.log_channel = log_channel

        # collect all registered commands
        for func in inspect.getmembers(self, predicate=inspect.ismethod):
            (_func_name, func) = func
            if not hasattr(func, "_is_command"):
                continue

            command = Command(func, func._name, func._args, func._help,
                func._help_short, func._permissions, func._use_prefix)
            self.commands.append(command)

            for name in func._aliases:
                command = Command(func, name, func._args, func._help,
                    func._help_short, func._permissions, func._use_prefix,
                    alias=True)
                self.commands.append(command)

    async def on_ready(self):
        # convert to discord object once we're connected to discord
        self.log_channel = self.get_channel(self.log_channel)

    async def on_guild_join(self, guild):
        await self.log_channel.send(f"Joined new guild `{guild.name}` / "
            f"`{guild.id}`")
        db.create_new_guild(guild)


    async def on_message(self, message):
        content = message.content
        author = message.author
        guild = message.guild

        if guild.id not in self.prefixes:
            prefix = db.get_snitch_prefix(guild)
            # fall back to default prefix if no prefix specified
            if prefix is None:
                self.prefixes[guild.id] = self.default_prefix

        prefix = self.prefixes[guild.id]

        for command in self.commands:

            command_name = command.name
            # some commands don't respect the prefix at all, eg
            # snitchvissetprefix
            if command.use_prefix:
                command_name = prefix + command.name

            # avoid .r matching .render by requiring the input to either match
            # exactly, or match the command name with a space.
            if not (
                content.startswith(command_name + " ") or
                content == command_name
            ):
                continue

            # hardcode some ids (eg me) to not send log mesages for
            if author.id not in [216008405758771200]:
                await self.log_channel.send(f"[{author.mention} / "
                    f"`{author.name}` / `{author.id}`] `{content}`")

            # also strip any whitespace, particularly after the command name
            args = content.removeprefix(command_name).strip()
            await command.invoke(message, args)
