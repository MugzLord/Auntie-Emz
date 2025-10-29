# Auntie Emz – Auto-thread B4B Bot (New thread per post)

Keeps your promo channel tidy by moving posts into **new threads** per post.

## Quick start
1) Set env vars (see `.env.example`)
2) `pip install -r requirements.txt`
3) `python bot.py`

### Commands
- `/b4b_status`
- `/b4b_help`

### Notes
- Parent channel must allow: Create Public Threads, Send in Threads, Manage Messages.
- Non-link posts in the parent can be auto-deleted if `LINKS_ONLY=1`.
