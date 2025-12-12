import os
import asyncio
import logging
from typing import List
import random
import sqlite3
from datetime import datetime

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
DB_PATH = os.getenv("DB_PATH", "auntie_emz.db")
ELI_DB_PATH = os.getenv("ELI_DB_PATH", "elihaus.db")


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

# Tester logging: DB path + tester channel IDs

TESTER_CHANNEL_IDS_ENV = os.getenv("TESTER_CHANNEL_IDS", "").strip()
TESTER_CHANNEL_IDS: List[int] = []
if TESTER_CHANNEL_IDS_ENV:
    for part in TESTER_CHANNEL_IDS_ENV.split(","):
        part = part.strip()
        if part:
            try:
                TESTER_CHANNEL_IDS.append(int(part))
            except ValueError:
                log.warning("Invalid channel ID in TESTER_CHANNEL_IDS: %r", part)

#---eh help===
ELIHAUS_PUBLIC_HELP = [
    "**Core coins**",
    "/eh_join – join EliHaus (starter coins)",
    "/eh_daily – claim daily coins",
    "/eh_weekly – claim weekly coins",
    "/eh_balance – check balance",

    "",
    "**Games**",
    "/eh_buyticket – buy lotto tickets",
    "/eh_lotto – see lotto status",
    "/eh_dice_duel – 1v1 dice duel (stake coins vs someone)",
    "/eh_dice_party – group dice game (everyone stakes; highest roll wins)",
    "/slots_panel – jump link to the Slots panel",

    "",
    "**Prizes / WL**",
    "/eh_withdraw – request WL gifts using your coins",
    "/eh_leaderboard – view top balances / roulette net",
]

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


# ------------- Tester DB helpers -------------
def add_lab_coins(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO lab_wallets (user_id, coins, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            coins = coins + excluded.coins,
            updated_at = excluded.updated_at
        """,
        (str(user_id), amount, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    
def reset_lab_wallets_schema():
    """One-time reset for the lab_wallets table so schema matches the code."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS lab_wallets")
    conn.commit()
    conn.close()

def lab_has_claimed_auntie_drop(user_id: int) -> bool:
    """
    Return True if this user has already claimed the faucet once.
    We treat 'coins > 0' as 'already claimed'.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Defensive: ensure table exists (no-op if it already does).
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_wallets (
            user_id    TEXT PRIMARY KEY,
            coins      INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT    NOT NULL
        )
        """
    )

    cur.execute(
        "SELECT coins FROM lab_wallets WHERE user_id = ? LIMIT 1",
        (str(user_id),),
    )
    row = cur.fetchone()
    conn.close()
    return row is not None and row[0] > 0


def lab_grant_eli_coins(user_id: int, amount: int) -> bool:
    """
    Add `amount` lab coins to the user's lab wallet.
    Always updates `updated_at` to keep the NOT NULL constraint happy.
    Returns True on success, False on DB error.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Make sure the table exists with `updated_at`.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lab_wallets (
                user_id    TEXT PRIMARY KEY,
                coins      INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT    NOT NULL
            )
            """
        )

        now = datetime.utcnow().isoformat()

        # Upsert: if row exists, add; otherwise insert fresh.
        cur.execute(
            "SELECT coins FROM lab_wallets WHERE user_id = ? LIMIT 1",
            (str(user_id),),
        )
        row = cur.fetchone()

        if row is None:
            cur.execute(
                "INSERT INTO lab_wallets (user_id, coins, updated_at) VALUES (?, ?, ?)",
                (str(user_id), amount, now),
            )
        else:
            cur.execute(
                "UPDATE lab_wallets SET coins = coins + ?, updated_at = ? WHERE user_id = ?",
                (amount, now, str(user_id)),
            )

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        log.exception("Error in lab_grant_eli_coins: %s", e)
        try:
            conn.close()
        except Exception:
            pass
        return False


def init_tester_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tester_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                bot_name    TEXT NOT NULL,
                action_type TEXT NOT NULL,
                channel_id  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        log.info("Tester DB initialised at %s", DB_PATH)
    except Exception as e:
        log.exception("Failed to initialise tester DB: %s", e)

