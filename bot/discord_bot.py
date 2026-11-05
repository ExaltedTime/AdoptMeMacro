"""The bot's old place (it moved to scripts/discord_bot.py): this runs it, so that a bot started - or started again by /update, which uses the command it was started with - from here still comes
up. Delete this file (and this folder) once the bot is started as `python scripts/discord_bot.py`."""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.argv[0] = os.path.join(ROOT, "scripts", "discord_bot.py")
runpy.run_path(sys.argv[0], run_name="__main__")
