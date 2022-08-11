import inspect
import traceback

from discord import Client as _Client

from command import Command
import db
import config

class Client(_Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_prefix = config.DEFAULT_PREFIX
        # guild id to prefix. cache snitch prefix to avoid unecessary db hits
        self.prefixes = {}
        self.commands = []
        self.command_log_channel = None
        self.join_log_channel = None
        self.error_log_channel = None

        # collect all registered commands
        for func in inspect.getmembers(self, predicate=inspect.ismethod):
            (_func_name, func) = func
            if not hasattr(func, "_is_command"):
                continue

            command = Command(func, func._name, func._args, func._help,
                func._help_short, func._permissions, func._use_prefix,
                func._parse)
            self.commands.append(command)

            for name in func._aliases:
                command = Command(func, name, func._args, func._help,
                    func._help_short, func._permissions, func._use_prefix,
                    func._parse, alias=True)
                self.commands.append(command)

    async def on_ready(self):
        # convert to discord object once we're connected to discord
        if config.COMMAND_LOG_CHANNEL:
            self.command_log_channel = self.get_channel(config.COMMAND_LOG_CHANNEL)
        if config.JOIN_LOG_CHANNEL:
            self.join_log_channel = self.get_channel(config.JOIN_LOG_CHANNEL)
        if config.ERORR_LOG_CHANNEL:
            self.error_log_channel = self.get_channel(config.ERORR_LOG_CHANNEL)

    async def on_guild_join(self, guild):
        if self.join_log_channel:
            await self.join_log_channel.send(f"Joined new guild `{guild.name}` "
                f"/ `{guild.id}`")
        db.create_new_guild(guild.id)

    async def on_error(self, event_method, *args, **kwargs):
        await super().on_error(event_method, *args, **kwargs)

        if self.error_log_channel:
            err = traceback.format_exc()
            await self.error_log_channel.send(f"Ignoring exception in "
                f"{event_method}: \n```\n{err}\n```")


    async def on_message(self, message):
        await self.maybe_handle_command(message, message.content)

    async def maybe_handle_command(self, message, content):
        author = message.author
        guild = message.guild

        # only respond to messages from whitelisted guilds if testing, to avoid
        # responding from commands from actual users
        if config.TESTING and guild.id not in config.TESTING_GUILDS:
            return

        if guild.id not in self.prefixes:
            prefix = db.get_snitch_prefix(guild.id)
            # fall back to default prefix if no prefix specified
            if prefix is None:
                self.prefixes[guild.id] = self.default_prefix
        prefix = self.prefixes[guild.id]

        custom_commands = db.get_commands(guild.id)

        for command in self.commands + custom_commands:
            if not self.command_matches(guild.id, command, content):
                continue

            if command.use_prefix:
                command_name = prefix + command.name

            # don't log commands by the author, gets annoying for testing
            if author.id != config.AUTHOR_ID and self.command_log_channel:
                await self.command_log_channel.send(f"[`{guild.name}`] "
                    f"[{author.mention} / `{author.name}`] `{content}`")

            # also strip any whitespace, particularly after the command name
            args = content.removeprefix(command_name).strip()

            if isinstance(command, Command):
                await command.invoke(message, args)
            else:
                # recurse on the aliased command text. Should never infinitely
                # recurse because we require new commands to invoke an existing
                # command, so they should never self reference or loop.
                await self.maybe_handle_command(message, command.command_text)

    def command_matches(self, guild_id, command, content):
        if guild_id not in self.prefixes:
            prefix = db.get_snitch_prefix(guild_id)
            # fall back to default prefix if no prefix specified
            if prefix is None:
                self.prefixes[guild_id] = self.default_prefix
        prefix = self.prefixes[guild_id]

        command_name = command.name
        # some commands don't respect the prefix at all, eg
        # snitchvissetprefix
        if command.use_prefix:
            command_name = prefix + command.name

        # avoid eg .r matching .render by requiring the input to either match
        # exactly, or match the command name with a space.
        return (
            content.startswith(command_name + " ") or
            content == command_name
        )