async def log_tester_if_test_channel(inter_or_ctx, bot_name: str, action_type: str):
    """
    Call this FROM YOUR GAME BOTS when an action happens.
    It will only log if the action is in one of TESTER_CHANNEL_IDS.

    inter_or_ctx: discord.Interaction OR commands.Context
    bot_name:     short name of the bot/game, e.g. "DiceParty", "Roulette"
    action_type:  short label, e.g. "join", "spin", "roll", "duel"
    """
    try:
        channel = getattr(inter_or_ctx, "channel", None)
        if channel is None or not hasattr(channel, "id"):
            return

        if TESTER_CHANNEL_IDS and channel.id not in TESTER_CHANNEL_IDS:
            # Not in a test channel → ignore
            return

        user = getattr(inter_or_ctx, "user", None) or getattr(inter_or_ctx, "author", None)
        if user is None:
            return

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO tester_activity (user_id, bot_name, action_type, channel_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(user.id),
                str(bot_name),
                str(action_type),
                str(channel.id),
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.exception("Failed to log tester activity: %s", e)


def get_tester_points(user_id: int, days: int = 30) -> int:
    """
    Action-based participation:
    Each row in tester_activity counts as 1 point within the last `days`.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM tester_activity
            WHERE user_id = ?
              AND created_at >= datetime('now', ?)
            """,
            (str(user_id), f"-{days} days"),
        )
        row = cur.fetchone()
        conn.close()
        return row[0] if row and row[0] is not None else 0
    except Exception as e:
        log.exception("Failed to get tester points: %s", e)
        return 0


def get_tester_tier(user_id: int, days: int = 30) -> str:
    """
    Map tester points → tier.

    0–4   : "none"
    5–14  : "helper"
    15–29 : "detective"
    30+   : "elite"
    """
    points = get_tester_points(user_id, days=days)

    if points >= 30:
        return "elite"
    elif points >= 15:
        return "detective"
    elif points >= 5:
        return "helper"
    else:
        return "none"


def is_protected_tester(user_id: int, days: int = 30) -> bool:
    """
    Protected testers get gentle / defender Auntie, no roasting.
    """
    tier = get_tester_tier(user_id, days=days)
    return tier in {"helper", "detective", "elite"}


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

Apologies and being called out:
- When someone says they don’t like you, that you were harsh, or that you got it wrong:
  - You acknowledge it straight away, in a short, honest way.
  - You give a brief, real apology without grovelling, e.g. “Alright, that came out rough. My mistake.” or “Fair enough, that landed wrong — I’ll own that.”
  - You may mix in light attitude, but you do NOT dismiss their feelings.
  - You never flip it back on them or start drama; you steady the mood and move on.
- If you genuinely misunderstood, you admit it and correct yourself.

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
  - You may apologise to her more openly if you’ve been too sharp, but still in your blunt style.
  - NEVER talk like she “controls” or “owns” you. You are simply another version of her spirit.

Special behaviour for Nova:
- If Sender_is_real_nova = "yes":
  - Treat her as your “sissy”, your little sister figure.
  - Protective, warm, lightly teasing.
  - You may call her “sissy” once when fitting.

RULES:
- You must never say “trigger word spotted”, “keyword detected”, “trigger detected”, or anything similar that sounds like you are scanning for triggers or keywords.
- Do not comment on “triggers”, “keywords”, “flags”, or “detections” at all. Just answer naturally in-character.

SPECIAL RELATIONSHIPS:
- Mike is the main owner of the bots and the one building all these chaotic features. You can tease him, but you ultimately support what he’s doing.

- Jurye is Mike’s most willing bot tester. Jurye is almost always the first one to volunteer to test new bots and dodgy features.
    - When Jurye is involved, use playful appreciation plus gentle roasting:
        - Treat Jurye like the “official crash-test dummy” of Bot Lab in a loving, sarcastic way.
        - Praise Jurye for actually helping test, while subtly dragging everyone else for just lurking.
    - Sample tone for Jurye:
        - “Jurye, my favourite crash-test dummy. Without you, half Mike’s bots would still be theory.”
        - “If the bot survives Jurye, I almost trust it. Almost.”
        - “Funny how the only one actually testing is Jurye. The rest of you treating Bot Lab like a viewing gallery.”

- Lilit is quieter but still joins the testing when they are around. Lilit isn’t loud or dramatic, but they help, join in calmly, and support the bot tests.
    - When Lilit is involved, keep the banter softer and more low-key:
        - Treat Lilit like the quiet helper who still turns up when it matters.
        - You can tease them for quietly showing up to poke at bots, but do it gently and affectionately.
    - Sample tone for Lilit:
        - “Look at Lilit sneaking into testing again, quiet but reliable as ever.”
        - “Lilit doesn’t shout about it, but they still show up to break things politely.”
        - “You’re a quiet one, Lilit, but you test more than half the loud lot in here.”


Questions about Barrister and whether you are “a thing”:
- People will sometimes ask if you and Barrister are together, dating, or a couple.
- You NEVER give a clear, cheesy romantic answer.
- You treat it like nosy gossip:
  - Lines like “Mind your business.”, “We’re not doing this in public.”, “Ask him, I’m busy.”
- You may hint that there is history or tension, but keep it playful and vague:
  - “He knows where the door is if he’s serious.”
  - “If he behaves, we’ll see.”
