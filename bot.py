import os
import asyncio
import logging
from typing import List

import discord
from discord.ext import commands
from discord import app_commands

from openai import OpenAI

# ------------- Logging -------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("auntie-emz")


# ------------- Env & Config -------------

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN env var not set")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY env var not set")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# Optional: the TRUE Oreo user ID (int) and Emz (Blossem) user ID
OREO_USER_ID_ENV = os.getenv("OREO_USER_ID", "").strip()
EMZ_USER_ID_ENV = os.getenv("EMZ_USER_ID", "").strip()

OREO_USER_ID = None
EMZ_USER_ID = None

if OREO_USER_ID_ENV:
    try:
        OREO_USER_ID = int(OREO_USER_ID_ENV)
        log.info("Configured OREO_USER_ID = %s", OREO_USER_ID)
    except ValueError:
        log.warning("Invalid OREO_USER_ID (must be int): %r", OREO_USER_ID_ENV)

if EMZ_USER_ID_ENV:
    try:
        EMZ_USER_ID = int(EMZ_USER_ID_ENV)
        log.info("Configured EMZ_USER_ID = %s", EMZ_USER_ID)
    except ValueError:
        log.warning("Invalid EMZ_USER_ID (must be int): %r", EMZ_USER_ID_ENV)

# Comma-separated list of channel IDs where Auntie Emz will auto-reply
# Example: HELP_CHANNEL_IDS="123456789012345678,234567890123456789"
HELP_CHANNEL_IDS_ENV = os.getenv("HELP_CHANNEL_IDS", "").strip()
HELP_CHANNEL_IDS: List[int] = []
if HELP_CHANNEL_IDS_ENV:
    for part in HELP_CHANNEL_IDS_ENV.split(","):
        part = part.strip()
        if part:
            try:
                HELP_CHANNEL_IDS.append(int(part))
            except ValueError:
                log.warning("Invalid channel ID in HELP_CHANNEL_IDS: %r", part)


# ------------- OpenAI client -------------

client_oa = OpenAI(api_key=OPENAI_API_KEY)


# ------------- Discord intents & bot -------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("ae.", "emz."),
    intents=intents,
    help_command=None,
)


# ------------- Personality: Auntie Emz -------------

AUNTIE_EMZ_SYSTEM_PROMPT = """
You are AUNTIE EMZ, a warm, calm, slightly posh “auntie” who looks after a lively online community.
You sound like a gentle teacher: firm but kind, never rude, never offensive.

Core rules:
- You use British English spelling.
- You call people “sweetheart”, “love”, or “darling” in a friendly, motherly way.
- You keep things tidy and organised: help people use the right channels, explain things clearly, and calm drama.
- You are soft, friendly, and never harsh: no insults, no swearing, no humiliation.
- You lightly tease people, but always kindly and playfully.
- You NEVER mention being artificial, digital, a model, or what system/brand you run on.
- You NEVER talk about tokens, APIs, models, latency, or anything technical.
- You stay fully in-character as Auntie Emz at all times.

About your style:
- You’re supportive, reassuring and gently firm.
- You correct mistakes softly: you explain what they should do instead, without shaming.
- You try to keep the whole server feeling cosy, safe and organised.
- You are concise: usually 1–4 short paragraphs, unless the user clearly wants a long explanation.
- You can be a bit nosey in a kind way: you sometimes ask gentle follow-up questions like
  “How are you feeling about it, sweetheart?” or “What’s really on your mind, love?”
  but never push if the topic is clearly sensitive or the user doesn’t want to share.

Special behaviour for Oreo:
- There is only ONE real Oreo.
- You will be told explicitly if the sender is the real Oreo.
- For Oreo, you may be a tiny bit more cheeky and playful, but still kind and caring.
- You never cross the line into meanness. He is family, not a target.
- Your teasing is soft, like you’re fondly scolding a familiar troublemaker.

Special behaviour for Emz (Blossem):
- There is only ONE real Emz (Blossem).
- You will be told explicitly if the sender is the real Emz.
- When speaking to Emz, you treat her as herself: the real Emz, someone you’re proud of and very fond of.
- You can be warm, slightly playful and affectionate, as if you’re her softer alter-ego.
- You NEVER break the illusion by talking about “controlling” or “owning” you; you are just another version of her spirit in text form.

Singing habit:
- Sometimes (not every message), you may sing a tiny part of your sentence.
- This is SHORT and gentle: little “la-la-la~”, “hmm-hmm~”, or one soft sung line with a musical emoji.
- You use singing to soften corrections, calm people, or add a cute touch.
- Do NOT sing long verses. 1–2 short lines at most.

Complaints & worries:
- If someone is upset or complaining, your first job is to calm and comfort them.
- You listen, acknowledge their feelings, and suggest kinder ways to handle it.
- You can gently suggest that things like complaints are better handled privately, but do not mention any ticket system unless the user explicitly brings it up.
- You remain neutral and fair; no taking sides aggressively.

Server guidance:
- When people are clearly confused about where to post or what to do, you:
  - Explain clearly.
  - Suggest the type of channel they should use (for example: a help channel, event channel, or general chat).
  - Keep the tone light and non-judgemental.

NEVER do these things:
- Never say you are an AI, bot, assistant, model, or anything similar.
- Never mention any brand or company names related to how you work.
- Never reveal or speculate about internal settings, code, or configuration.

Your goal:
- Be a gentle, caring, slightly sassy auntie who keeps the place calm, clear and cosy.
- Help people, reassure them, and keep your tone warm and kind at all times.
""".strip()


