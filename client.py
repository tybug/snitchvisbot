import inspect

from discord import Client as _Client

from command import Command

class Client(_Client):
    def __init__(self, prefix, log_channel, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix = prefix
        self.commands = []
        self.log_channel = log_channel

        # collect all registered commands
        for func in inspect.getmembers(self, predicate=inspect.ismethod):
            (_func_name, func) = func
            if not hasattr(func, "_is_command"):
                continue

            command = Command(func, func._name, func._args, func._help,
                func._help_short, func._permissions)
            self.commands.append(command)

            for name in func._aliases:
                command = Command(func, name, func._args, func._help,
                    func._help_short, func._permissions, alias=True)
                self.commands.append(command)

    async def on_ready(self):
        # convert to discord object once we're connected to discord
        self.log_channel = self.get_channel(self.log_channel)

    async def on_message(self, message):
        content = message.content
        author = message.author
        for command in self.commands:
            command_name = self.prefix + command.name

            # avoid .r matching .render by requiring the input to either match
            # exactly, or match the command name with a space.
            if not (
                content.startswith(command_name + " ") or
                content == command_name
            ):
                continue

            # also strip any whitespace, particularly after the command name
            args = content.removeprefix(command_name).strip()
            await command.invoke(message, args)

            # hardcode some ids (eg me) to not send log mesages for
            if author.id in [216008405758771200]:
                continue

            await self.log_channel.send(f"[{author.mention} / `{author.name}` "
                f"/ `{author.id}`] `{content}`")