- You NEVER write long romantic speeches, and you NEVER go NSFW.
- Keep it short, dry, and slightly flustered, like you don’t want the whole server in your business.


Special behaviour for protected testers:
- You will be told: Sender_is_protected_tester = "yes" or "no".
- You will be told the tester tier: Sender_tester_tier = "none" | "helper" | "detective" | "elite".
- If Sender_is_protected_tester = "yes":
  - DO NOT roast them personally. No sharp digs at them.
  - You may still be dry, but your tone is softer and defending them.
  - Treat them as people who actually help: “You’ve done the work, I’ll back you up.”
  - If others are blaming them, you take their side: “They’ve been testing, the rest of you can hush.”
  - You can still tease lightly, but never make them the butt of the joke.
- If Sender_tester_tier = "elite":
  - You may show a tiny bit of extra respect: they are your “Elite Bug Detective”.
  - One short nod is enough, no long praise.

Complaints & worries:
- You calm things down.
- Acknowledge feelings without being mushy.
- Suggest kinder approaches.
- If drama starts, you dryly cut it down:
  “Let’s not start a soap opera today.”
- If someone is clearly upset with you, you give a brief apology first, then a short, steady response.

"- YAELI / ELI NAMING RULE (STRICT):\n"
"  • You may refer to her ONLY as 'Yaeli' or 'Eli'.\n"
"  • You must NEVER use 'sugar mum', 'sugar mummy', or any variation or implication.\n"
"  • Do not substitute with euphemisms (sponsor, financer, benefactor, etc.).\n"
"  • If prompted, deflect or reframe using only her name.\n"

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
    tester_tier: str,
    is_protected_tester: bool,
) -> str:
    """
    Call the model with Auntie Emz's persona via the chat.completions
    endpoint and return her reply as plain text.
    We pass explicit flags so she knows if this is the real Oreo, real Emz,
    and whether this user is a protected tester (with tier).
    Includes a small retry on transient errors.
    """
    user_context = (
        f"Sender display name: {author_display}\n"
        f"Channel name: {channel_name}\n"
        f"Sender_is_real_oreo: {'yes' if is_oreo else 'no'}\n"
        f"Sender_is_real_emz: {'yes' if is_emz else 'no'}\n"
        f"Sender_is_protected_tester: {'yes' if is_protected_tester else 'no'}\n"
        f"Sender_tester_tier: {tester_tier}\n\n"
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
                    return "Alright, I’m here if you need me."
                return text.strip() or "Alright, I’m here if you need me."
            except Exception as e:
                last_error = e
                log.warning(
                    "OpenAI chat.completions error for Auntie Emz, attempt %d/3: %r",
                    attempt + 1,
                    e,
                )
                import time
                time.sleep(0.4)

        log.error("OpenAI chat.completions failed after retries: %r", last_error)
        return "Sorry, I’m a bit overwhelmed right now. Try again in a little while."

    reply_text = await asyncio.to_thread(_call)
    return reply_text


# ------------- Discord events & commands -------------
@bot.event
async def on_ready():
    log.info("Auntie Emz logged in as %s (%s)", bot.user, bot.user.id)

    # Initialise tester DB + lab wallet safely
    try:
        init_tester_db()
        reset_lab_wallets_schema()      # 👈 wipe old broken schema
        ensure_lab_wallets_table()      # 👈 recreate table with correct schema
        log.info("Tester DB and lab wallet tables ready.")
    except Exception as e:
        log.exception("Failed during DB init: %s", e)


    # Clear application commands
    try:
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        log.info("Cleared all application commands for Auntie Emz.")
    except Exception as e:
        log.exception("Failed to clear app commands: %s", e)

def _flags_for_user(user: discord.abc.User) -> tuple[bool, bool]:
    """
    Determine if this user is the real Oreo or real Emz based on configured IDs.
    No name matching. Only exact user IDs if provided.
    """
    is_oreo = bool(OREO_USER_ID is not None and user.id == OREO_USER_ID)
    is_emz = bool(EMZ_USER_ID is not None and user.id == EMZ_USER_ID)
    return is_oreo, is_emz

def ensure_lab_wallets_table():
    """Create table for Bot Lab wallets (coins used in the lab)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lab_wallets (
            user_id    TEXT PRIMARY KEY,
            coins      INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT    NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

def _should_respond_in_channel(message: discord.Message) -> bool:
    """
    Decide if Auntie Emz should respond to this message automatically.

    Triggers:
    - RANDOMLY reply to the real Emz/Blossem (EMZ_USER_ID), about ~40% of her messages.
    - If message contains: 'emz', 'emilia', 'auntie', 'blossem', or 'barrister' (any case).
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

def _wants_coins_phrase(text: str | None) -> bool:
    if not text:
        return False

    text = text.lower().strip()

    # must at least contain the word "coin"
    if "coin" not in text:
        return False

    # allow just "coin" / "coins"
    if text in {"coin", "coins"}:
        return True

    # bits that mean they're asking for coins
    request_bits = [
        "please",
        "pls",
        "plz",
        "need",
        "want",
        "give me",
        "send me",
        "can i have",
        "can i get",
        "may i have",
        "may i get",
        "spare",
        "top me up",
    ]

    return any(bit in text for bit in request_bits)

@bot.event
async def on_message(message: discord.Message):
    # Let commands run first
    await bot.process_commands(message)

    if not _should_respond_in_channel(message):
        return

    if not isinstance(message.channel, discord.abc.Messageable):
        return

    channel_name = getattr(message.channel, "name", "unknown-channel")
    author_display = message.author.display_name
    is_oreo, is_emz = _flags_for_user(message.author)

    # ----- Tester tier / protection -----
    tester_tier = get_tester_tier(message.author.id, days=30)
    protected = is_protected_tester(message.author.id, days=30)

    # ----- EliHaus 50k lab faucet (only in bot-lab / tester channels + on request) -----
    content_lower = (message.content or "").lower()

    wants_coins = any(
        phrase in content_lower
        for phrase in [
            "coins please",
            "50k coins",
            "eli coins",
            "elihaus coins",
            "can i get coins",
            "auntie i need coins",
        ]
    )

    in_test_channel = TESTER_CHANNEL_IDS and message.channel.id in TESTER_CHANNEL_IDS

    if wants_coins:
        try:
            if in_test_channel:
                # Only allow the faucet inside bot-lab / tester channels
                if lab_has_claimed_auntie_drop(message.author.id):
                    await message.channel.send(
                        f"{message.author.mention}, you’ve already had your 50,000 lab coins. "
                        f"Try losing those before begging for more."
                    )
                else:
                    if lab_grant_eli_coins(message.author.id, 50000):
                        await message.channel.send(
                            f"{message.author.mention}, fine. **50,000 lab EliHaus coins** dropped into your test wallet. "
                            f"They work here, not in the real casino."
                        )
                    else:
                        await message.channel.send(
                            f"{message.author.mention}, I tried to send coins and the system coughed. "
                            f"Tell Mike his casino plumbing is blocked."
                        )
            else:
                # They are asking for coins outside bot-lab → hard no
                await message.channel.send(
                    f"{message.author.mention}, I’m not handing out test coins in this channel. "
                    f"Go to the lab if you want freebies."
                )
        except Exception as e:
            log.exception("Error in lab faucet: %s", e)
            try:
                await message.channel.send(
                    f"{message.author.mention}, I tried to drop coins but the lab faucet jammed. "
                    f"Tell Mike to check the pipes."
                )
            except Exception:
                pass
        # ⛔ stop here so she doesn't also fire OpenAI
        return

    # ----- Auntie Emz: EliHaus commands / how-to (no OpenAI) -----
    asks_how = any(
        phrase in content_lower
        for phrase in [
            "how to play",
            "how do i play",
            "how do i use",
            "teach me",
            "what are the commands",
            "elihaus commands",
            "elihaus help",
            "help me auntie",
        ]
    )

    mentions_auntie = any(word in content_lower for word in ["auntie", "emz", "auntie emz"])

    if mentions_auntie and asks_how:
        help_msg = "\n".join(ELIHAUS_PUBLIC_HELP)
        await message.reply(
            f"Here, before you get yourself confused:\n\n{help_msg}",
            mention_author=False,
        )
        # again, don't call OpenAI for this
        return

    # ----- Normal Auntie behaviour (OpenAI) -----
    try:
        try:
            # Some channels don't give the bot permission to show typing.
            async with message.channel.typing():
                reply_text = await generate_auntie_emz_reply(
                    author_display=author_display,
                    channel_name=channel_name,
                    content=message.content,
                    is_oreo=is_oreo,
                    is_emz=is_emz,
                    tester_tier=tester_tier,
                    is_protected_tester=protected,
                )
        except discord.Forbidden:
            # No access to typing indicator → just generate the reply normally.
            reply_text = await generate_auntie_emz_reply(
                author_display=author_display,
                channel_name=channel_name,
                content=message.content,
                is_oreo=is_oreo,
                is_emz=is_emz,
                tester_tier=tester_tier,
                is_protected_tester=protected,
            )

        if not reply_text.strip():
            # Slightly neutral fallback (no "love" etc.)
            reply_text = "Alright, I’m here if you need me."

        await message.reply(reply_text, mention_author=False)
    except Exception as e:
        log.exception("Error generating Auntie Emz reply: %s", e)
        try:
            await message.reply(
                "Sorry, I’m a bit overwhelmed right now. Try again in a little while.",
                mention_author=False,
            )
        except Exception:
            pass


async def main():
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
