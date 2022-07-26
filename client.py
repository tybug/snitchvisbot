import inspect

from discord import Client as _Client

from command import Command

class Client(_Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix = "."
        self.commands = []

        # collect all registered commands
        for func in inspect.getmembers(self, predicate=inspect.ismethod):
            (_func_name, func) = func
            if hasattr(func, "_is_command"):
                command = Command(func._name, func._args, func)
                self.commands.append(command)

    async def on_message(self, message):
        content = message.content
        for command in self.commands:
            command_name = self.prefix + command.name
            # TODO avoid accidental collisions, eg .v and .v2
            if content.startswith(command_name):
                # also strip any whitespace, particularly after the command name
                args = content.removeprefix(command_name).strip()
                await command.invoke(message, args)
