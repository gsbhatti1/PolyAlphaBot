import os
import time
import sqlite3
import traceback

import os
if os.getenv("REQUIRE_STANDALONE_TELEGRAM", "0") != "1":
    raise SystemExit("Refusing to run standalone telegram_commands.py (409-safe). Use service integrated loop instead.")

