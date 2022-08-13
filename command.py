import re
import shlex
from datetime import timedelta, datetime
import inspect

import config

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
        for permission in self.permissions:
            # handle custom permissions
            if permission == "author":
                if message.author.id != config.AUTHOR_ID:
                    await message.channel.send("Only the bot author "
                        f"(<@{config.AUTHOR_ID}>) can run that command.")
                    return
                continue

            permissions = message.author.permissions_in(message.channel)
            if not getattr(permissions, permission):
                await message.channel.send("You do not have permission to do "
                    f"that (requires `{permission}`).")
                return

        # preserve quoted arguments with spaces as a single argument
        arg_strings = shlex.split(arg_string)
        # inlude em dash special case for phones
        if any(arg_string in ["--help", "-h", "—help"] for arg_string in arg_strings):
            help_message = self.help_message()
            # ugly hardcode hack.
            # things will get messy if we ever split somewhere other than in
            # the middle of a code block, but only `.r -h` invokes this edge
            # case for now.
            if len(help_message) >= 2000:
                # 25 chars of buffer
                message1 = help_message[:1975] + "\n```"
                message2 = "```\n" + help_message[1975:]
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
        except (ParseError, ValueError) as e:
            await message.channel.send(f"{e}\nRun `.{self.name} --help` for "
                "more information.")

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
            # TODO implement ? and n (1/2/3/etc) nargs, I'm not sure I'll ever
            # use them
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
        choices=None, convert_mode="individual"
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
        self.flags = [short, long, long.replace("--", "—")] if long else [short]
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


# a command converter from a human-readable timedelta string representation
# (eg "4d1h30m20s") to seconds.
def human_timedelta(val):
    # special-case value. shorthand for 999999y, or some other ridiculously
    # big number. The calling code is responsible for handling this as a special
    # case.
    # TODO could we get clever here by returning the current datetime? probably
    # would be susceptible to deviations due to computation time. maybe the
    # entire api (ie return value/type) of this function needs to be rethought?
    if val == "all":
        return "all"

    # Small handwritten lexer. There might be a better way to do this.

    # year, month, week, day, hour, minute, second
    units = ["y", "mo", "w", "d", "h", "m", "s"]
    # unit to integer
    unit_vals = {}
    # buffer for current integer string
    current_int_str = ""

    # handle `mo` case with val[-2:]
    if val[-1] not in units and val[-2:] not in units:
        raise ParseError("Expected last character to be one of "
            f"`{', '.join(units)}`, got `{val[-1]}`")

    i = 0
    while i < len(val):
        char = val[i]
        i += 1

        # peak ahead one char to resolve ambiguity between `m` and `mo`.
        if i != len(val) and char + val[i] in units:
            char = char + val[i]
            # extra increment to handle extra char
            i += 1
        elif char not in units:
            # integer validity will be checked later, when we can give a better
            # error message
            current_int_str += char
            continue

        if not current_int_str:
            raise ParseError(f"`{char}` must be preceeded by an integer")

        try:
            val_ = int(current_int_str)
        except ValueError:
            raise ParseError(f"Expected a valid integer to preceed `{char}`, "
                f"got {current_int_str}")

        if char in unit_vals:
            raise ParseError(f"Cannot specify `{char}` twice")

        unit_vals[char] = val_
        current_int_str = ""

    y = unit_vals.get("y", 0)
    mo = unit_vals.get("mo", 0)
    w = unit_vals.get("w", 0)
    d = unit_vals.get("d", 0)
    h = unit_vals.get("h", 0)
    m = unit_vals.get("m", 0)
    s = unit_vals.get("s", 0)

    # is this accurate? no. will it be good enough? probably.
    weeks = (y * 52) + (mo * 4) + w
    return timedelta(weeks=weeks, days=d, hours=h, minutes=m, seconds=s)

# TODO parse time as well, not just date (eg 7/12/2022 8:02:20 PM, or probably
# 24 hour clock, or support both)
def human_datetime(val):

    # month, day, year
    parts = ["", "", ""]
    parsing_i = 0
    for char in val:
        if char == "/":
            parsing_i += 1
            continue
        if parsing_i > 2:
            raise ParseError(f"Invalid date `{val}`. Expected format "
                "`mm/dd/yyy`.")
        if char == " ":
            break
        parts[parsing_i] = parts[parsing_i] + char

    # make sure we've parsed at least one char for all of month/day/year
    if parsing_i != 2:
        raise ParseError(f"Invalid date `{val}`. Expected format `mm/dd/yyyy`.")

    try:
        month = int(parts[0])
    except ValueError:
        raise ParseError(f"Invalid month `{parts[0]}`.")

    try:
        day = int(parts[1])
    except ValueError:
        raise ParseError(f"Invalid day `{parts[1]}`.")

    try:
        year = int(parts[2])
        # allow users to specify short years with just two digits. Yes, this
        # will break in a century. I'm not worried about it.
        if len(parts[2]) == 2:
            year += 2000
        if len(parts[2]) not in [2, 4]:
            raise ParseError(f"Invalid year `{year}`. Must be either 2 or 4 "
                "digits.")
    except ValueError:
        raise ParseError(f"Invalid year `{parts[2]}`.")

    if not 1 <= month <= 12:
        raise ParseError(f"Invalid month `{month}`. Must be between `1` and "
            "`12` inclusive.")

    if not 1 <= day <= 31:
        raise ParseError(f"Invalid day `{day}`. Must be between `1` and `31` "
            "inclusive.")

    return datetime(year=year, month=month, day=day)

def bounds(val):
    if len(val) != 4:
        raise ParseError(f"Invalid bounds {val}. Must be in the format "
            "`x1 z1 x2 z2`, eg `0 0 400 -400`.")

    x1 = int(val[0])
    y1 = int(val[1])
    x2 = int(val[2])
    y2 = int(val[3])

    if x1 >= x2:
        raise ParseError(f"x1 ({x1}) must be less than x2 ({x2}). Remember "
            "that the first two "
            "values specify the bottom left corner, and the last two values "
            "specify the top right corner.")
    if y1 <= y2:
        raise ParseError(f"z1 ({y1}) must be greater than z2 ({y2}). Remember "
            "that the first "
            "two values specify the bottom left corner, and the last two "
            "values specify the top right corner.")

    return [x1, y1, x2, y2]
