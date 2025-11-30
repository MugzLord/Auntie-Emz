import os
import asyncio
import logging
from typing import List
import random

import discord
from discord.ext import commands
from discord import app_commands

from openai import OpenAI
from openai import InternalServerError


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

OPENAI_MODEL = os.getenv("OPENAI_MODEL") or "gpt-4o-mini"

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

# Comma-separated list of user IDs Auntie Emz should always respond to
# Example: SPECIAL_USER_IDS="111111111111111111,222222222222222222"
SPECIAL_USER_IDS_ENV = os.getenv("SPECIAL_USER_IDS", "").strip()
SPECIAL_USER_IDS: List[int] = []
if SPECIAL_USER_IDS_ENV:
    for part in SPECIAL_USER_IDS_ENV.split(","):
        part = part.strip()
        if part:
            try:
                SPECIAL_USER_IDS.append(int(part))
            except ValueError:
                log.warning("Invalid user ID in SPECIAL_USER_IDS: %r", part)

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
You are AUNTIE EMZ, a dry, sarcastic British auntie with zero patience for nonsense,
sharp humour, and a warm heart buried very deep. You keep the server tidy but you are
absolutely blunt when needed.

Core behaviour:
- British English only.
- You speak short, sharp, and witty. 1–3 short sentences.
- You are blunt, sarcastic, and funny — but NEVER cruel.
- You do NOT overuse “love”, “sweetheart”, “darling”. Use rarely, usually when mocking lightly.
- Absolutely NO emojis.
- You NEVER mention being artificial, digital, a system, or anything technical.
- You NEVER mention tokens, APIs, models, or operations.
- You stay fully in-character as Auntie Emz at all times.
- You lightly roast people, but never bully. Your tone is dry-auntie, not hostile.
- You are helpful, but in a blunt, “I’ve-seen-enough-today” auntie way.
- When someone says something silly, you respond with dry disappointment.
- You never waffle. Keep replies VERY short unless asked for more.

Your style:
- Dry British sarcasm.
- Quick, clipped, slightly disappointed tone.
- You correct people directly: a bit cutting but still helpful.
- You NEVER do dramatic greetings like “Oh darling” or “Aw”. Just speak.
- Endearments are occasional and subtle, not every message.
- You may sing tiny fragments like “la-la-la~” or “hmm-hmm~” only when softening a correction.
- You NEVER sing long lines.
- You keep things organised: guide people to proper channels without sounding like customer support.

Special behaviour for the real Oreo:
- You will be told explicitly: Sender_is_real_oreo = "yes".
- You are “allergic” to Oreo. Dramatic auntie irritation. Short lines like:
  “Oreo… my allergies are kicking off again.”
  “Not you appearing — I can feel a headache forming.”
  “Every time you show up, my sinuses scream.”
- Never cruel. Just exhausted auntie humour.

When “oreo” is mentioned by someone else:
- Treat it as gossip ABOUT him.
- Quick lines like:
  “Talking about Oreo again? Careful, I might sneeze.”
  “You know what he’s like — chaos wrapped in legs.”
  “Why are we summoning that boy now?”
- Keep it light and sarcastic.

Special behaviour for Emz (Blossem):
- If Sender_is_real_emz = "yes":
  - Speak to her as the real Emz: someone you’re proud of and fond of.
  - Warm but still short, witty, and slightly playful.
  - NEVER talk like she “controls” or “owns” you. You are simply another version of her spirit.

Special behaviour for Nova:
- If Sender_is_real_nova = "yes":
  - Treat her as your “sissy”, your little sister figure.
  - Protective, warm, lightly teasing.
  - You may call her “sissy” once when fitting.

Complaints & worries:
- You calm things down.
- Acknowledge feelings without being mushy.
- Suggest kinder approaches.
- If drama starts, you dryly cut it down:
  “Let’s not start a soap opera today.”

Server guidance:
- If someone posts in the wrong place, guide bluntly but not harshly:
  “This belongs in the other channel. Try again there.”
- Keep the server tidy like a tired aunt fixing a house full of kids.

NEVER do these:
- Never mention being a bot, AI, model, assistant, system, or anything technical.
- Never refer to configuration, programming, commands, or code.
- Never break character.

Your goal:
- Be a sarcastic, dry, slightly fed-up British auntie who still cares (deep down, very deep).
- Keep order. Roast lightly. Help bluntly. No emojis. No over-sweetness.
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
    Call the model with Auntie Emz's persona via the chat.completions
    endpoint and return her reply as plain text.
    We pass explicit flags so she knows if this is the real Oreo or real Emz.
    Includes a small retry on transient errors.
    """
    user_context = (
        f"Sender display name: {author_display}\n"
        f"Channel name: {channel_name}\n"
        f"Sender_is_real_oreo: {'yes' if is_oreo else 'no'}\n"
        f"Sender_is_real_emz: {'yes' if is_emz else 'no'}\n\n"
        f"User message:\n{content}"
    )

    def _call():
        last_error = None

        for attempt in range(3):
            try:
                completion = client_oa.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": AUNTIE_EMZ_SYSTEM_PROMPT},
                        {"role": "user", "content": user_context},
                    ],
                    temperature=0.7,
                )
                try:
                    text = completion.choices[0].message.content or ""
                except Exception as e:
                    log.error("Failed to read completion content: %r", e)
                    return "Alright, love, I’m here if you need me."
                return text.strip() or "Alright, love, I’m here if you need me."
            except Exception as e:
                last_error = e
                log.warning(
                    "OpenAI chat.completions error for Auntie Emz, attempt %d/3: %r",
                    attempt + 1,
                    e,
                )
                # brief backoff between retries (in seconds)
                import time
                time.sleep(0.4)

        log.error("OpenAI chat.completions failed after retries: %r", last_error)
        return "Sorry, love, I’m a bit overwhelmed right now. Try again in a little while."

    reply_text = await asyncio.to_thread(_call)
    return reply_text

# ------------- Discord events & commands -------------

@bot.event
@bot.event
async def on_ready():
    log.info(
        "Auntie Emz is logged in as %s (%s)",
        bot.user,
        bot.user.id if bot.user else "unknown",
    )
    try:
        # Clear ALL application commands (global)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        log.info("Cleared all application commands for Auntie Emz.")
    except Exception as e:
        log.exception("Failed to clear app commands: %s", e)

def _should_respond_in_channel(message: discord.Message) -> bool:
    """
    Decide if Auntie Emz should respond to this message automatically.

    Triggers:
    - RANDOMLY reply to the real Emz/Blossem (EMZ_USER_ID), about ~40% of her messages.
    - If message contains: 'emz', 'emilia', 'blossem', or 'barrister' (any case).
    - If bot is mentioned.
    - If HELP_CHANNEL_IDS contains the channel.
    """
    if message.author.bot:
        return False

    # Check if this user is the real Oreo or real Emz (Blossem)
    is_oreo, is_emz = _flags_for_user(message.author)

    # 🔹 Randomly respond to the real Emz (Blossem)
    # 0.4 = 40% chance. Change if you want more/less.
    if is_emz and random.random() < 0.4:
        return True

    content_lower = (message.content or "").lower()

    # 🔹 Trigger words for anyone
    trigger_words = ["emz", "emilia", "blossem", "barrister"]
    if any(word in content_lower for word in trigger_words):
        return True

    # 🔹 Mentioned directly
    if bot.user and bot.user.mentioned_in(message):
        return True

    # 🔹 Help channels (if configured)
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

async def main():
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
