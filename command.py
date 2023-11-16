import re
import shlex
import inspect

from discord.utils import utcnow

import config
from utils import try_dateparser

class ParseError(Exception):
    pass

class Command:
    def __init__(self, function, name, args, help, help_short, permissions,
        use_prefix, parse, *, alias=False
    ):
        self.name = name
        self.args = args
        self.function = function
        self.help = help
        self.help_short = help_short or help
        self.permissions = permissions
        self.use_prefix = use_prefix
        # whether to parse arguments or not. Will almost always be true, but
        # some commands have such custom parsing needs that it's easier to just
        # give them the input text wholesale
        self.parse = parse
        self.alias = alias

    def __eq__(self, other):
        return self.function == other.function and self.name == other.name

    def __hash__(self):
        return hash((self.function, self.name))

    def help_message(self):
        positional_args = [arg for arg in self.args if arg.positional]
        flag_args = [arg for arg in self.args if not arg.positional]

        text = f"{self.help}\n"

        arg_text = ""
        for arg in positional_args:
            arg_text += f"\n  {arg}: {arg.help}"
        if positional_args:
            text += f"\nPositional Arguments:\n```{arg_text}\n```"

        arg_text = ""
        for arg in flag_args:
            arg_text += f"\n  {arg}: {arg.help}"
        if flag_args:
            text += f"\nOptions:\n```{arg_text}\n```"

        return text

    async def invoke(self, message, arg_string):
        permissions = message.channel.permissions_for(message.author)
        for permission in self.permissions:
            # handle custom permissions
            if permission == "author":
                if message.author.id != config.AUTHOR_ID:
                    await message.channel.send("Only the bot author "
                        f"(<@{config.AUTHOR_ID}>) can run that command.")
                    return
                continue

            if not getattr(permissions, permission):
                await message.channel.send("You do not have permission to do "
                    f"that (requires `{permission}`).")
                return

        # smart quotes strike again! you will be shocked to hear that shlex does
        # not split on these.
        arg_string = arg_string.replace("“", "\"")
        arg_string = arg_string.replace("”", "\"")

        try:
            # preserve quoted arguments with spaces as a single argument
            arg_strings = shlex.split(arg_string)
        except ValueError:
            # lone single quotations break things. apparently single quotes are
            # valid identifiers in namelayer groups. sigh.
            await message.channel.send("No matching parenthesis found. If you "
                "are passing a parameter which contains a single quote "
                "(e.g. `my_nation's_group`), wrap that parameter in double "
                "quotes instead (e.g. `\"my_nation's_group\"`)")
            return

        # inlude em dash special case for phones
        if any(arg_string in ["--help", "-h", "—help"] for arg_string in arg_strings):
            help_message = self.help_message()
            # ugly hardcode hack.
            # things will get messy if we ever split somewhere other than in
            # the middle of a code block, but only `.r -h` invokes this edge
            # case for now.
            if len(help_message) >= 2000:
                # few hundred chars of buffer
                message1 = help_message[:1800] + "\n```"
                message2 = "```\n" + help_message[1800:]
                await message.channel.send(message1)
                await message.channel.send(message2)
                return

            await message.channel.send(self.help_message())
            return

        try:
            await self._invoke(message, arg_strings)
        # ideally we'll raise ParseErrors with nice error messages, but in the
        # worst case of a python-level error, catch anyway so we don't just
        # silently fail.
        except ParseError as e:
            await message.channel.send(f"{e}\nRun `.{self.name} --help` for "
                "more information.")
        except Exception as e:
            await message.channel.send("Encountered fatal error shown below "
                "while running command. Contact tybug if the issue persists."
                "\n```\n"
                f"{e}\n"
                "```")
            raise e

    async def _invoke(self, message, arg_strings):
        if not self.parse:
            await self.function(message, args=arg_strings)
            return

        kwargs = {}
        # index into arg_strings
        i = 0
        positional_args = [arg for arg in self.args if arg.positional]
        flag_args = [arg for arg in self.args if not arg.positional]

        def is_flag(arg_string):
            if len(arg_string) == 1:
                return False
            # include em dash case
            return arg_string[0] in ["-", "—"] and not arg_string[1].isdigit()

        # deal with positional arguments first.
        for arg in positional_args:
            if i == len(arg_strings):
                raise ParseError(f"Missing parameter for `{arg}`.")

            # always process positional args at least once - positional args
            # can't be optional
            val = arg.process(message, arg_strings[i])
            i += 1

            # the only valid nargs besides `None` is `+`. In this case, greedily
            # process the rest of the arg strings until we hit a flag.
            if arg.nargs == "+":
                val = [val]
                while i < len(arg_strings):
                    arg_string = arg_strings[i]
                    if is_flag(arg_string):
                        break
                    i += 1
                    val.append(arg.process(message, arg_string))

            kwargs[arg.dest] = val

        # now handle non-positional arguments (ie -f/--flag arguments).
        while i < len(arg_strings):
            arg_string = arg_strings[i]
            i += 1
            if not is_flag(arg_string):
                raise ParseError("Invalid positional parameter "
                    f"`{arg_string}`.")

            arg = [arg for arg in flag_args if arg_string in arg.flags]
            if not arg:
                raise ParseError(f"Invalid argument `{arg_string}`.")
            arg = arg[0]

            if arg.store_boolean:
                # make sure this flag isn't being given a parameter if it's
                # store_boolean
                if i < len(arg_strings) and not is_flag(arg_strings[i]):
                    raise ParseError(f"`{arg}` doesn't take any parameters.")
                val = True
            # process a variable number of arg_strings depending on nargs.
            elif arg.nargs is None:
                # process just once
                if i == len(arg_strings):
                    raise ParseError(f"`{arg}` requires at least one "
                        "parameter.")
                val = arg.process(message, arg_strings[i])
                i += 1
            elif arg.nargs == "*":
                val = []
                while i < len(arg_strings) and not is_flag(arg_strings[i]):
                    v = arg_strings[i]
                    if arg.convert_mode == "individual":
                        val.append(arg.process(message, v))
                    else:
                        val.append(v)
                    i += 1
                if arg.convert_mode == "together":
                    val = arg.process(message, val)

            elif arg.nargs == "+":
                # TODO handle arg.convert_mode in nargs="+"
                val = []
                # always process at least one val
                if i == len(arg_strings) or is_flag(arg_strings[i]):
                    raise ParseError(f"`{arg}` requires at least one "
                        "parameter.")
                val.append(arg.process(message, arg_strings[i]))
                i += 1
                # then process like *
                while i < len(arg_strings) and not is_flag(arg_strings[i]):
                    val.append(arg.process(message, arg_strings[i]))
                    i += 1
            elif arg.nargs == "?":
                if i < len(arg_strings) and not is_flag(arg_strings[i]):
                    val = arg.process(message, arg_strings[i])
                    i += 1
                else:
                    val = arg.const
            # TODO implement n (1/2/3/etc) nargs, I'm not sure I'll ever use
            # them
            else:
                raise Exception(f"unimplemented nargs option {arg.nargs}")

            kwargs[arg.dest] = val

        # check if any parameters are missing, and assign default values if
        # appropriate
        for arg in flag_args:
            # check for required arguments which haven't been assigned a value
            # yet
            if arg.dest not in kwargs and arg.default is None and arg.required:
                raise ParseError(f"`{arg}` is required.")

            # assign default values if not present
            if arg.dest not in kwargs:
                kwargs[arg.dest] = arg.default

        # validate argument values against valid choices if specified
        for arg in self.args:
            if not arg.choices:
                continue

            val = kwargs[arg.dest]
            # if the argument wasn't passed, don't validate it against choices
            if val is None:
                continue

            if val not in arg.choices:
                raise ParseError(f"`{arg}` must be one of "
                    f"`{'`, `'.join(arg.choices)}`.")

        await self.function(message, **kwargs)

