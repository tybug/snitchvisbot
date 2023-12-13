import dateparser
from datetime import timezone
import asyncio
import itertools

from discord import Color, Embed

queue = asyncio.Queue()

def fire_later(awaitable):
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    # queue has no max size, so put_nowait is safe
    queue.put_nowait((future, awaitable))
    return future

embed_grey = Color.from_rgb(156, 156, 156)

def channel_str(channels):
    return ", ".join(channel.mention for channel in channels)

def role_str(roles):
    return "`" + "`, `".join(role.name for role in roles) + "`"

def channel_accessible(guild, channel):
    roles = channel.allowed_roles_to_discord(guild)
    roles_str = role_str(roles)
    return f"{channel.mention} (accessible by {roles_str})"

def try_dateparser(val):
    from command import ParseError
    settings = {
        "TIMEZONE": "utc"
    }

    datetime = dateparser.parse(val, settings=settings)
    if datetime is None:
        raise ParseError(f"Invalid time `{val}`.")

    return datetime.replace(tzinfo=timezone.utc)

def message_log_prefix(guild, author):
    return f"[`{guild.name}`] [{author.mention} / `{author.name}`]"


def align_right_column(val, *, newline_after, spacing, offset):
    newline_points = []
    potential_newline_point = None
    chars_seen = 0
    i = 0
    for c in val:
        # only allow newlines on whitespace. Only add a newline at the
        # latest possible point.
        if c == " ":
            potential_newline_point = i
        # skip the first iteration
        if i > offset and chars_seen % (newline_after - offset) == 0:
            newline_points.append(potential_newline_point)
            # we might have added a newline early and have spillover
            # chars.
            chars_seen = i - potential_newline_point
        i += 1
        chars_seen += 1

    for idx in reversed(newline_points):
        newline = f"\n{' ' * spacing}"
        assert val[idx] == " "
        # replace val[idx] (a space) with the newline
        val = val[:idx] + newline + val[idx + 1:]
    return val


def align_two_column_table(values, *, max_left_size):
    left_spacing = " " * 2
    middle_spacing = " " * 4
    right_column_newline_after = 60
    # dont let a single very long arg cause extremely long messages.
    max_size = min(max_left_size, max(len(left) for (left, _) in values))

    # uncomment for a sampling of ansi colors
    # colors = itertools.cycle([None] + [getattr(ANSI, attr) for attr in dir(ANSI) if not attr.startswith("__")])
    # print([attr for attr in dir(ANSI) if not attr.startswith("__")])

    # None of the ansi choices are very good for a zebra striped table.
    # plain coloring combined with ANSI.GREEN or ANSI.BROWN seems like the
    # best combination.
    colors = itertools.cycle([None, ANSI.GREEN])

    def align_value(left, right):
        total_spacing = len(left_spacing) + max_size + len(middle_spacing)
        # offset caused by arg going out of bounds of max_size. need to
        # inset our aligned right column to account for this
        offset = max(0, len(left) - max_size)

        aligned_right = align_right_column(
            right,
            newline_after=right_column_newline_after,
            spacing=total_spacing,
            offset=offset
        )

        v = f"{left_spacing}{left:{max_size}}{middle_spacing}{aligned_right}"
        color = next(colors)
        if color is not None:
            v = f"{color}{v}{ANSI.END}"

        return v

    return"\n".join(align_value(left, right) for (left, right) in values)


def create_embed(content, *, color=embed_grey):
    return Embed(description=content, color=color)

# https://gist.github.com/kkrypt0nn/a02506f3712ff2d1c8ca7c9e0aed7c06
class ANSI:
    BLACK = "\u001b[0;30m"
    RED = "\u001b[0;31m"
    GREEN = "\u001b[0;32m"
    BROWN = "\u001b[0;33m"
    BLUE = "\u001b[0;34m"
    PURPLE = "\u001b[0;35m"
    CYAN = "\u001b[0;36m"
    WHITE = "\u001b[0;37m"

    # I don't think these names are right. I think [1; means bold for discord
    # not, "light".
    DARK_GREY = "\u001b[1;30m"
    LIGHT_RED = "\u001b[1;31m"
    LIGHT_GREEN = "\u001b[1;32m"
    YELLOW = "\u001b[1;33m"
    LIGHT_BLUE = "\u001b[1;34m"
    LIGHT_PURPLE = "\u001b[1;35m"
    LIGHT_CYAN = "\u001b[1;36m"
    LIGHT_WHITE = "\u001b[1;37m"

    BOLD = "\u001b[1m"
    FAINT = "\u001b[2m"
    ITALIC = "\u001b[3m"
    UNDERLINE = "\u001b[4m"
    BLINK = "\u001b[5m"
    NEGATIVE = "\u001b[7m"
    CROSSED = "\u001b[9m"
    END = "\u001b[0m"
