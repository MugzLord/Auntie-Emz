import os, re, asyncio, contextlib, urllib.parse
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

# ---------------- Env ----------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Set DISCORD_TOKEN")

B4B_CHANNEL_ID = int(os.getenv("B4B_CHANNEL_ID", "0"))   # Parent channel ID
GUILD_ID = int(os.getenv("GUILD_ID", "0"))               # Optional: faster slash sync if set
MODE = os.getenv("MODE", "new").lower()                  # new | one  (this build defaults to "new")
LINKS_ONLY = os.getenv("LINKS_ONLY", "1") in ("1","true","True","yes","Y")
AUTO_ARCHIVE_MINUTES = int(os.getenv("AUTO_ARCHIVE_MINUTES", "10080"))  # 1 week
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))   # Optional log channel

# ---------------- Intents ----------------
INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.messages = True
INTENTS.message_content = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)

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

# ---------------- Events ----------------
@bot.event
async def on_ready():
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
    name = thread_name_for(message.author, message.content)
    thread = await parent.create_thread(
        name=name,
        auto_archive_duration=AUTO_ARCHIVE_MINUTES,
        type=discord.ChannelType.public_thread
    )

    # Repost content + attachments into thread
    files = []
    for att in message.attachments:
        with contextlib.suppress(Exception):
            files.append(await att.to_file())

    content = message.content or "(no text content)"
    with contextlib.suppress(Exception):
        await thread.send(f"{message.author.mention}\n{content}", files=files or None)
        await thread.send("↖️ Keep all updates and chat **in this thread**. Parent channel stays link-only.")

    with contextlib.suppress(Exception):
        await message.delete()

    await log(message.guild, f"Created thread for {message.author.mention}: {thread.mention}")

# ---------------- Slash Commands ----------------
@bot.tree.command(name="b4b_status", description="Show configuration")
async def b4b_status(inter: discord.Interaction):
    ch = inter.guild.get_channel(B4B_CHANNEL_ID) if inter.guild else None
    emb = (discord.Embed(title="Auntie Emz – Status", colour=discord.Colour.magenta())
           .add_field(name="Parent Channel", value=ch.mention if ch else f"ID {B4B_CHANNEL_ID}", inline=False)
           .add_field(name="Mode", value=MODE, inline=True)
           .add_field(name="Links Only", value=str(LINKS_ONLY), inline=True)
           .add_field(name="Auto-archive (min)", value=str(AUTO_ARCHIVE_MINUTES), inline=True))
    await inter.response.send_message(embed=emb, ephemeral=True)

@bot.tree.command(name="b4b_help", description="Show quick help")
async def b4b_help(inter: discord.Interaction):
    await inter.response.send_message(
        "**Auntie Emz – B4B Helper**\n\n"
        "Post in the parent channel and I’ll move it into a thread.\n"
        "Admins can configure using `/b4b_*` commands.",
        ephemeral=True
    )

async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