def command(name, *, args=[], help=None, help_short=None, permissions=[],
    aliases=[], use_prefix=True, parse=True
):
    if not help:
        raise Exception("Help text is required for all commands.")

    def decorator(f):
        f._is_command = True
        f._name = name
        f._args = args
        f._help = help
        f._help_short = help_short
        f._permissions = permissions
        f._aliases = aliases
        f._use_prefix = use_prefix
        f._parse = parse
        return f
    return decorator

class Arg:
    def __init__(self, short, long=None, *, default=None, convert=None,
        nargs=None, store_boolean=False, required=False, dest=None, help=None,
        choices=None, convert_mode="individual", const=None
    ):
        if not short.startswith("-"):
            positional = True
            dest_ = short
        elif not long:
            positional = False
            dest_ = short
        else:
            positional = False
            dest_ = long

        # nargs of * implies a default of an empty list
        if default is None and nargs == "*":
            default = []
        # nargs of + implies a required argument
        if nargs == "+":
            required = True

        dest = dest or dest_
        self.positional = positional
        # remove prefix twice to remove both - and -- prefixes
        self.dest = dest.removeprefix("-").removeprefix("-").replace("-", "_")
        # some (all? at least ios) phones replace two dashes with an em dash, so
        # also add an em dash flag to account for this.

        short_em = short.replace("--", "—")
        self.flags = [short, short_em]
        if long:
            self.flags += [long, long.replace("--", "—")]

        self.short = short
        self.long = long
        self.default = default
        self.convert = convert
        self.nargs = nargs
        self.store_boolean = store_boolean
        self.required = required
        self.help = help
        self.choices = choices
        # one of "individual" or "together"
        self.convert_mode = convert_mode
        self.const = const

        if help is None:
            raise Exception("Help text is required for all arguments.")

    def __str__(self):
        if self.short and self.long:
            return f"{self.short}/{self.long}"
        return self.short

    def process(self, message, val):
        if self.convert:
            # some converters don't need access to the `message` context and so
            # only accept a single variable. Support these converters (which
            # include useful converters like just `int` or `float`).

            # these converters aren't strictly speaking functions and so don't
            # have a signature. avoid erroring with a inspect.signature call.
            if self.convert in [int, float]:
                return self.convert(val)
            sig = inspect.signature(self.convert)
            if len(sig.parameters) == 1:
                return self.convert(val)
            return self.convert(message, val)
        return val

