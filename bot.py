import os, re, asyncio, sqlite3, contextlib, urllib.parse
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

# ---------------- Env ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_TOKEN")

B4B_CHANNEL_ID = int(os.getenv("B4B_CHANNEL_ID", "0"))
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
MODE = os.getenv("MODE", "one").lower()                 # one | new (this build defaults to "one")
LINKS_ONLY = os.getenv("LINKS_ONLY", "1") in ("1","true","True","yes","Y")
AUTO_ARCHIVE_MINUTES = int(os.getenv("AUTO_ARCHIVE_MINUTES", "10080"))
DB_PATH = os.getenv("DB_PATH", "auntie_emz.db")         # used to store per-user thread id
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))

# ---------------- Intents ----------------
INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.messages = True
INTENTS.message_content = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)

# ---------------- Storage (one-thread-per-creator) ----------------
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS creator_thread (
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    thread_id   INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
"""
def db_init():
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute("PRAGMA journal_mode=WAL;")
        cx.execute(CREATE_SQL)

# ---------------- Helpers ----------------
URL_RE = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

async def log(guild: Optional[discord.Guild], msg: str):
    if LOG_CHANNEL_ID and guild:
        ch = guild.get_channel(LOG_CHANNEL_ID)
        if isinstance(ch, (discord.TextChannel, discord.Thread)):
            with contextlib.suppress(Exception):
                await ch.send(msg)

def thread_name_for(author: discord.abc.User, content: str) -> str:
    domain = "post"
    m = URL_RE.search(content or "")
    if m:
        try:
            domain = urllib.parse.urlparse(m.group(1)).netloc.replace("www.", "")
        except Exception:
            pass
    base = f"{author.display_name} – {domain}"
    return (base[:97] + "…") if len(base) > 100 else base

async def get_or_fetch_user_thread(guild: discord.Guild, user_id: int) -> Optional[discord.Thread]:
    with sqlite3.connect(DB_PATH) as cx:
        row = cx.execute(
            "SELECT thread_id FROM creator_thread WHERE guild_id=? AND user_id=?",
            (guild.id, user_id)
        ).fetchone()
    if not row:
        return None
    tid = int(row[0])
    ch = guild.get_channel(tid)
    if isinstance(ch, discord.Thread):
        return ch
    try:
        fetched = await guild.fetch_channel(tid)
        if isinstance(fetched, discord.Thread):
            return fetched
    except Exception:
        return None
    return None

async def set_user_thread(guild: discord.Guild, user_id: int, thread_id: int):
    with sqlite3.connect(DB_PATH) as cx:
        cx.execute(
            "INSERT OR REPLACE INTO creator_thread (guild_id, user_id, thread_id) VALUES (?,?,?)",
            (guild.id, user_id, thread_id)
        )

# ---------------- Events ----------------
@bot.event
async def on_ready():
    db_init()
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
    except Exception as e:
        print("Slash sync error:", e)
    print(f"Logged in as {bot.user} (id={bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if not B4B_CHANNEL_ID or message.channel.id != B4B_CHANNEL_ID:
        return

    # Links-only moderation
    if LINKS_ONLY and not URL_RE.search(message.content or ""):
        with contextlib.suppress(Exception):
            await message.delete()
        with contextlib.suppress(Exception):
            await message.author.send(
                "Hi! The promo channel is **links only**. "
                "Please post your shop/link there and chat inside the thread I'll create for you."
            )
        await log(message.guild, f"Deleted non-link post by {message.author.mention} in <#{B4B_CHANNEL_ID}>.")
        return

    parent: discord.TextChannel = message.channel  # type: ignore

    # Reuse or create the user's dedicated thread
    thr = await get_or_fetch_user_thread(message.guild, message.author.id)
    if thr and thr.parent_id == parent.id and not thr.locked:
        if thr.archived:
            with contextlib.suppress(Exception):
                await thr.edit(archived=False, auto_archive_duration=AUTO_ARCHIVE_MINUTES)
    else:
        name = thread_name_for(message.author, message.content)
        thr = await parent.create_thread(
            name=name,
            auto_archive_duration=AUTO_ARCHIVE_MINUTES,
            type=discord.ChannelType.public_thread
        )
        await set_user_thread(message.guild, message.author.id, thr.id)

    # Repost content + attachments in the THREAD
    files = []
    for att in message.attachments:
        with contextlib.suppress(Exception):
            files.append(await att.to_file())

    content = message.content or "(no text content)"
    with contextlib.suppress(Exception):
        await thr.send(f"{message.author.mention}\n{content}", files=files or None)
        await thr.send("↖️ Keep all updates and chat **in this thread**. Parent channel stays link-only.")

    # ✅ NEW: drop a tidy embed in the PARENT so people see who posted
    try:
        # grab first link from message for the embed
        m = URL_RE.search(message.content or "")
        link_txt = m.group(1) if m else "Routed to thread →"
        emb = discord.Embed(
            title=f"{message.author.display_name}",
            description=f"[Open their thread]({thr.jump_url})\n{link_txt}",
            colour=discord.Colour.magenta()
        )
        if message.author.display_avatar:
            emb.set_thumbnail(url=message.author.display_avatar.url)
        await parent.send(embed=emb)
    except Exception:
        pass

    # delete original message to keep parent clean
    with contextlib.suppress(Exception):
        await message.delete()

    await log(message.guild, f"Routed post by {message.author.mention} to thread {thr.mention}")


# ---------------- Commands ----------------
@bot.tree.command(name="b4b_status", description="Show configuration")
async def b4b_status(inter: discord.Interaction):
    ch = inter.guild.get_channel(B4B_CHANNEL_ID) if inter.guild else None
    emb = (discord.Embed(title="Auntie Emz – Status", colour=discord.Colour.magenta())
           .add_field(name="Parent Channel", value=ch.mention if ch else f"ID {B4B_CHANNEL_ID}", inline=False)
           .add_field(name="Mode", value=MODE, inline=True)
           .add_field(name="Links Only", value=str(LINKS_ONLY), inline=True)
           .add_field(name="Auto-archive (min)", value=str(AUTO_ARCHIVE_MINUTES), inline=True)
           .add_field(name="DB Path", value=DB_PATH, inline=False))
    await inter.response.send_message(embed=emb, ephemeral=True)

@bot.tree.command(name="b4b_find_thread", description="Find (or create) the user's dedicated thread")
@app_commands.describe(user="User to locate")
async def b4b_find_thread(inter: discord.Interaction, user: Optional[discord.Member] = None):
    member = user or inter.user  # type: ignore
    parent = inter.guild.get_channel(B4B_CHANNEL_ID)
    if not isinstance(parent, discord.TextChannel):
        await inter.response.send_message("Parent channel not set.", ephemeral=True); return

    thr = await get_or_fetch_user_thread(inter.guild, member.id)
    if not thr:
        thr = await parent.create_thread(
            name=thread_name_for(member, member.display_name),
            auto_archive_duration=AUTO_ARCHIVE_MINUTES,
            type=discord.ChannelType.public_thread
        )
        await set_user_thread(inter.guild, member.id, thr.id)
    await inter.response.send_message(f"Thread for {member.mention}: {thr.mention}", ephemeral=True)

@bot.tree.command(name="b4b_help", description="Show quick help")
async def b4b_help(inter: discord.Interaction):
    await inter.response.send_message(
        "**Auntie Emz – B4B Helper**\n\n"
        "Post in the parent channel and I’ll move it into your thread.\n"
        "Admins can configure using `/b4b_*` commands.",
        ephemeral=True
    )

async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