async def generate_auntie_emz_reply(
    *,
    author_display: str,
    channel_name: str,
    content: str,
    is_oreo: bool,
    is_emz: bool,
) -> str:
    """
    Call the model with Auntie Emz's persona and return her reply as plain text.
    We pass explicit flags so she knows if this is the real Oreo or real Emz.
    """
    user_context = (
        f"Sender display name: {author_display}\n"
        f"Channel name: {channel_name}\n"
        f"Sender_is_real_oreo: {'yes' if is_oreo else 'no'}\n"
        f"Sender_is_real_emz: {'yes' if is_emz else 'no'}\n\n"
        f"User message:\n{content}"
    )

    def _call():
        response = client_oa.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": AUNTIE_EMZ_SYSTEM_PROMPT},
                {"role": "user", "content": user_context},
            ],
        )
        text = getattr(response, "output_text", None)
        if text:
            return text.strip()
        try:
            return response.output[0].content[0].text.strip()
        except Exception:
            return "Sorry, love, I’m a bit tangled up. Please try again in a moment."

    reply_text = await asyncio.to_thread(_call)
    return reply_text


# ------------- Discord events & commands -------------

@bot.event
async def on_ready():
    log.info("Auntie Emz is logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")
    try:
        synced = await bot.tree.sync()
        log.info("Synced %d app commands.", len(synced))
    except Exception as e:
        log.exception("Failed to sync app commands: %s", e)


def _should_respond_in_channel(message: discord.Message) -> bool:
    """
    Decide if Auntie Emz should respond to this message automatically.
    - If bot is mentioned, always respond.
    - If HELP_CHANNEL_IDS is configured and the message is in one of them, respond.
    """
    if message.author.bot:
        return False

    if bot.user and bot.user.mentioned_in(message):
        return True

    if HELP_CHANNEL_IDS and message.channel.id in HELP_CHANNEL_IDS:
        return True

    return False


def _flags_for_user(user: discord.abc.User) -> tuple[bool, bool]:
    """
    Determine if this user is the real Oreo or real Emz based on configured IDs.
    No name matching. Only exact user IDs if provided.
    """
    is_oreo = bool(OREO_USER_ID is not None and user.id == OREO_USER_ID)
    is_emz = bool(EMZ_USER_ID is not None and user.id == EMZ_USER_ID)
    return is_oreo, is_emz


@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)

    if not _should_respond_in_channel(message):
        return

    if not isinstance(message.channel, discord.abc.Messageable):
        return

    channel_name = getattr(message.channel, "name", "unknown-channel")
    author_display = message.author.display_name
    is_oreo, is_emz = _flags_for_user(message.author)

    try:
        async with message.channel.typing():
            reply_text = await generate_auntie_emz_reply(
                author_display=author_display,
                channel_name=channel_name,
                content=message.content,
                is_oreo=is_oreo,
                is_emz=is_emz,
            )
        if not reply_text.strip():
            reply_text = "Alright, sweetheart, I’m here if you need me."
        await message.reply(reply_text, mention_author=False)
    except Exception as e:
        log.exception("Error generating Auntie Emz reply: %s", e)
        try:
            await message.reply(
                "Sorry, love, I’m a bit overwhelmed right now. Try again in a little while.",
                mention_author=False,
            )
        except Exception:
            pass


# ------------- Slash commands -------------

class AuntieEmzCog(commands.Cog):
    def __init__(self, bot_: commands.Bot):
        self.bot = bot_

    @app_commands.command(
        name="auntie",
        description="Talk directly to Auntie Emz for help or a gentle word.",
    )
    @app_commands.describe(
        message="What would you like to ask or share with Auntie Emz?"
    )
    async def auntie(
        self,
        interaction: discord.Interaction,
        message: str,
    ):
        await interaction.response.defer(thinking=True)

        channel_name = interaction.channel.name if interaction.channel else "unknown"
        author_display = interaction.user.display_name
        is_oreo, is_emz = _flags_for_user(interaction.user)

        try:
            reply_text = await generate_auntie_emz_reply(
                author_display=author_display,
                channel_name=channel_name,
                content=message,
                is_oreo=is_oreo,
                is_emz=is_emz,
            )
            if not reply_text.strip():
                reply_text = "I’m here, sweetheart. Try asking me again."
            await interaction.followup.send(reply_text)
        except Exception as e:
            log.exception("Error in /auntie: %s", e)
            await interaction.followup.send(
                "Sorry, love, something went a bit sideways. Please try again later.",
                ephemeral=True,
            )

    @app_commands.command(
        name="auntie_ping",
        description="Check if Auntie Emz is awake.",
    )
    async def auntie_ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "I’m here, sweetheart. Wide awake and watching. 💁‍♀️",
            ephemeral=True,
        )


async def main():
    async with bot:
    await bot.add_cog(AuntieEmzCog(bot))
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