def channel(message, val):
    match = re.match(r"<#([0-9]+)>", val)
    if not match:
        raise ParseError(f"Invalid channel `{val}`. Make sure to use a proper "
            "channel mention (eg `#snitches`) instead of just the channel name.")
    channel_id = int(match.group(1))
    return message.guild.get_channel(channel_id)

def role(message, val):
    # allow a special value of "everyone" to refer to the default role without
    # pinging it. Won't be caught by our role name handling below because the
    # default guild role actually has a name of @everyone, not just everyone.
    if val == "everyone":
        return message.guild.default_role

    match = re.match(r"<@&([0-9]+)>", val)
    if not match:
        # people don't want to ping roles when specifying them as arguments, so
        # also allow specifying the role's name instead of mentioning it.
        for role in message.guild.roles:
            if role.name.lower() == val.lower():
                return role
        raise ParseError(f"Invalid role `{val}`.")
    role_id = int(match.group(1))
    return message.guild.get_role(role_id)


def human_timedelta(val):
    # special-case value. The calling code is responsible for handling this
    # case.
    # TODO could we get clever here by returning the current datetime? probably
    # would be susceptible to deviations due to computation time.
    if val == "all":
        return "all"

    datetime = try_dateparser(val)
    return utcnow() - datetime

def human_datetime(vals):
    val = " ".join(vals)
    return try_dateparser(val)

def bounds(val):
    if len(val) != 4:
        raise ParseError(f"Invalid bounds {val}. Must be in the format "
            "`x1 z1 x2 z2`, eg `0 0 400 -400`.")

    x1 = int(val[0])
    y1 = int(val[1])
    x2 = int(val[2])
    y2 = int(val[3])

    # assume (x1, y1) and (x2, y2) are opposing vertices on a rectangle.
    # compute the lower left and upper right vertices (which might in fact be
    # the original points).

    ll_x = min(x1, x2)
    ll_y = min(y1, y2)
    ur_x = max(x1, x2)
    ur_y = max(y1, y2)

    return [ll_x, ll_y, ur_x, ur_y]
