from datetime import timedelta
from argparse import ArgumentParser, ArgumentError

def channel_str(channels):
    return ", ".join(channel.mention for channel in channels)

def snitch_channels_str(channels):
    if not channels:
        return "No snitch channels set"
    return f"Current snitch channels: {channel_str(channels)}"

class SnitchvisParsingException(Exception):
    pass

class ArgParser(ArgumentParser):
    def __init__(self, message, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message = message

    def add_arg(self, *args, **kwargs):
        self.add_argument(*args, **kwargs)

    async def parse_args(self, *args, **kwargs):
        return await self.parse_known_args(*args, **kwargs)

    async def parse_known_args(self, *args, **kwargs):
        # everything after the first space (ie cut off `.v`)
        try:
            args, argv = super().parse_known_args(*args, **kwargs)
        except (ArgumentError, SnitchvisParsingException) as e:
            await self.message.channel.send(str(e))
            return None


        if argv:
            if len(argv) == 1:
                m = f"Unkown argument: `{argv[0]}`"
            else:
                m = f"Unknown arguments: `{'`, `'.join(argv)}`"
            await self.message.channel.send(m)
            return None
        return args

# an argparser converter from a human-readable timedelta string representation
# (eg "4d1h30m20s") to seconds.
def human_timedelta(input):
    # special-case value. shorthand for 999999y, or some other ridiculously
    # big number. The calling code is responsible for handling this as a special
    # case.
    # TODO could we get clever here by returning the current datetime? probably
    # would be susceptible to deviations due to computation time. maybe the
    # entire api (ie return value/type) of this function needs to be rethought?
    if input == "all":
        return "all"

    # Small handwritten lexer. There might be a better way to do this.

    # year, month, week, day, hour, minute, second
    units = ["y", "mo", "w", "d", "h", "m", "s"]
    # unit to integer
    unit_vals = {}
    # buffer for current integer string
    current_int_str = ""

    # handle `mo` case with input[-2:]
    if input[-1] not in units and input[-2:] not in units:
        raise SnitchvisParsingException("Expected last character to be one of "
            f"`{', '.join(units)}`, got `{input[-1]}`")

    i = 0
    while i < len(input):
        char = input[i]
        i += 1

        # peak ahead one char to resolve ambiguity between `m` and `mo`.
        if i != len(input) and char + input[i] in units:
            char = char + input[i]
            # extra increment to handle extra char
            i += 1
        elif char not in units:
            # integer validity will be checked later, when we can give a better
            # error message
            current_int_str += char
            continue

        if not current_int_str:
            raise SnitchvisParsingException(f"`{char}` must be preceeded by an "
                "integer")

        try:
            val = int(current_int_str)
        except ValueError:
            raise SnitchvisParsingException(f"Expected a valid integer to "
                f"preceed `{char}`, got {current_int_str}")

        if char in unit_vals:
            raise SnitchvisParsingException(f"Cannot specify `{char}` twice")

        unit_vals[char] = val
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
    td = timedelta(weeks=weeks, days=d, hours=h, minutes=m, seconds=s)
    return td.total_seconds()
