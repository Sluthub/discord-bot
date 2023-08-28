from nextcord.ext import commands, tasks
import nextcord
import asyncio
import pathlib
import logging
import aiohttp
import shutil
import json

# Config options
LIBRARY_PATH: str
ADMIN_ROLE: int
VERIFIED_ROLE: int
VERIFY_CHANNEL: int
JELLYFIN_API: str

JELLYFIN_APIKEY: str
JELLYFIN_USERID: str
DISCORD_TOKEN: str

DISK_CHANNEL: int
MOVIES_CATEGORY: str
ANIME_CATEGORY: str
TV_CATEGORY: str
MOVIES_CHANNEL: int
ANIME_CHANNEL: int
TV_CHANNEL: int

env = pathlib.Path(".env.py")
if env.exists():
    exec(compile(env.read_text(), env.name, "exec"))

# Logging
LOGLEVEL = logging.INFO

class _LogFormat(logging.Formatter):
    LEVEL_COLOURS = [
        (logging.DEBUG, "\x1b[40;1m"),
        (logging.INFO, "\x1b[34;1m"),
        (logging.WARNING, "\x1b[33;1m"),
        (logging.ERROR, "\x1b[31m"),
        (logging.CRITICAL, "\x1b[41m"),
    ]
    FORMATS = {
        level: logging.Formatter(
            f"\x1b[30;1m%(asctime)s\x1b[0m {colour}%(levelname)-8s\x1b[0m \x1b[35m%(name)s\x1b[0m %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
        for level, colour in LEVEL_COLOURS
    }

    def format(self, record: logging.LogRecord):
        formatter = self.FORMATS.get(record.levelno)
        if formatter is None:
            formatter = self.FORMATS[logging.DEBUG]
        if record.exc_info:
            text = formatter.formatException(record.exc_info)
            record.exc_text = f"\x1b[31m{text}\x1b[0m"
        output = formatter.format(record)
        record.exc_text = None
        return output

_log = logging.getLogger()
_log.setLevel(LOGLEVEL)
_handler = logging.StreamHandler()
_handler.setFormatter(_LogFormat())
_log.addHandler(_handler)
log = logging.getLogger(__name__.strip("_"))
log.warn = log.warning

# Users
JELLYFIN_USERS: list[str] = []
KNOWN_USERS_FILE = pathlib.Path(__file__).absolute().parent / "known_users.json"
try:
    KNOWN_USERS: dict[str, int] = json.loads(KNOWN_USERS_FILE.read_bytes())
    assert isinstance(KNOWN_USERS, dict)
except Exception:
    KNOWN_USERS: dict[str, int] = {}
save_known_users = lambda: KNOWN_USERS_FILE.write_text(json.dumps(KNOWN_USERS))

# Bot
intents = nextcord.Intents().default()
intents.message_content = True
intents.members = True
bot = commands.Bot(intents=intents)


async def jellyfin_api(method: str, endpoint: str, **kwargs):
    headers = kwargs.pop("headers", {}) | {
        "Authorization": f'MediaBrowser Token="{JELLYFIN_APIKEY}"'
    }
    async with aiohttp.ClientSession() as cs:
        async with cs.request(
            method=method,
            url=JELLYFIN_API + endpoint,
            headers=headers,
            **kwargs
        ) as req:
            res = await req.json()
    return res


async def fetch_jellyfin_users():
    log.info("Fetching jellyfin users...")
    res: list[dict[str]] = await jellyfin_api("GET", "/Users")
    users = [user["Name"] for user in res]
    global JELLYFIN_USERS
    JELLYFIN_USERS = users
    log.info("Fetched jellyfin users!")


async def clean_known_users():
    log.info("Cleaning deleted users...")
    guild = (await bot.fetch_channel(VERIFY_CHANNEL)).guild
    for role in await guild.fetch_roles():
        if role.id == VERIFIED_ROLE:
            verified = role
            break
    assert verified
    members = await guild.fetch_members(limit=150).flatten()
    remove = []

    for jellyfin_user, discord_user in KNOWN_USERS.items():

        # Check user removed from jellyfin
        if jellyfin_user not in JELLYFIN_USERS:
            log.info(f"Cleaning removed jellyfin user {jellyfin_user!r}...")
            for member in members:
                if member.id == discord_user:
                    try:
                        await member.remove_roles(verified)
                    except Exception:
                        log.warn(f"Can't unverify discord user {discord_user}, removing from known users regardless...")
                    break
            else:  # No break
                log.info(f"Discord user {discord_user} left the server...")
            remove.append(jellyfin_user)
            continue

        # Check user removed from discord
        for member in members:
            if member.id == discord_user:
                if verified not in member.roles:
                    log.info(f"Cleaning unverified discord user {discord_user}...")
                    remove.append(jellyfin_user)
                break
        else:  # No break
            log.info(f"Cleaning removed discord user {discord_user}...")
            remove.append(jellyfin_user)

    if remove:
        for user in remove:
            del KNOWN_USERS[user]
        save_known_users()
        log.info("Cleaned deleted users!")


async def get_latest_items(category: str, limit: int) -> dict[str]:
    res: dict[str] = await jellyfin_api("GET", f"/Users/{JELLYFIN_USERID}/Items", params={
        "limit": limit,
        "recursive": "true",
        "sortBy": "DateCreated",
        "sortOrder": "Descending",
        "includeItemTypes": "Movie,Series",
        "parentId": category,
    })
    return res


@tasks.loop(minutes=5.0)
async def housekeeping():
    if housekeeping.first_run:
        housekeeping.first_run = False
    else:
        await fetch_jellyfin_users()

    await clean_known_users()

    log.info("Updating channels and presence...")
    await bot.change_presence(
        activity=nextcord.Activity(
            type=nextcord.ActivityType.watching,
            name=(await get_latest_items(MOVIES_CATEGORY, 1))["Items"][0]["Name"]
        ),
        status=nextcord.Status.do_not_disturb,
    )

    channel = bot.get_channel(DISK_CHANNEL) or await bot.fetch_channel(DISK_CHANNEL)
    d = shutil.disk_usage(LIBRARY_PATH)
    t = 1_000_000_000_000
    await channel.edit(name=f"Data: {d.used / t:.1f}TB / {d.total / t:.0f}TB | ~{d.used / d.total:.0%}")

    for channel_id, category, name in (
        (MOVIES_CHANNEL, MOVIES_CATEGORY, "Movies",),
        (ANIME_CHANNEL, ANIME_CATEGORY, "Anime",),
        (TV_CHANNEL, TV_CATEGORY, "Shows",),
    ):
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        items = await get_latest_items(category, 0)
        await channel.edit(name=f"{name}: {items['TotalRecordCount']}")
    log.info("Updated channels and presence!")


@bot.event
async def on_ready():
    log.info(f"Logged in as '{bot.user}'!")
    housekeeping.start()


@bot.event
async def on_message(message: nextcord.Message):
    await bot.wait_until_ready()
    if message.channel.id == VERIFY_CHANNEL:
        user = message.content
        await message.delete()
        await fetch_jellyfin_users()
        if user in JELLYFIN_USERS:
            if user in KNOWN_USERS:
                log.warn(f"User '{message.author}' tried verifying as existing user {user!r}!")
                await message.author.send("That user is already verified!")
            else:
                log.info(f"Adding user '{message.author}' as {user!r}...")
                verified = message.guild.get_role(VERIFIED_ROLE)
                await message.author.add_roles(verified)
                KNOWN_USERS[user] = message.author.id
                save_known_users()
                log.info(f"Added user '{message.author}' as {user!r}!")
        else:
            log.warn(f"User '{message.author}' failed verification as {user!r}!")
            await message.author.send("That user does not exist!")


@bot.slash_command()
async def gib_ip(interaction: nextcord.Interaction):
    await interaction.defer(ephemeral=True)
    if not interaction.user.get_role(ADMIN_ROLE):
        await interaction.send("https://yourmom.zip", ephemeral=True)
        return
    async with aiohttp.ClientSession() as cs:
        async with cs.get("https://ifconfig.me/ip") as req:
            ip = await req.text()
    await interaction.send(f"`{ip}`", ephemeral=True)


if __name__ == "__main__":
    asyncio.run(fetch_jellyfin_users())
    housekeeping.first_run = True
    bot.run(DISCORD_TOKEN)
