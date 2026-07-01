import os
import re
import json
import io
import asyncio
import sqlite3
import gzip
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env Datei oder in Railway Variables.")

if not GUILD_ID_RAW.isdigit():
    raise RuntimeError("GUILD_ID fehlt in der .env Datei oder ist ungültig.")

GUILD_ID = int(GUILD_ID_RAW)
BOT_TZ = ZoneInfo("Europe/Berlin")

RAILWAY_VOLUME_MOUNT_PATH = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
DEFAULT_DB_PATH = (
    Path(RAILWAY_VOLUME_MOUNT_PATH) / "vollpfosten_cr8.sqlite3"
    if RAILWAY_VOLUME_MOUNT_PATH
    else Path("vollpfosten_cr8.sqlite3")
)
DB_PATH = Path(os.getenv("DB_PATH", DEFAULT_DB_PATH))
LINEUP_TEMPLATE_DIR = Path("assets") / "lineups"
PLAYER_CARD_DIR = Path("assets") / "cards"
SERVER_NAME = "Vollpfosten CR8"

ROLE_MANAGER = "Manager"
ROLE_STAMMELF = "Stammelf"
ROLE_TESTER = "Tester"
ROLE_REGISTERED = "Registriert"
ROLE_FINISHED = "Fertig"

POSITIONS = ["TW", "IV", "RV", "LV", "ZDM", "ZM", "ZOM", "LF", "RF", "ST"]

OFF_POSITIONS = {"LF", "ZOM", "RF", "ST"}
MID_POSITIONS = {"ZM", "ZDM"}
DEF_POSITIONS = {"IV", "RV", "LV", "TW"}

PROFILE_UPDATE_SUPPRESS_SECONDS = 12
MIN_LINEUP_PLAYERS = 4
profile_update_suppressed_until: dict[int, datetime] = {}

CATEGORY_INFO = "📌 INFO"
CATEGORY_CHAT = "💬 CHAT"
CATEGORY_TEAM = "🧠 TEAM"
CATEGORY_VOICE = "🔊 VOICE"

CH_RULES = "regeln"
CH_PROFILE = "profil"
CH_MAIN_POSITIONS = "hauptpositionen"
CH_SIDE_POSITIONS = "nebenpositionen"
CH_NUMBERS = "trikotnummer"
CH_CLUB_BADGE = "vereinswappen"
CH_GENERAL = "allgemein"
CH_CLIPS = "clips"
CH_MANAGER = "manager-chat"
CH_STAMMELF = "stammelf-chat"
CH_LINEUPS = "aufstellungen"
CH_AVAILABILITY = "verfuegbarkeit"
CH_MATCH_REPORTS = "spielberichte"

VC_BENCH = "bank"
VC_STAMMELF = "kabine"
VC_MANAGER = "krisenbesprechung"
VC_OTHER_GAMES = "andere-games"

RULES_MARKER = "[VCR8_RULES_PANEL]"
MAIN_POSITIONS_MARKER = "[VCR8_MAIN_POSITIONS_PANEL]"
SIDE_POSITIONS_MARKER = "[VCR8_SIDE_POSITIONS_PANEL]"
NUMBERS_MARKER = "[VCR8_NUMBERS_PANEL]"

RULES_TEXT = """## Willkommen bei Vollpfosten CR8

So bist du in unter 1 Minute startklar:

**1. Regeln akzeptieren**
Drücke unten auf den Button. Danach bekommst du die Rolle **Tester**.

**2. Profil ausfüllen**
Gehe in **#profil** und wähle:
- 1-2 Hauptpositionen
- eine freie Trikotnummer
- optional Nebenpositionen

**3. Mitspielen**
Wenn alles fertig ist, bekommst du automatisch Zugriff auf die wichtigen Kanäle und Voice-Chats.

**Kurzregeln**
- Respektvoll bleiben, kein Spam, kein unnötiges Drama.
- Teamplay geht vor Ego-Play.
- Auf Manager-Ansagen hören.
- Bei Verfügbarkeitsabfragen ehrlich abstimmen.

Nebenpositionen sind freiwillig. Wenn du nur eine Position spielst oder deine zwei Positionen beide Hauptpositionen sind, musst du keine Nebenposition auswählen.
"""

POLL_NOTE = "Hinweis: Du musst **nicht** exakt zur angegebenen Startzeit da sein."

WEEKDAY_DE = {
    0: "Montag",
    1: "Dienstag",
    2: "Mittwoch",
    3: "Donnerstag",
    4: "Freitag",
    5: "Samstag",
    6: "Sonntag",
}


def main_role_name(pos: str) -> str:
    return f"Haupt-{pos}"


def side_role_name(pos: str) -> str:
    return f"Neben-{pos}"


def db():
    if DB_PATH.parent != Path("."):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS profiles (
            user_id INTEGER PRIMARY KEY,
            base_name TEXT,
            jersey TEXT,
            main_positions TEXT,
            side_positions TEXT
        )
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            weekday_text TEXT NOT NULL,
            date_text TEXT NOT NULL,
            time_text TEXT NOT NULL,
            start_at TEXT NOT NULL,
            channel_id INTEGER,
            message_id INTEGER,
            created_by INTEGER,
            auto_created INTEGER DEFAULT 0,
            yes_threshold_announced INTEGER DEFAULT 0,
            remind_60_sent INTEGER DEFAULT 0,
            remind_5_sent INTEGER DEFAULT 0,
            closed INTEGER DEFAULT 0,
            cleanup_deleted INTEGER DEFAULT 0
        )
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            response TEXT NOT NULL,
            later_time TEXT,
            voted_at TEXT NOT NULL,
            PRIMARY KEY (poll_id, user_id)
        )
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS nonvote_warnings (
            user_id INTEGER PRIMARY KEY,
            missed_count INTEGER DEFAULT 0,
            last_warned_at_count INTEGER DEFAULT 0
        )
        """
    )

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS ea_club_matches (
            match_id TEXT NOT NULL,
            match_type TEXT NOT NULL,
            channel_id INTEGER,
            message_id INTEGER,
            snapshot_json TEXT NOT NULL,
            posted_at TEXT NOT NULL,
            PRIMARY KEY (match_id, match_type)
        )
        """
    )

    con.commit()
    con.close()


def migrate_db():
    con = db()

    cols_profiles = [row["name"] for row in con.execute("PRAGMA table_info(profiles)").fetchall()]
    if "base_name" not in cols_profiles:
        con.execute("ALTER TABLE profiles ADD COLUMN base_name TEXT")
    if "jersey" not in cols_profiles:
        con.execute("ALTER TABLE profiles ADD COLUMN jersey TEXT")
    if "main_positions" not in cols_profiles:
        con.execute("ALTER TABLE profiles ADD COLUMN main_positions TEXT")
    if "side_positions" not in cols_profiles:
        con.execute("ALTER TABLE profiles ADD COLUMN side_positions TEXT")

    cols_polls = [row["name"] for row in con.execute("PRAGMA table_info(polls)").fetchall()]
    if "auto_created" not in cols_polls:
        con.execute("ALTER TABLE polls ADD COLUMN auto_created INTEGER DEFAULT 0")
    if "yes_threshold_announced" not in cols_polls:
        con.execute("ALTER TABLE polls ADD COLUMN yes_threshold_announced INTEGER DEFAULT 0")
    if "remind_60_sent" not in cols_polls:
        con.execute("ALTER TABLE polls ADD COLUMN remind_60_sent INTEGER DEFAULT 0")
    if "remind_5_sent" not in cols_polls:
        con.execute("ALTER TABLE polls ADD COLUMN remind_5_sent INTEGER DEFAULT 0")
    if "closed" not in cols_polls:
        con.execute("ALTER TABLE polls ADD COLUMN closed INTEGER DEFAULT 0")
    if "cleanup_deleted" not in cols_polls:
        con.execute("ALTER TABLE polls ADD COLUMN cleanup_deleted INTEGER DEFAULT 0")

    cols_votes = [row["name"] for row in con.execute("PRAGMA table_info(poll_votes)").fetchall()]
    if "later_time" not in cols_votes:
        try:
            con.execute("ALTER TABLE poll_votes ADD COLUMN later_time TEXT")
        except sqlite3.OperationalError:
            pass

    con.commit()
    con.close()


def get_profile(user_id: int):
    con = db()
    row = con.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    con.close()

    if row is None:
        return {
            "user_id": user_id,
            "base_name": None,
            "jersey": None,
            "main_positions": [],
            "side_positions": [],
        }

    return {
        "user_id": row["user_id"],
        "base_name": row["base_name"],
        "jersey": row["jersey"],
        "main_positions": json.loads(row["main_positions"]) if row["main_positions"] else [],
        "side_positions": json.loads(row["side_positions"]) if row["side_positions"] else [],
    }


def get_profile_by_jersey(jersey: str, exclude_user_id: int | None = None):
    con = db()
    if exclude_user_id is None:
        row = con.execute(
            "SELECT * FROM profiles WHERE jersey = ? LIMIT 1",
            (jersey,),
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM profiles WHERE jersey = ? AND user_id != ? LIMIT 1",
            (jersey, exclude_user_id),
        ).fetchone()
    con.close()
    return row


def save_profile(user_id: int, base_name=None, jersey=None, main_positions=None, side_positions=None):
    current = get_profile(user_id)

    if base_name is None:
        base_name = current["base_name"]
    if jersey is None:
        jersey = current["jersey"]
    if main_positions is None:
        main_positions = current["main_positions"]
    if side_positions is None:
        side_positions = current["side_positions"]

    con = db()
    con.execute(
        """
        INSERT INTO profiles (user_id, base_name, jersey, main_positions, side_positions)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            base_name = excluded.base_name,
            jersey = excluded.jersey,
            main_positions = excluded.main_positions,
            side_positions = excluded.side_positions
        """,
        (
            user_id,
            base_name,
            jersey,
            json.dumps(main_positions),
            json.dumps(side_positions),
        ),
    )
    con.commit()
    con.close()


def create_poll_record(kind: str, title: str, weekday_text: str, date_text: str, time_text: str, start_at: datetime, created_by: int | None, auto_created: bool):
    con = db()
    cur = con.execute(
        """
        INSERT INTO polls (kind, title, weekday_text, date_text, time_text, start_at, created_by, auto_created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (kind, title, weekday_text, date_text, time_text, start_at.isoformat(), created_by, 1 if auto_created else 0),
    )
    poll_id = cur.lastrowid
    con.commit()
    con.close()
    return poll_id


def set_poll_message_info(poll_id: int, channel_id: int, message_id: int):
    con = db()
    con.execute(
        "UPDATE polls SET channel_id = ?, message_id = ? WHERE id = ?",
        (channel_id, message_id, poll_id),
    )
    con.commit()
    con.close()


def get_poll_by_message_id(message_id: int):
    con = db()
    row = con.execute("SELECT * FROM polls WHERE message_id = ?", (message_id,)).fetchone()
    con.close()
    return row


def get_poll_by_id(poll_id: int):
    con = db()
    row = con.execute("SELECT * FROM polls WHERE id = ?", (poll_id,)).fetchone()
    con.close()
    return row


def poll_is_closed(poll_row) -> bool:
    return poll_row is None or bool(poll_row["closed"])


def get_open_polls():
    con = db()
    rows = con.execute("SELECT * FROM polls WHERE closed = 0").fetchall()
    con.close()
    return rows


def get_polls_pending_cleanup():
    con = db()
    rows = con.execute(
        """
        SELECT *
        FROM polls
        WHERE cleanup_deleted = 0
          AND message_id IS NOT NULL
          AND channel_id IS NOT NULL
        """
    ).fetchall()
    con.close()
    return rows


def get_latest_poll_message_id_for_channel(channel_id: int):
    con = db()
    row = con.execute(
        """
        SELECT message_id
        FROM polls
        WHERE channel_id = ? AND message_id IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel_id,),
    ).fetchone()
    con.close()
    return row["message_id"] if row else None


def get_latest_open_poll_for_channel(channel_id: int):
    con = db()
    row = con.execute(
        """
        SELECT *
        FROM polls
        WHERE channel_id = ? AND closed = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel_id,),
    ).fetchone()
    con.close()
    return row


def upsert_vote(poll_id: int, user_id: int, response: str, later_time: str | None = None):
    con = db()
    con.execute(
        """
        INSERT INTO poll_votes (poll_id, user_id, response, later_time, voted_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(poll_id, user_id) DO UPDATE SET
            response = excluded.response,
            later_time = excluded.later_time,
            voted_at = excluded.voted_at
        """,
        (poll_id, user_id, response, later_time, datetime.now(BOT_TZ).isoformat()),
    )
    con.commit()
    con.close()


def delete_vote(poll_id: int, user_id: int):
    con = db()
    con.execute(
        "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
        (poll_id, user_id),
    )
    con.commit()
    con.close()


def get_votes_for_poll(poll_id: int):
    con = db()
    rows = con.execute(
        "SELECT * FROM poll_votes WHERE poll_id = ? ORDER BY voted_at ASC",
        (poll_id,),
    ).fetchall()
    con.close()
    return rows


def mark_poll_threshold_announced(poll_id: int):
    con = db()
    con.execute("UPDATE polls SET yes_threshold_announced = 1 WHERE id = ?", (poll_id,))
    con.commit()
    con.close()


def mark_poll_reminder_60(poll_id: int):
    con = db()
    con.execute("UPDATE polls SET remind_60_sent = 1 WHERE id = ?", (poll_id,))
    con.commit()
    con.close()


def mark_poll_reminder_5(poll_id: int):
    con = db()
    con.execute("UPDATE polls SET remind_5_sent = 1 WHERE id = ?", (poll_id,))
    con.commit()
    con.close()


def close_poll(poll_id: int):
    con = db()
    con.execute("UPDATE polls SET closed = 1 WHERE id = ?", (poll_id,))
    con.commit()
    con.close()


def mark_poll_cleanup_deleted(poll_id: int):
    con = db()
    con.execute("UPDATE polls SET cleanup_deleted = 1 WHERE id = ?", (poll_id,))
    con.commit()
    con.close()


def daily_poll_exists_for_date(date_text: str):
    con = db()
    row = con.execute(
        "SELECT id FROM polls WHERE kind = 'daily_funclub' AND date_text = ? LIMIT 1",
        (date_text,),
    ).fetchone()
    con.close()
    return row is not None


def get_setting(key: str, default: str | None = None):
    con = db()
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    con.close()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    con = db()
    con.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    con.commit()
    con.close()


def delete_setting(key: str):
    con = db()
    con.execute("DELETE FROM settings WHERE key = ?", (key,))
    con.commit()
    con.close()


def is_auto_availability_enabled():
    return get_setting("auto_availability_enabled", "1") == "1"


EA_API_BASE_URL = "https://proclubs.ea.com/api/fc/"
EA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/112.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}
EA_MATCH_TYPES = ("leagueMatch", "playoffMatch")
EA_PLATFORM_LABELS = {
    "common-gen5": "PS5 / Xbox Series X|S / PC",
    "common-gen4": "PS4 / Xbox One",
    "nx": "Switch",
}


class EAStatsError(Exception):
    pass


def ea_get_json_sync(endpoint: str, params: dict[str, str]):
    url = f"{EA_API_BASE_URL}{endpoint}?{urlencode(params)}"
    request = Request(url, headers=EA_HEADERS, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
            encoding = response.headers.get("Content-Encoding", "")
            if "gzip" in encoding:
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 403:
            return ea_get_json_with_curl_sync(url, endpoint)
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise EAStatsError(f"EA API {endpoint} hat Status {exc.code}: {body[:120]}") from exc
    except URLError as exc:
        raise EAStatsError(f"EA API Anfrage fehlgeschlagen: {exc}") from exc
    except TimeoutError as exc:
        raise EAStatsError("EA API hat zu lange nicht geantwortet.") from exc
    except json.JSONDecodeError as exc:
        raise EAStatsError("EA API hat keine gültigen JSON-Daten geliefert.") from exc


def ea_get_json_with_curl_sync(url: str, endpoint: str):
    command = [
        "curl",
        "-L",
        "--silent",
        "--show-error",
        "--fail",
        "--compressed",
        "--max-time",
        "20",
        "-A",
        EA_HEADERS["User-Agent"],
        "-H",
        f"Accept: {EA_HEADERS['Accept']}",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=25,
        )
    except FileNotFoundError as exc:
        raise EAStatsError(
            "EA blockt den Python-Request mit 403 und `curl` ist auf dem Host nicht verfügbar."
        ) from exc
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "").strip()
        raise EAStatsError(
            f"EA API {endpoint} wurde auch vom curl-Fallback geblockt: {message[:160]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise EAStatsError("EA API curl-Fallback hat zu lange nicht geantwortet.") from exc

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise EAStatsError("EA API curl-Fallback hat keine gültigen JSON-Daten geliefert.") from exc


async def ea_get_json(endpoint: str, params: dict[str, str]):
    return await asyncio.to_thread(ea_get_json_sync, endpoint, params)


async def ea_search_club(club_name: str, platform: str):
    return await ea_get_json(
        "allTimeLeaderboard/search",
        {"platform": platform, "clubName": club_name},
    )


async def ea_fetch_matches(club_id: str, platform: str, match_type: str):
    return await ea_get_json(
        "clubs/matches",
        {"platform": platform, "clubIds": str(club_id), "matchType": match_type},
    )


async def ea_fetch_member_stats(club_id: str, platform: str):
    return await ea_get_json(
        "members/stats",
        {"platform": platform, "clubId": str(club_id)},
    )


def int_stat(data: dict, key: str, default: int = 0) -> int:
    try:
        return int(float(data.get(key, default) or default))
    except (TypeError, ValueError):
        return default


def float_stat(data: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(data.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def percent(made: int, attempts: int) -> str:
    if attempts <= 0:
        return "-"
    return f"{round((made / attempts) * 100)}%"


def format_match_time(timestamp: int | str | None) -> str:
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        return "unbekannt"
    return datetime.fromtimestamp(value, BOT_TZ).strftime("%d.%m.%Y %H:%M")


def get_configured_ea_club():
    club_id = get_setting("ea_club_id")
    platform = get_setting("ea_platform")
    if not club_id or not platform:
        return None
    return {
        "club_id": club_id,
        "club_name": get_setting("ea_club_name", "Vollpfosten CR8"),
        "platform": platform,
        "channel_id": get_setting("ea_stats_channel_id"),
    }


def get_own_and_opponent(match: dict, club_id: str):
    clubs = match.get("clubs", {})
    own = clubs.get(str(club_id))
    opponent = None
    for current_id, club_data in clubs.items():
        if str(current_id) != str(club_id):
            opponent = club_data
            break
    return own, opponent


def get_match_players(match: dict, club_id: str) -> list[dict]:
    raw_players = match.get("players", {}).get(str(club_id), {})
    players = []
    for player_id, stats in raw_players.items():
        item = dict(stats)
        item["player_id"] = str(player_id)
        item["playername"] = item.get("playername") or item.get("name") or f"Spieler {player_id}"
        players.append(item)
    return sorted(players, key=lambda p: float_stat(p, "rating"), reverse=True)


def player_short_line(player: dict) -> str:
    goals = int_stat(player, "goals")
    assists = int_stat(player, "assists")
    rating = float_stat(player, "rating")
    passes = int_stat(player, "passesmade")
    pass_attempts = int_stat(player, "passattempts")
    tackles = int_stat(player, "tacklesmade")
    tackle_attempts = int_stat(player, "tackleattempts")
    return (
        f"**{player['playername']}** - {rating:.1f} Rating | "
        f"{goals}T/{assists}A | Pass {percent(passes, pass_attempts)} | Tackles {tackles}/{tackle_attempts}"
    )


def build_match_report_embed(match: dict, club_id: str, match_type: str) -> discord.Embed:
    own, opponent = get_own_and_opponent(match, club_id)
    if not own:
        raise EAStatsError("Eigener Club wurde im Match nicht gefunden.")

    own_name = own.get("details", {}).get("name", get_setting("ea_club_name", "Unser Club"))
    opponent_name = opponent.get("details", {}).get("name", "Gegner") if opponent else "Gegner"
    own_goals = int_stat(own, "goals")
    opponent_goals = int_stat(opponent or {}, "goals")
    result = "Sieg" if own_goals > opponent_goals else "Niederlage" if own_goals < opponent_goals else "Unentschieden"
    color = discord.Color.green() if result == "Sieg" else discord.Color.red() if result == "Niederlage" else discord.Color.gold()

    players = get_match_players(match, club_id)
    totals = {
        "goals": sum(int_stat(p, "goals") for p in players),
        "assists": sum(int_stat(p, "assists") for p in players),
        "shots": sum(int_stat(p, "shots") for p in players),
        "passes": sum(int_stat(p, "passesmade") for p in players),
        "pass_attempts": sum(int_stat(p, "passattempts") for p in players),
        "tackles": sum(int_stat(p, "tacklesmade") for p in players),
        "tackle_attempts": sum(int_stat(p, "tackleattempts") for p in players),
        "saves": sum(int_stat(p, "saves") for p in players),
        "redcards": sum(int_stat(p, "redcards") for p in players),
    }

    embed = discord.Embed(
        title=f"{result}: {own_name} {own_goals}:{opponent_goals} {opponent_name}",
        description=f"**{match_type_label(match_type)}** | {format_match_time(match.get('timestamp'))}",
        color=color,
    )
    embed.add_field(
        name="Teamstats",
        value=(
            f"Schüsse: **{totals['shots']}**\n"
            f"Tore/Assists: **{totals['goals']} / {totals['assists']}**\n"
            f"Passgenauigkeit: **{percent(totals['passes'], totals['pass_attempts'])}** "
            f"({totals['passes']}/{totals['pass_attempts']})\n"
            f"Tackles: **{totals['tackles']}/{totals['tackle_attempts']}**\n"
            f"Paraden: **{totals['saves']}** | Rote Karten: **{totals['redcards']}**\n"
            "xG: **nicht von EA geliefert**"
        ),
        inline=False,
    )

    top_players = players[:5]
    embed.add_field(
        name="Topspieler",
        value="\n".join(player_short_line(player) for player in top_players) if top_players else "-",
        inline=False,
    )
    embed.set_footer(text=f"Match-ID: {match.get('matchId')} | EA Clubs Daten")
    return embed


def match_type_label(match_type: str) -> str:
    return "Playoff-Spiel" if match_type == "playoffMatch" else "Liga-Spiel"


def build_player_stats_embed(match: dict, club_id: str, player_id: str) -> discord.Embed:
    players = match.get("players", {}).get(str(club_id), {})
    player = players.get(str(player_id))
    if player is None:
        raise EAStatsError("Spieler wurde in diesem Match nicht gefunden.")

    name = player.get("playername", "Spieler")
    passes = int_stat(player, "passesmade")
    pass_attempts = int_stat(player, "passattempts")
    tackles = int_stat(player, "tacklesmade")
    tackle_attempts = int_stat(player, "tackleattempts")

    embed = discord.Embed(
        title=f"Spieler-Stats: {name}",
        description=f"Match-ID: `{match.get('matchId')}`",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Offensive",
        value=(
            f"Tore: **{int_stat(player, 'goals')}**\n"
            f"Assists: **{int_stat(player, 'assists')}**\n"
            f"Schüsse: **{int_stat(player, 'shots')}**\n"
            "xG: **nicht von EA geliefert**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Spielaufbau",
        value=(
            f"Pässe: **{passes}/{pass_attempts}**\n"
            f"Passgenauigkeit: **{percent(passes, pass_attempts)}**\n"
            f"Position: **{player.get('pos', '-')}**\n"
            f"Rating: **{float_stat(player, 'rating'):.2f}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Defensive/Torwart",
        value=(
            f"Tackles: **{tackles}/{tackle_attempts}**\n"
            f"Tacklequote: **{percent(tackles, tackle_attempts)}**\n"
            f"Paraden: **{int_stat(player, 'saves')}**\n"
            f"Gegentore: **{int_stat(player, 'goalsconceded')}**\n"
            f"Clean Sheets: **{int_stat(player, 'cleansheetsany')}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Sonstiges",
        value=(
            f"Spielzeit: **{int_stat(player, 'secondsPlayed') // 60} Min.**\n"
            f"MOTM: **{'Ja' if int_stat(player, 'mom') else 'Nein'}**\n"
            f"Rote Karten: **{int_stat(player, 'redcards')}**"
        ),
        inline=False,
    )
    return embed


def save_ea_match(match_id: str, match_type: str, channel_id: int | None, message_id: int | None, snapshot: dict):
    con = db()
    con.execute(
        """
        INSERT INTO ea_club_matches (match_id, match_type, channel_id, message_id, snapshot_json, posted_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(match_id, match_type) DO UPDATE SET
            channel_id = excluded.channel_id,
            message_id = excluded.message_id,
            snapshot_json = excluded.snapshot_json
        """,
        (
            str(match_id),
            match_type,
            channel_id,
            message_id,
            json.dumps(snapshot, ensure_ascii=False),
            datetime.now(BOT_TZ).isoformat(),
        ),
    )
    con.commit()
    con.close()


def mark_ea_match_seen(match_id: str, match_type: str, snapshot: dict):
    save_ea_match(str(match_id), match_type, None, None, snapshot)


def ea_match_exists(match_id: str, match_type: str) -> bool:
    con = db()
    row = con.execute(
        "SELECT 1 FROM ea_club_matches WHERE match_id = ? AND match_type = ? LIMIT 1",
        (str(match_id), match_type),
    ).fetchone()
    con.close()
    return row is not None


def get_ea_match_by_message_id(message_id: int):
    con = db()
    row = con.execute(
        "SELECT * FROM ea_club_matches WHERE message_id = ? LIMIT 1",
        (message_id,),
    ).fetchone()
    con.close()
    if row is None:
        return None
    data = dict(row)
    data["snapshot"] = json.loads(data["snapshot_json"])
    return data


def get_latest_posted_ea_match():
    con = db()
    row = con.execute(
        """
        SELECT *
        FROM ea_club_matches
        WHERE message_id IS NOT NULL
        ORDER BY posted_at DESC
        LIMIT 1
        """
    ).fetchone()
    con.close()
    if row is None:
        return None
    data = dict(row)
    data["snapshot"] = json.loads(data["snapshot_json"])
    return data


def get_warning_info(user_id: int):
    con = db()
    row = con.execute(
        "SELECT * FROM nonvote_warnings WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    con.close()
    if row is None:
        return {"user_id": user_id, "missed_count": 0, "last_warned_at_count": 0}
    return dict(row)


def increase_missed_vote(user_id: int):
    info = get_warning_info(user_id)
    new_count = info["missed_count"] + 1

    con = db()
    con.execute(
        """
        INSERT INTO nonvote_warnings (user_id, missed_count, last_warned_at_count)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET missed_count = excluded.missed_count
        """,
        (user_id, new_count, info["last_warned_at_count"]),
    )
    con.commit()
    con.close()
    return new_count, info["last_warned_at_count"]


def mark_warned_count(user_id: int, count: int):
    info = get_warning_info(user_id)
    con = db()
    con.execute(
        """
        INSERT INTO nonvote_warnings (user_id, missed_count, last_warned_at_count)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            missed_count = excluded.missed_count,
            last_warned_at_count = excluded.last_warned_at_count
        """,
        (user_id, info["missed_count"], count),
    )
    con.commit()
    con.close()


def reset_missed_vote_count(user_id: int):
    info = get_warning_info(user_id)
    con = db()
    con.execute(
        """
        INSERT INTO nonvote_warnings (user_id, missed_count, last_warned_at_count)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            missed_count = excluded.missed_count,
            last_warned_at_count = excluded.last_warned_at_count
        """,
        (user_id, 0, info["last_warned_at_count"]),
    )
    con.commit()
    con.close()


def strip_managed_nick(name: str) -> str:
    if not name:
        return "Spieler"
    pattern = r"^(?:#\d{1,2}\s*\|\s*)?(?:[A-Z]{2,4}(?:/[A-Z]{2,4})?\s*\|\s*)?"
    stripped = re.sub(pattern, "", name).strip()
    return stripped if stripped else name


def strip_number_from_nick(name: str) -> str:
    if not name:
        return "Spieler"
    stripped = re.sub(r"^#\d{1,2}\s*\|\s*", "", name).strip()
    return stripped if stripped else name


def parse_main_positions_from_nick(member: discord.Member) -> list[str]:
    raw = member.nick or member.global_name or member.name or ""
    raw = raw.strip()
    match = re.match(r"^(?:#\d{1,2}\s*\|\s*)?([A-Z]{2,4}(?:/[A-Z]{2,4})?)\s*\|", raw)
    if not match:
        return []

    part = match.group(1).strip()
    positions = [p.strip() for p in part.split("/") if p.strip() in POSITIONS]
    cleaned = []
    for p in positions:
        if p not in cleaned:
            cleaned.append(p)
    return cleaned[:2]


def parse_hhmm(time_str: str):
    try:
        t = datetime.strptime(time_str.strip(), "%H:%M")
        return t.hour, t.minute
    except ValueError:
        return None


def clean_name_for_availability(member: discord.Member) -> str:
    profile = get_profile(member.id)
    if profile["base_name"]:
        return profile["base_name"]

    raw = member.nick or member.global_name or member.name
    raw = strip_number_from_nick(raw)
    raw = strip_managed_nick(raw)
    return raw


def get_first_main_position_for_member(member: discord.Member) -> str:
    profile = get_profile(member.id)
    if profile["main_positions"]:
        return profile["main_positions"][0]

    from_nick = parse_main_positions_from_nick(member)
    if from_nick:
        return from_nick[0]

    return "?"


def get_weekday_text(dt: datetime) -> str:
    return WEEKDAY_DE[dt.weekday()]


def parse_manual_start_datetime(datum: str, uhrzeit: str):
    try:
        date_part = datetime.strptime(datum.strip(), "%d.%m.%Y")
        time_part = datetime.strptime(uhrzeit.strip(), "%H:%M")
        return datetime(
            year=date_part.year,
            month=date_part.month,
            day=date_part.day,
            hour=time_part.hour,
            minute=time_part.minute,
            tzinfo=BOT_TZ,
        )
    except ValueError:
        return None


def calculate_recommended_start(votes_rows, start_at: datetime):
    yes_count = sum(1 for v in votes_rows if v["response"] == "yes")
    later_votes = [v for v in votes_rows if v["response"] == "later" and v["later_time"]]

    if yes_count >= 4:
        return start_at.strftime("%H:%M"), 0

    needed = 4 - yes_count
    if len(later_votes) < needed:
        return None, needed

    parsed_later = []
    for row in later_votes:
        parsed = parse_hhmm(row["later_time"])
        if parsed is None:
            continue
        hour, minute = parsed
        later_dt = datetime(
            year=start_at.year,
            month=start_at.month,
            day=start_at.day,
            hour=hour,
            minute=minute,
            tzinfo=BOT_TZ,
        )
        if later_dt < start_at:
            later_dt = start_at
        parsed_later.append(later_dt)

    if len(parsed_later) < needed:
        return None, needed

    parsed_later.sort()
    relevant = parsed_later[:needed]
    recommended = max(relevant)
    return recommended.strftime("%H:%M"), needed


def is_manager(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or any(r.name == ROLE_MANAGER for r in member.roles)
    )


def has_role(member: discord.Member, role_name: str) -> bool:
    return any(r.name == role_name for r in member.roles)


async def get_fresh_member(member: discord.Member) -> discord.Member:
    try:
        fresh = await member.guild.fetch_member(member.id)
        return fresh
    except discord.HTTPException:
        cached = member.guild.get_member(member.id)
        return cached or member


def can_use_profile_system(member: discord.Member) -> bool:
    return (
        has_role(member, ROLE_TESTER)
        or has_role(member, ROLE_MANAGER)
        or has_role(member, ROLE_STAMMELF)
        or has_role(member, ROLE_REGISTERED)
        or has_role(member, ROLE_FINISHED)
    )


def get_role_by_name(guild: discord.Guild, role_name: str):
    return discord.utils.get(guild.roles, name=role_name)


def get_existing_main_roles_from_member(member: discord.Member) -> list[str]:
    found = []
    for role in member.roles:
        if role.name.startswith("Haupt-"):
            pos = role.name.replace("Haupt-", "", 1)
            if pos in POSITIONS and pos not in found:
                found.append(pos)
    return found[:2]


def get_existing_side_roles_from_member(member: discord.Member) -> list[str]:
    found = []
    for role in member.roles:
        if role.name.startswith("Neben-"):
            pos = role.name.replace("Neben-", "", 1)
            if pos in POSITIONS and pos not in found:
                found.append(pos)
    return found


async def rebuild_profile_from_server_state(member: discord.Member):
    profile = get_profile(member.id)

    base_name = profile["base_name"] or strip_managed_nick(member.nick or member.global_name or member.name)
    jersey = profile["jersey"]
    main_positions = list(profile["main_positions"])
    side_positions = list(profile["side_positions"])

    if not jersey:
        raw = member.nick or member.global_name or member.name or ""
        match = re.match(r"^#(\d{1,2})\s*\|", raw.strip())
        if match:
            jersey = match.group(1)

    if not main_positions:
        main_positions = parse_main_positions_from_nick(member)

    if not main_positions:
        main_positions = get_existing_main_roles_from_member(member)

    if not side_positions:
        side_positions = get_existing_side_roles_from_member(member)

    side_positions = [p for p in side_positions if p not in main_positions]

    save_profile(
        member.id,
        base_name=base_name,
        jersey=jersey,
        main_positions=main_positions,
        side_positions=side_positions,
    )


def build_nick(base_name: str, jersey: str | None, main_positions: list[str]) -> str:
    base_name = base_name.strip() if base_name else "Spieler"

    left_parts = []
    if jersey:
        left_parts.append(f"#{jersey}")
    if main_positions:
        left_parts.append("/".join(main_positions))

    if left_parts:
        nick = f"{' | '.join(left_parts)} | {base_name}"
    else:
        nick = base_name

    if len(nick) <= 32:
        return nick

    if left_parts:
        prefix = f"{' | '.join(left_parts)} | "
        remaining = max(1, 32 - len(prefix))
        return prefix + base_name[:remaining]

    return base_name[:32]


def next_step_message(member: discord.Member):
    profile = get_profile(member.id)

    if not has_role(member, ROLE_TESTER):
        return "Nächster Schritt: Öffne **#regeln** und klicke auf **Regeln akzeptieren**."
    if not (1 <= len(profile["main_positions"]) <= 2):
        return "Nächster Schritt: Öffne **#profil** und wähle **1-2 Hauptpositionen**."
    if not profile["jersey"]:
        return "Nächster Schritt: Setze in **#profil** deine **freie Trikotnummer**."
    return "✅ Fertig. Du bist registriert und kannst mitspielen."


async def send_join_dm(member: discord.Member):
    fresh = await get_fresh_member(member)
    text = (
        f"Willkommen auf **{fresh.guild.name}**.\n\n"
        "**Dein Start in 3 Schritten:**\n"
        "1. **#regeln** öffnen und Regeln akzeptieren.\n"
        "2. **#profil** öffnen, Hauptpositionen wählen und Trikotnummer setzen.\n"
        "3. Optional Nebenpositionen wählen. Wenn du keine hast, einfach überspringen.\n\n"
        "Der Bot setzt Rollen, Nickname und Zugriff automatisch.\n\n"
        f"{next_step_message(fresh)}"
    )
    try:
        await fresh.send(text)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


def suppress_profile_rebuild(member_id: int):
    profile_update_suppressed_until[member_id] = datetime.now(BOT_TZ) + timedelta(seconds=PROFILE_UPDATE_SUPPRESS_SECONDS)


def is_profile_rebuild_suppressed(member_id: int) -> bool:
    until = profile_update_suppressed_until.get(member_id)
    if until is None:
        return False
    if datetime.now(BOT_TZ) >= until:
        profile_update_suppressed_until.pop(member_id, None)
        return False
    return True


async def send_private_progress_dm(member: discord.Member, intro: str):
    fresh = await get_fresh_member(member)
    text = f"{intro}\n\n{next_step_message(fresh)}"
    try:
        await fresh.send(text)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


async def ensure_base_name(member: discord.Member):
    profile = get_profile(member.id)
    if profile["base_name"]:
        return profile["base_name"]

    raw = member.nick or member.global_name or member.name
    base_name = strip_managed_nick(raw)
    save_profile(member.id, base_name=base_name)
    return base_name


def meets_profile_requirements(profile: dict) -> bool:
    return (
        bool(profile["jersey"])
        and 1 <= len(profile["main_positions"]) <= 2
    )


async def sync_position_roles(member: discord.Member, rebuild: bool = True):
    if rebuild:
        await rebuild_profile_from_server_state(member)
    guild = member.guild
    profile = get_profile(member.id)

    wanted_main = set(profile["main_positions"])
    wanted_side = set(profile["side_positions"])

    current_main_roles = []
    current_side_roles = []
    for role in member.roles:
        if role.name.startswith("Haupt-"):
            current_main_roles.append(role)
        elif role.name.startswith("Neben-"):
            current_side_roles.append(role)

    safe_has_any_position_data = bool(wanted_main or wanted_side) or not rebuild

    remove_roles = []
    if safe_has_any_position_data:
        for role in current_main_roles:
            pos = role.name.replace("Haupt-", "", 1)
            if pos not in wanted_main:
                remove_roles.append(role)

        for role in current_side_roles:
            pos = role.name.replace("Neben-", "", 1)
            if pos not in wanted_side:
                remove_roles.append(role)

    add_roles = []
    for pos in wanted_main:
        role = get_role_by_name(guild, main_role_name(pos))
        if role and role not in member.roles:
            add_roles.append(role)

    for pos in wanted_side:
        role = get_role_by_name(guild, side_role_name(pos))
        if role and role not in member.roles:
            add_roles.append(role)

    try:
        if remove_roles:
            await member.remove_roles(*remove_roles, reason="Positionsrollen aktualisiert")
        if add_roles:
            await member.add_roles(*add_roles, reason="Positionsrollen aktualisiert")
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


async def remove_old_plain_position_roles(member: discord.Member):
    old_roles = [r for r in member.roles if r.name in POSITIONS]
    if old_roles:
        try:
            await member.remove_roles(*old_roles, reason="Alte Positionsrollen durch Haupt/Neben ersetzt")
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass


async def update_registered_role(member: discord.Member, rebuild: bool = True):
    registered_role = get_role_by_name(member.guild, ROLE_REGISTERED)
    if registered_role is None:
        return

    if rebuild:
        await rebuild_profile_from_server_state(member)
    profile = get_profile(member.id)
    should_have = has_role(member, ROLE_TESTER) and meets_profile_requirements(profile)
    has_registered = registered_role in member.roles

    try:
        if should_have and not has_registered:
            await member.add_roles(registered_role, reason="Tester und Profil vollständig")
        elif not should_have and has_registered:
            await member.remove_roles(registered_role, reason="Tester oder Profil unvollständig")
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


async def update_finished_role(member: discord.Member, rebuild: bool = True):
    finished_role = get_role_by_name(member.guild, ROLE_FINISHED)
    if finished_role is None:
        return

    if rebuild:
        await rebuild_profile_from_server_state(member)
    profile = get_profile(member.id)
    should_have = has_role(member, ROLE_TESTER) and meets_profile_requirements(profile)
    has_finished = finished_role in member.roles

    try:
        if should_have and not has_finished:
            await member.add_roles(finished_role, reason="Tester und Profil vollständig")
        elif not should_have and has_finished:
            await member.remove_roles(finished_role, reason="Tester oder Profil unvollständig")
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


async def update_member_profile(member: discord.Member, rebuild: bool = True):
    if rebuild:
        await rebuild_profile_from_server_state(member)
    base_name = await ensure_base_name(member)
    suppress_profile_rebuild(member.id)
    await sync_position_roles(member, rebuild=False)
    await update_registered_role(member, rebuild=False)
    await update_finished_role(member, rebuild=False)

    profile = get_profile(member.id)
    effective_main = profile["main_positions"]

    if rebuild and not effective_main:
        effective_main = parse_main_positions_from_nick(member) or get_existing_main_roles_from_member(member)

    if can_use_profile_system(member):
        new_nick = build_nick(base_name, profile["jersey"], effective_main)
    else:
        new_nick = base_name

    try:
        await member.edit(nick=new_nick, reason="Vollpfosten CR8: Profil aktualisiert")
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


async def create_role_if_missing(guild: discord.Guild, name: str, colour: discord.Colour = discord.Colour.default()):
    role = get_role_by_name(guild, name)
    if role is None:
        role = await guild.create_role(name=name, colour=colour, reason="Vollpfosten CR8 Setup")
    return role


async def create_category_if_missing(guild: discord.Guild, name: str):
    category = discord.utils.get(guild.categories, name=name)
    if category is None:
        category = await guild.create_category(name=name, reason="Vollpfosten CR8 Setup")
    return category


async def create_text_if_missing(guild: discord.Guild, category: discord.CategoryChannel, name: str, overwrites=None):
    channel = discord.utils.get(guild.text_channels, name=name)
    if channel is None:
        channel = await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason="Vollpfosten CR8 Setup",
        )
    return channel


async def create_voice_if_missing(guild: discord.Guild, category: discord.CategoryChannel, name: str, overwrites=None, user_limit=None):
    channel = discord.utils.get(guild.voice_channels, name=name)
    if channel is None:
        channel = await guild.create_voice_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            user_limit=user_limit or 0,
            reason="Vollpfosten CR8 Setup",
        )
    return channel


def get_voice_channel_by_names(guild: discord.Guild, names: list[str]):
    lowered = {name.lower() for name in names}
    for channel in guild.voice_channels:
        if channel.name.lower() in lowered:
            return channel
    return None


async def apply_channel_overwrites(channel, overwrites, reason: str = "Vollpfosten CR8 Setup"):
    try:
        await channel.edit(overwrites=overwrites, reason=reason)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


async def delete_channel_if_exists(guild: discord.Guild, name: str):
    channel = discord.utils.get(guild.channels, name=name)
    if channel is None:
        return False
    try:
        await channel.delete(reason="Vollpfosten CR8 Setup: alter Profilkanal entfernt")
        return True
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False


def overwrite_hidden_except(guild, roles_allowed, can_send=True):
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for role in roles_allowed:
        ow[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=can_send,
            read_message_history=True,
        )
    if guild.me is not None:
        ow[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
    return ow


def overwrite_locked_text_for_managers(guild, manager_role):
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=False,
            attach_files=False,
            embed_links=False,
            add_reactions=False,
            use_application_commands=False,
        ),
        manager_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            add_reactions=True,
            use_application_commands=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            add_reactions=True,
            use_application_commands=True,
        ),
    }


def overwrite_team_locked_text_for_managers(guild, finished_role, manager_role):
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            read_message_history=False,
            send_messages=False,
            attach_files=False,
            embed_links=False,
            add_reactions=False,
            use_application_commands=False,
        ),
        finished_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=False,
            attach_files=False,
            embed_links=False,
            add_reactions=False,
            use_application_commands=False,
        ) if finished_role else discord.PermissionOverwrite(),
        manager_role: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            add_reactions=True,
            use_application_commands=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            read_message_history=True,
            send_messages=True,
            attach_files=True,
            embed_links=True,
            add_reactions=True,
            use_application_commands=True,
        ),
    }


def overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        tester_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True),
        manager_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True),
        stammelf_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True),
    }


def overwrite_voice_bank(guild, manager_role):
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            move_members=False,
        ),
        manager_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            move_members=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
        ),
    }


def overwrite_voice_for_finished(guild, finished_role, manager_role):
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            connect=False,
            speak=False,
            move_members=False,
        ),
        finished_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            move_members=False,
        ) if finished_role else discord.PermissionOverwrite(),
        manager_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            move_members=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
        ),
    }


def overwrite_voice_public(guild, tester_role, manager_role, stammelf_role):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        tester_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        manager_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        stammelf_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
    }


def overwrite_voice_kabine(guild, finished_role, manager_role, stammelf_role):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        finished_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            move_members=False,
        ) if finished_role else discord.PermissionOverwrite(),
        stammelf_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            move_members=False,
            use_soundboard=True,
            use_external_sounds=True,
        ),
        manager_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            stream=True,
            use_voice_activation=True,
            move_members=True,
            use_soundboard=True,
            use_external_sounds=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
            use_soundboard=True,
            use_external_sounds=True,
        ),
    }


def overwrite_voice_manager(guild, manager_role):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        manager_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
    }


def build_profile_channel_overwrites_normal(guild: discord.Guild):
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        ),
    }


def build_profile_channel_overwrites_off(guild: discord.Guild):
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            read_message_history=False,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        ),
    }


def build_availability_overwrites(guild: discord.Guild):
    fertig_role = get_role_by_name(guild, ROLE_FINISHED)
    return {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            read_message_history=False,
            add_reactions=False,
        ),
        fertig_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
            add_reactions=False,
        ) if fertig_role else discord.PermissionOverwrite(),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            add_reactions=False,
        ),
    }


async def set_profile_channels_mode(guild: discord.Guild, offline_mode: bool):
    channel_names = [CH_PROFILE, CH_MAIN_POSITIONS, CH_SIDE_POSITIONS, CH_NUMBERS]
    text_channels = {c.name: c for c in guild.text_channels}
    overwrites = build_profile_channel_overwrites_off(guild) if offline_mode else build_profile_channel_overwrites_normal(guild)

    for name in channel_names:
        channel = text_channels.get(name)
        if channel is not None:
            await channel.edit(overwrites=overwrites, reason="Vollpfosten CR8: Profilkanäle umgeschaltet")


async def delete_panel_messages(channel: discord.TextChannel, marker: str, content: str, bot_user):
    first_content_line = next((line for line in content.splitlines() if line.strip()), "")
    to_delete = []
    async for msg in channel.history(limit=100):
        is_old_marked_panel = msg.content.startswith(marker)
        is_current_panel = bool(first_content_line) and msg.content.startswith(first_content_line)
        if msg.author == bot_user and (is_old_marked_panel or is_current_panel):
            to_delete.append(msg)

    for msg in to_delete:
        try:
            await msg.delete()
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass


async def replace_panel_message(channel: discord.TextChannel, marker: str, content: str, view: discord.ui.View, bot_user):
    await delete_panel_messages(channel, marker, content, bot_user)
    await channel.send(content, view=view)


def find_emoji_by_names(guild: discord.Guild, names: list[str]):
    for name in names:
        emoji = discord.utils.get(guild.emojis, name=name)
        if emoji is not None:
            return emoji
    return None


def get_position_emoji(guild: discord.Guild, pos: str, counters: dict[str, int]):
    if pos == "LF":
        return find_emoji_by_names(guild, ["LF_RF", "RF_LF"])
    if pos == "RF":
        return find_emoji_by_names(guild, ["LF_RF2", "RF_LF2"])
    if pos == "RV":
        return find_emoji_by_names(guild, ["RV_LV"])
    if pos == "LV":
        return find_emoji_by_names(guild, ["RV_LV2", "LV_RV2"])
    if pos == "ST":
        count = counters.get("ST", 0)
        counters["ST"] = count + 1
        return find_emoji_by_names(guild, ["ST"] if count == 0 else ["ST2", "ST_2"])
    if pos == "IV":
        count = counters.get("IV", 0)
        counters["IV"] = count + 1
        return find_emoji_by_names(guild, ["IV"] if count == 0 else ["IV2", "IV_2"])
    if pos == "TW":
        return find_emoji_by_names(guild, ["TW"])
    if pos == "ZOM":
        return find_emoji_by_names(guild, ["ZOM"])
    if pos == "ZDM":
        return find_emoji_by_names(guild, ["ZDM"])
    if pos == "ZM":
        return find_emoji_by_names(guild, ["ZM"])
    return None


def build_poll_embed(guild: discord.Guild, poll_row, votes_rows):
    title = poll_row["title"]
    weekday_text = poll_row["weekday_text"]
    date_text = poll_row["date_text"]
    time_text = poll_row["time_text"]

    try:
        start_at = datetime.fromisoformat(poll_row["start_at"])
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=BOT_TZ)
    except ValueError:
        start_at = datetime.now(BOT_TZ)

    embed = discord.Embed(
        title=title,
        description=(
            f"📅 **{weekday_text}, {date_text}**\n"
            f"⏰ **{time_text}**\n\n"
            f"{POLL_NOTE}"
        ),
        colour=discord.Colour.green(),
    )

    offensive_lines = []
    midfield_lines = []
    defensive_lines = []
    no_lines = []

    yes_like_members = []
    no_members = []

    for vote in votes_rows:
        member = guild.get_member(vote["user_id"])
        if member is None:
            continue
        if vote["response"] in ("yes", "later"):
            yes_like_members.append((member, vote["voted_at"], vote["response"], vote["later_time"]))
        elif vote["response"] == "no":
            no_members.append(member)

    yes_like_members.sort(key=lambda x: x[1])
    no_members.sort(key=lambda m: clean_name_for_availability(m).lower())

    emoji_counters_yes = {"ST": 0, "IV": 0}
    emoji_counters_no = {"ST": 0, "IV": 0}

    for member, _, response, later_time in yes_like_members:
        pos = get_first_main_position_for_member(member)
        name = clean_name_for_availability(member)
        emoji = get_position_emoji(guild, pos, emoji_counters_yes)
        prefix = str(emoji) if emoji else "•"

        if response == "later" and later_time:
            line = f"{prefix} {pos} | {name} – {later_time}"
        else:
            line = f"{prefix} {pos} | {name}"

        if pos in OFF_POSITIONS:
            offensive_lines.append(line)
        elif pos in MID_POSITIONS:
            midfield_lines.append(line)
        else:
            defensive_lines.append(line)

    for member in no_members:
        pos = get_first_main_position_for_member(member)
        name = clean_name_for_availability(member)
        emoji = get_position_emoji(guild, pos, emoji_counters_no)
        prefix = str(emoji) if emoji else "•"
        no_lines.append(f"{prefix} {pos} | {name}")

    embed.add_field(name="🔥 Angreifer", value="\n".join(offensive_lines) if offensive_lines else "-", inline=True)
    embed.add_field(name="⚙️ Mittelfeld", value="\n".join(midfield_lines) if midfield_lines else "-", inline=True)
    embed.add_field(name="🛡️ Defensive", value="\n".join(defensive_lines) if defensive_lines else "-", inline=True)
    embed.add_field(name="❌ Nein", value="\n".join(no_lines) if no_lines else "-", inline=False)

    recommended_time, needed = calculate_recommended_start(votes_rows, start_at)
    if recommended_time:
        embed.add_field(
            name="⏰ Empfohlene Startzeit",
            value=recommended_time,
            inline=False,
        )
    elif needed > 0:
        embed.add_field(
            name="⏰ Empfohlene Startzeit",
            value=f"Noch nicht genug Zusagen. Es fehlen mindestens **{needed}** weitere verfügbare Spieler.",
            inline=False,
        )

    return embed


def get_main_positions_for_member(member: discord.Member) -> list[str]:
    profile = get_profile(member.id)
    positions = list(profile["main_positions"])
    if not positions:
        positions = parse_main_positions_from_nick(member)
    if not positions:
        positions = get_existing_main_roles_from_member(member)
    return [pos for pos in positions if pos in POSITIONS][:2]


def shorten_lineup_text(text: str, max_len: int = 17) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "."


def center_lineup_row(cells: list[str], width: int = 76) -> str:
    if not cells:
        return ""
    if len(cells) == 1:
        return cells[0].center(width).rstrip()
    gap = max(2, (width - sum(len(cell) for cell in cells)) // (len(cells) - 1))
    return (" " * gap).join(cells).center(width).rstrip()


def get_poll_start_at(poll_row):
    try:
        start_at = datetime.fromisoformat(poll_row["start_at"])
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=BOT_TZ)
        return start_at
    except ValueError:
        return datetime.now(BOT_TZ)


def get_vote_available_at(vote, start_at: datetime):
    if vote["response"] != "later" or not vote["later_time"]:
        return start_at

    parsed = parse_hhmm(vote["later_time"])
    if parsed is None:
        return start_at

    hour, minute = parsed
    available_at = datetime(
        year=start_at.year,
        month=start_at.month,
        day=start_at.day,
        hour=hour,
        minute=minute,
        tzinfo=BOT_TZ,
    )
    if available_at < start_at:
        return start_at
    return available_at


LINEUP_FORMATIONS = {
    "4-3-3 offensiv": {
        "template": "433_offensiv.png",
        "rows": [
            [("LF", "LW", 162, 198), ("ST", "ST", 438, 178), ("RF", "RW", 710, 198)],
            [("ZM", "CM", 208, 394), ("ZOM", "CAM", 438, 374), ("ZM", "CM", 667, 394)],
            [("LV", "LB", 89, 563), ("IV", "CB", 252, 588), ("IV", "CB", 622, 588), ("RV", "RB", 784, 563)],
            [("TW", "GK", 438, 626)],
        ],
    },
    "4-1-2-1-2": {
        "template": "41212_2.png",
        "rows": [
            [("ST", "ST", 286, 181), ("ST", "ST", 586, 181)],
            [("ZOM", "CAM", 435, 256)],
            [("ZM", "CM", 178, 340), ("ZM", "CM", 693, 340)],
            [("ZDM", "CDM", 435, 449)],
            [("LV", "LB", 96, 511), ("IV", "CB", 289, 521), ("IV", "CB", 576, 521), ("RV", "RB", 770, 511)],
            [("TW", "GK", 435, 644)],
        ],
    },
    "3-4-3": {
        "template": "343.png",
        "rows": [
            [("LF", "LW", 205, 177), ("ST", "ST", 453, 177), ("RF", "RW", 701, 177)],
            [("LV", "LM", 88, 327), ("ZM", "CM", 304, 359), ("ZM", "CM", 599, 359), ("RV", "RM", 818, 327)],
            [("IV", "CB", 184, 490), ("IV", "CB", 453, 490), ("IV", "CB", 722, 490)],
            [("TW", "GK", 453, 650)],
        ],
    },
    "4-2-3-1 (2)": {
        "template": "4231_2.png",
        "rows": [
            [("ST", "ST", 453, 171)],
            [("LF", "LM", 89, 326), ("ZOM", "CAM", 453, 377), ("RF", "RM", 813, 326)],
            [("ZDM", "CDM", 261, 376), ("ZDM", "CDM", 623, 376)],
            [("LV", "LB", 81, 555), ("IV", "CB", 296, 579), ("IV", "CB", 594, 579), ("RV", "RB", 782, 555)],
            [("TW", "GK", 453, 649)],
        ],
    },
}


def flatten_lineup_slots(rows):
    return [slot for row in rows for slot, _, _, _ in row]


def assign_lineup_for_formation(players: list[dict], rows: list[list[str]]):
    slots = flatten_lineup_slots(rows)
    memo = {}

    def better(candidate, current):
        if current is None:
            return candidate
        return candidate if candidate[:3] > current[:3] else current

    def solve(slot_index: int, used_mask: int):
        key = (slot_index, used_mask)
        if key in memo:
            return memo[key]

        if slot_index >= len(slots):
            return (0, 0, 0, ())

        slot_pos = slots[slot_index]
        best = solve(slot_index + 1, used_mask)
        best = (best[0], best[1], best[2], (None,) + best[3])

        for player_index, player in enumerate(players):
            if used_mask & (1 << player_index):
                continue
            if slot_pos not in player["positions"]:
                continue

            rest = solve(slot_index + 1, used_mask | (1 << player_index))
            candidate = (
                rest[0] + 1,
                rest[1] - int(player["available_at"].timestamp()),
                rest[2] - player["vote_index"],
                (player_index,) + rest[3],
            )
            best = better(candidate, best)

        memo[key] = best
        return best

    score = solve(0, 0)
    assigned = []
    used_ids = set()
    slot_index = 0

    for row in rows:
        assigned_row = []
        for slot_pos, label, x, y in row:
            player_index = score[3][slot_index] if slot_index < len(score[3]) else None
            player = players[player_index] if player_index is not None else None
            if player is not None:
                used_ids.add(player["member"].id)
            assigned_row.append((slot_pos, label, x, y, player))
            slot_index += 1
        assigned.append(assigned_row)

    bench = [player for player in players if player["member"].id not in used_ids]
    return score, assigned, bench


def load_lineup_font(size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def fit_lineup_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int = 19, min_size: int = 11):
    for size in range(start_size, min_size - 1, -1):
        font = load_lineup_font(size, bold=True)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=2)
        if bbox[2] - bbox[0] <= max_width:
            return font
    return load_lineup_font(min_size, bold=True)


def lineup_name_y(label_y: int, image_height: int) -> int:
    if label_y > image_height - 55:
        return label_y - 58
    return label_y - 48


def draw_centered_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill, stroke_fill=(0, 0, 0), stroke_width: int = 2):
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    text_width = bbox[2] - bbox[0]
    draw.text(
        (x - text_width / 2, y),
        text,
        font=font,
        fill=fill,
        stroke_fill=stroke_fill,
        stroke_width=stroke_width,
    )


PLAYER_CARD_FILES = {
    "a4gprophet": "A4G_Prophet.png",
    "prophet": "A4G_Prophet.png",
    "andi": "Andi.png",
    "calybost": "Calybost.png",
    "calypost": "Calybost.png",
    "chabbes": "Chabbes.png",
    "chrisi kongstrong": "Chrisi_Kongstrong.png",
    "chrisikongstrong": "Chrisi_Kongstrong.png",
    "eviln8ghtmare": "Eviln8ghtmare.png",
    "ibri": "Ibri.png",
    "ibrakadabra": "Ibri.png",
    "ibrakadabra1900": "Ibri.png",
    "patbfg": "Pat_BFG.png",
    "pat bfg": "Pat_BFG.png",
    "zeropainter": "ZERO_PAINTER.png",
    "zero painter": "ZERO_PAINTER.png",
}

DISPLAY_NAME_ALIASES = {
    "calypost": "Calybost",
}


def normalize_player_name(name: str) -> str:
    name = re.sub(r"\([^)]*\)", "", name).lower()
    name = re.sub(r"[^a-z0-9]+", "", name)
    return name


def display_lineup_name(name: str) -> str:
    suffix_match = re.search(r"\s*(\([^)]*\))\s*$", name)
    suffix = f" {suffix_match.group(1)}" if suffix_match else ""
    base = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    normalized = normalize_player_name(base)
    return f"{DISPLAY_NAME_ALIASES.get(normalized, base)}{suffix}".strip()


def get_player_card_path(name: str):
    normalized = normalize_player_name(name)
    filename = PLAYER_CARD_FILES.get(normalized)
    if filename is None:
        return None
    path = PLAYER_CARD_DIR / filename
    return path if path.exists() else None


def paste_player_card(base: Image.Image, card_path: Path, x: int, label_y: int):
    card = Image.open(card_path).convert("RGBA")
    target_h = 142
    target_w = round(card.width * (target_h / card.height))
    card = card.resize((target_w, target_h), Image.Resampling.LANCZOS)

    left = round(x - target_w / 2)
    top = round(label_y - target_h - 13)
    left = max(0, min(left, base.width - target_w))
    top = max(0, min(top, base.height - target_h))
    base.alpha_composite(card, (left, top))


def draw_lineup_name_badge(draw: ImageDraw.ImageDraw, x: int, y: int, name: str, image_width: int):
    font = fit_lineup_font(draw, name, max_width=96, start_size=13, min_size=9)
    bbox = draw.textbbox((0, 0), name, font=font, stroke_width=1)
    width = bbox[2] - bbox[0] + 10
    height = bbox[3] - bbox[1] + 7
    left = max(4, min(round(x - width / 2), image_width - width - 4))
    top = max(4, y)
    draw.rounded_rectangle((left, top, left + width, top + height), radius=7, fill=(0, 0, 0, 185))
    draw.text((left + 5, top + 2), name, font=font, fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke_width=1)


def build_lineup_image(guild: discord.Guild, poll_row, votes_rows):
    start_at = get_poll_start_at(poll_row)
    available = []
    for vote_index, vote in enumerate(votes_rows):
        if vote["response"] not in ("yes", "later"):
            continue
        member = guild.get_member(vote["user_id"])
        if member is None:
            continue
        positions = get_main_positions_for_member(member)
        if not positions:
            continue
        name = clean_name_for_availability(member)
        available_at = get_vote_available_at(vote, start_at)
        if vote["response"] == "later" and vote["later_time"]:
            name = f"{name} ({vote['later_time']})"
        name = display_lineup_name(name)
        available.append(
            {
                "member": member,
                "name": name,
                "positions": positions,
                "available_at": available_at,
                "vote_index": vote_index,
            }
        )

    available.sort(key=lambda player: (player["available_at"], player["vote_index"], player["name"].lower()))

    best_name = None
    best_score = None
    best_assigned = None
    best_bench = None

    for formation_name, config in LINEUP_FORMATIONS.items():
        score, assigned, bench = assign_lineup_for_formation(available, config["rows"])
        if best_score is None or score[:3] > best_score[:3]:
            best_name = formation_name
            best_score = score
            best_assigned = assigned
            best_bench = bench

    starters = []
    bench = best_bench or []
    config = LINEUP_FORMATIONS[best_name]
    template_path = LINEUP_TEMPLATE_DIR / config["template"]

    image = Image.open(template_path).convert("RGBA")
    output = Image.new("RGBA", (image.width, image.height + 82), (22, 24, 28, 255))
    output.alpha_composite(image, (0, 0))
    draw = ImageDraw.Draw(output)

    title_font = load_lineup_font(28, bold=True)
    info_font = load_lineup_font(18, bold=True)

    for row in best_assigned or []:
        for slot_pos, label, x, y, player in row:
            name = "BOT" if player is None else player["name"]
            if player is not None:
                starters.append((slot_pos, player))
                card_path = get_player_card_path(name)
                if card_path is not None:
                    paste_player_card(output, card_path, x, y)
            draw_lineup_name_badge(draw, x, lineup_name_y(y, image.height), name, image.width)

    footer_y = image.height + 10
    draw.text((22, footer_y), f"{poll_row['title']} - {best_name}", font=title_font, fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke_width=2)
    draw.text(
        (22, footer_y + 38),
        f"{poll_row['weekday_text']}, {poll_row['date_text']} | {poll_row['time_text']} | freie Plätze: BOT",
        font=info_font,
        fill=(220, 235, 220),
        stroke_fill=(0, 0, 0),
        stroke_width=2,
    )

    buffer = io.BytesIO()
    output.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)

    bench_lines = []
    for player in sorted(bench, key=lambda p: (p["available_at"], p["vote_index"], p["name"].lower())):
        pos_text = "/".join(player["positions"]) if player["positions"] else "?"
        bench_lines.append(f"{pos_text} | {player['name']}")

    filename = f"aufstellung_{poll_row['id']}.png"
    file = discord.File(buffer, filename=filename)
    return file, best_name, bench_lines


def manual_slot_key_map(formation_name: str):
    config = LINEUP_FORMATIONS[formation_name]
    key_map = {}
    label_counts = {}
    role_counts = {}

    for row in config["rows"]:
        for slot in row:
            slot_pos, label, _, _ = slot
            label_key = normalize_player_name(label)
            role_key = normalize_player_name(slot_pos)
            label_counts[label_key] = label_counts.get(label_key, 0) + 1
            role_counts[role_key] = role_counts.get(role_key, 0) + 1
            label_index = label_counts[label_key]
            role_index = role_counts[role_key]

            key_map[f"{label_key}{label_index}"] = slot
            key_map[f"{role_key}{role_index}"] = slot
            if label_index == 1:
                key_map[label_key] = slot
            if role_index == 1:
                key_map[role_key] = slot

    return key_map


def parse_manual_assignments(formation_name: str, raw_text: str):
    key_map = manual_slot_key_map(formation_name)
    assignments = {}
    errors = []

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = re.match(r"^([A-Za-z0-9ÄÖÜäöüß _-]+)\s*[:=\-]\s*(.+)$", line)
        if not match:
            errors.append(line)
            continue

        raw_key, raw_name = match.group(1).strip(), match.group(2).strip()
        key = normalize_player_name(raw_key)
        slot = key_map.get(key)
        if slot is None:
            errors.append(line)
            continue

        assignments[slot] = display_lineup_name(raw_name)

    return assignments, errors


def manual_lineup_help(formation_name: str):
    config = LINEUP_FORMATIONS[formation_name]
    labels = []
    counts = {}
    for row in config["rows"]:
        for _, label, _, _ in row:
            counts[label] = counts.get(label, 0) + 1
            labels.append(f"{label}{counts[label]}" if counts[label] > 1 else label)
    return ", ".join(labels)


def build_manual_lineup_image(formation_name: str, title: str, assignments: dict):
    config = LINEUP_FORMATIONS[formation_name]
    template_path = LINEUP_TEMPLATE_DIR / config["template"]

    image = Image.open(template_path).convert("RGBA")
    output = Image.new("RGBA", (image.width, image.height + 82), (22, 24, 28, 255))
    output.alpha_composite(image, (0, 0))
    draw = ImageDraw.Draw(output)

    title_font = load_lineup_font(28, bold=True)
    info_font = load_lineup_font(18, bold=True)

    for row in config["rows"]:
        for slot in row:
            _, _, x, y = slot
            name = assignments.get(slot, "BOT")
            if name != "BOT":
                card_path = get_player_card_path(name)
                if card_path is not None:
                    paste_player_card(output, card_path, x, y)
            draw_lineup_name_badge(draw, x, lineup_name_y(y, image.height), name, image.width)

    footer_y = image.height + 10
    draw.text((22, footer_y), f"{title} - {formation_name}", font=title_font, fill=(255, 255, 255), stroke_fill=(0, 0, 0), stroke_width=2)
    draw.text(
        (22, footer_y + 38),
        "Manuelle Aufstellung | freie Plätze: BOT",
        font=info_font,
        fill=(220, 235, 220),
        stroke_fill=(0, 0, 0),
        stroke_width=2,
    )

    buffer = io.BytesIO()
    output.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="aufstellung_manuell.png")


def count_lineup_available_players(guild: discord.Guild, votes_rows) -> int:
    count = 0
    for vote in votes_rows:
        if vote["response"] not in ("yes", "later"):
            continue
        member = guild.get_member(vote["user_id"])
        if member is None:
            continue
        if get_main_positions_for_member(member):
            count += 1
    return count


async def refresh_poll_message(guild: discord.Guild, poll_id: int, message: discord.Message | None = None):
    poll_row = get_poll_by_id(poll_id)
    if poll_row is None:
        return

    votes_rows = get_votes_for_poll(poll_id)
    embed = build_poll_embed(guild, poll_row, votes_rows)

    if message is None:
        channel = guild.get_channel(poll_row["channel_id"])
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        try:
            message = await channel.fetch_message(poll_row["message_id"])
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    try:
        await message.edit(embed=embed, view=AvailabilityVoteView())
    except discord.HTTPException:
        pass


async def send_yes_voter_reminder_dm(guild: discord.Guild, poll_row, text: str):
    votes = get_votes_for_poll(poll_row["id"])
    available_ids = [row["user_id"] for row in votes if row["response"] in ("yes", "later")]

    for user_id in available_ids:
        member = guild.get_member(user_id)
        if member is None:
            continue
        try:
            await member.send(text)
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass


async def maybe_send_threshold_message(guild: discord.Guild, poll_row):
    if poll_row["yes_threshold_announced"]:
        return

    votes = get_votes_for_poll(poll_row["id"])
    available_count = sum(1 for row in votes if row["response"] in ("yes", "later"))

    if available_count >= 4:
        channel = guild.get_channel(poll_row["channel_id"])
        if channel is None or not isinstance(channel, discord.TextChannel):
            return

        try:
            start_at = datetime.fromisoformat(poll_row["start_at"])
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=BOT_TZ)
        except ValueError:
            start_at = datetime.now(BOT_TZ)

        recommended_time, _ = calculate_recommended_start(votes, start_at)
        extra = f"\n⏰ Empfohlene Startzeit: **{recommended_time}**" if recommended_time else ""

        try:
            await channel.send(f"✅ Es sind jetzt mindestens **4 verfügbare Spieler** da. Es kann gespielt werden.{extra}")
            mark_poll_threshold_announced(poll_row["id"])
        except discord.HTTPException:
            pass


async def notify_nonvoters(guild: discord.Guild, poll_row):
    votes = get_votes_for_poll(poll_row["id"])
    voted_ids = {row["user_id"] for row in votes}

    managers = [m for m in guild.members if has_role(m, ROLE_MANAGER)]
    finished_members = [m for m in guild.members if not m.bot and has_role(m, ROLE_FINISHED)]

    warned_members = []

    for member in finished_members:
        if member.id in voted_ids:
            reset_missed_vote_count(member.id)
            continue

        missed_count, last_warned_count = increase_missed_vote(member.id)

        if missed_count >= 5:
            warned_members.append(member)

            try:
                await member.send(
                    "⚠️ Du hast **5-mal nicht** bei Verfügbarkeitsabfragen abgestimmt. "
                    "Bitte stimme in Zukunft immer bei Verfügbarkeitsabfragen ab."
                )
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass

            reset_missed_vote_count(member.id)

    if warned_members:
        lines = "\n".join(
            f"- {member.display_name}"
            for member in warned_members
        )

        manager_message = (
            "⚠️ Folgende Spieler haben **5-mal nicht** bei Verfügbarkeitsabfragen abgestimmt:\n\n"
            f"{lines}"
        )

        for manager in managers:
            try:
                await manager.send(manager_message)
            except discord.Forbidden:
                pass
            except discord.HTTPException:
                pass

async def cleanup_expired_poll_message(guild: discord.Guild, poll_row, *, force: bool = False) -> bool:
    channel = guild.get_channel(poll_row["channel_id"])
    if channel is None or not isinstance(channel, discord.TextChannel):
        return False

    if not force:
        latest_message_id = get_latest_poll_message_id_for_channel(channel.id)
        if latest_message_id == poll_row["message_id"]:
            return False

    try:
        msg = await channel.fetch_message(poll_row["message_id"])
    except discord.NotFound:
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False

    try:
        await msg.delete()
        return True
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False


async def create_availability_poll(
    guild: discord.Guild,
    channel: discord.TextChannel,
    kind: str,
    title: str,
    weekday_text: str,
    date_text: str,
    time_text: str,
    start_at: datetime,
    created_by: int | None,
    auto_created: bool,
):
    poll_id = create_poll_record(kind, title, weekday_text, date_text, time_text, start_at, created_by, auto_created=auto_created)
    poll_row = get_poll_by_id(poll_id)
    embed = build_poll_embed(guild, poll_row, [])

    try:
        message = await channel.send(
            content="@everyone Bitte abstimmen.",
            embed=embed,
            view=AvailabilityVoteView(),
            allowed_mentions=discord.AllowedMentions(everyone=True),
        )
    except discord.HTTPException:
        return

    set_poll_message_info(poll_id, channel.id, message.id)


async def maybe_create_daily_funclub_poll():
    if not is_auto_availability_enabled():
        return

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    now = datetime.now(BOT_TZ)
    if now.hour != 12:
        return

    date_text = now.strftime("%d.%m.%Y")
    if daily_poll_exists_for_date(date_text):
        return

    channel = discord.utils.get(guild.text_channels, name=CH_AVAILABILITY)
    if channel is None:
        return

    start_at = datetime(now.year, now.month, now.day, 18, 0, tzinfo=BOT_TZ)
    weekday_text = get_weekday_text(now)

    await create_availability_poll(
        guild=guild,
        channel=channel,
        kind="daily_funclub",
        title="Funclubben",
        weekday_text=weekday_text,
        date_text=date_text,
        time_text="18:00 - 22:00",
        start_at=start_at,
        created_by=None,
        auto_created=True,
    )


async def process_poll_reminders():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    now = datetime.now(BOT_TZ)

    for poll_row in get_open_polls():
        try:
            start_at = datetime.fromisoformat(poll_row["start_at"])
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=BOT_TZ)
        except ValueError:
            continue

        await maybe_send_threshold_message(guild, poll_row)

        diff = start_at - now

        if not poll_row["remind_60_sent"] and timedelta(minutes=0) < diff <= timedelta(hours=1):
            await send_yes_voter_reminder_dm(
                guild,
                poll_row,
                f"⏳ **{poll_row['title']}** geht in ungefähr **1 Stunde** los.\n{POLL_NOTE}",
            )
            mark_poll_reminder_60(poll_row["id"])

        if not poll_row["remind_5_sent"] and timedelta(minutes=0) < diff <= timedelta(minutes=5):
            await send_yes_voter_reminder_dm(
                guild,
                poll_row,
                f"🚨 **{poll_row['title']}** geht in ungefähr **5 Minuten** los.\n{POLL_NOTE}",
            )
            mark_poll_reminder_5(poll_row["id"])

        if diff <= timedelta(minutes=0):
            await notify_nonvoters(guild, poll_row)
            close_poll(poll_row["id"])

    for poll_row in get_polls_pending_cleanup():
        try:
            start_at = datetime.fromisoformat(poll_row["start_at"])
            if start_at.tzinfo is None:
                start_at = start_at.replace(tzinfo=BOT_TZ)
        except ValueError:
            continue

        if now >= start_at + timedelta(hours=8):
            if not poll_row["closed"]:
                close_poll(poll_row["id"])

            deleted = await cleanup_expired_poll_message(guild, poll_row, force=True)
            if deleted:
                mark_poll_cleanup_deleted(poll_row["id"])


async def send_botlog_message(guild: discord.Guild, text: str):
    channel = discord.utils.get(guild.text_channels, name="botlog")
    if channel is None:
        return
    try:
        await channel.send(text)
    except discord.HTTPException:
        pass


async def get_match_report_channel(guild: discord.Guild):
    configured_channel_id = get_setting("ea_stats_channel_id")
    if configured_channel_id and configured_channel_id.isdigit():
        channel = guild.get_channel(int(configured_channel_id))
        if isinstance(channel, discord.TextChannel):
            return channel

    channel = discord.utils.get(guild.text_channels, name=CH_MATCH_REPORTS)
    if isinstance(channel, discord.TextChannel):
        return channel
    return discord.utils.get(guild.text_channels, name="botlog")


async def post_ea_match_report(guild: discord.Guild, match: dict, match_type: str, club_id: str):
    channel = await get_match_report_channel(guild)
    if not isinstance(channel, discord.TextChannel):
        return

    embed = build_match_report_embed(match, club_id, match_type)
    try:
        message = await channel.send(embed=embed, view=MatchStatsView())
    except discord.HTTPException as exc:
        await send_botlog_message(guild, f"Stats-Post konnte nicht gesendet werden: `{exc}`")
        return

    save_ea_match(str(match.get("matchId")), match_type, channel.id, message.id, match)


async def check_ea_matches(*, post_new: bool = True, mark_existing: bool = False) -> int:
    config = get_configured_ea_club()
    if config is None:
        return 0

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return 0

    posted = 0
    for match_type in EA_MATCH_TYPES:
        try:
            matches = await ea_fetch_matches(config["club_id"], config["platform"], match_type)
        except EAStatsError as exc:
            await send_botlog_message(guild, f"EA-Stats konnten nicht geladen werden ({match_type_label(match_type)}): `{exc}`")
            continue

        if not isinstance(matches, list):
            continue

        for match in sorted(matches, key=lambda item: int_stat(item, "timestamp")):
            match_id = str(match.get("matchId", ""))
            if not match_id or ea_match_exists(match_id, match_type):
                continue

            if mark_existing or not post_new:
                mark_ea_match_seen(match_id, match_type, match)
                posted += 1
                continue

            await post_ea_match_report(guild, match, match_type, config["club_id"])
            posted += 1

    return posted


async def background_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await maybe_create_daily_funclub_poll()
            await process_poll_reminders()
            await check_ea_matches()
        except Exception as e:
            print(f"Fehler im Hintergrundloop: {e}")
        await asyncio.sleep(60)


class RulesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Regeln akzeptieren", style=discord.ButtonStyle.success, custom_id="vcr8:rules:accept")
    async def accept_rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        tester_role = get_role_by_name(interaction.guild, ROLE_TESTER)
        if tester_role is None:
            await interaction.response.send_message("Die Rolle **Tester** existiert nicht.", ephemeral=True)
            return

        if tester_role not in interaction.user.roles:
            try:
                await interaction.user.add_roles(tester_role, reason="Regeln akzeptiert")
            except discord.Forbidden:
                await interaction.response.send_message("Ich darf die Rolle **Tester** nicht vergeben.", ephemeral=True)
                return
            except discord.HTTPException:
                await interaction.response.send_message("Fehler beim Vergeben der Rolle **Tester**.", ephemeral=True)
                return

        fresh_member = await get_fresh_member(interaction.user)
        await update_member_profile(fresh_member)
        fresh_member = await get_fresh_member(fresh_member)

        await interaction.response.send_message(
            f"Regeln akzeptiert.\n\n{next_step_message(fresh_member)}",
            ephemeral=True,
        )
        await send_private_progress_dm(fresh_member, "Du hast die Regeln akzeptiert.")


class MainPositionSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=pos, value=pos) for pos in POSITIONS]
        super().__init__(
            placeholder="Wähle 1 bis 2 Hauptpositionen",
            min_values=1,
            max_values=2,
            options=options,
            custom_id="vcr8:main_positions:select",
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            return
        if not can_use_profile_system(interaction.user):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Tester**.", ephemeral=True)
            return

        selected = list(self.values)
        profile = get_profile(interaction.user.id)
        side_positions = [pos for pos in profile["side_positions"] if pos not in selected]

        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey=profile["jersey"],
            main_positions=selected,
            side_positions=side_positions,
        )

        fresh_member = await get_fresh_member(interaction.user)
        await update_member_profile(fresh_member, rebuild=False)
        fresh_member = await get_fresh_member(fresh_member)

        await interaction.response.send_message(
            f"Deine Hauptpositionen wurden gesetzt: **{', '.join(selected)}**\n\n{next_step_message(fresh_member)}",
            ephemeral=True,
        )
        await send_private_progress_dm(fresh_member, "Deine Hauptpositionen wurden gespeichert.")


class MainPositionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MainPositionSelect())

    @discord.ui.button(label="Hauptpositionen resetten", style=discord.ButtonStyle.danger, custom_id="vcr8:main_positions:reset")
    async def reset_main_positions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return
        if not can_use_profile_system(interaction.user):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Tester**.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        profile = get_profile(interaction.user.id)
        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey=profile["jersey"],
            main_positions=[],
            side_positions=profile["side_positions"],
        )

        fresh_member = await get_fresh_member(interaction.user)
        await update_member_profile(fresh_member, rebuild=False)
        fresh_member = await get_fresh_member(fresh_member)

        await interaction.followup.send(
            f"Deine Hauptpositionen wurden zurückgesetzt.\n\n{next_step_message(fresh_member)}",
            ephemeral=True,
        )
        await send_private_progress_dm(fresh_member, "Deine Hauptpositionen wurden zurückgesetzt.")


class SidePositionSelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=pos, value=pos) for pos in POSITIONS]
        super().__init__(
            placeholder="Wähle beliebig viele Nebenpositionen",
            min_values=0,
            max_values=len(POSITIONS),
            options=options,
            custom_id="vcr8:side_positions:select",
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            return
        if not can_use_profile_system(interaction.user):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Tester**.", ephemeral=True)
            return

        selected = list(self.values)
        profile = get_profile(interaction.user.id)
        selected = [pos for pos in selected if pos not in profile["main_positions"]]

        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey=profile["jersey"],
            main_positions=profile["main_positions"],
            side_positions=selected,
        )

        fresh_member = await get_fresh_member(interaction.user)
        await update_member_profile(fresh_member, rebuild=False)
        fresh_member = await get_fresh_member(fresh_member)

        text = ", ".join(selected) if selected else "keine"
        await interaction.response.send_message(
            f"Deine Nebenpositionen wurden gesetzt: **{text}**\n\n{next_step_message(fresh_member)}",
            ephemeral=True,
        )
        await send_private_progress_dm(fresh_member, "Deine Nebenpositionen wurden gespeichert.")


class SidePositionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(SidePositionSelect())

    @discord.ui.button(label="Nebenpositionen resetten", style=discord.ButtonStyle.danger, custom_id="vcr8:side_positions:reset")
    async def reset_side_positions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return
        if not can_use_profile_system(interaction.user):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Tester**.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        profile = get_profile(interaction.user.id)
        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey=profile["jersey"],
            main_positions=profile["main_positions"],
            side_positions=[],
        )

        fresh_member = await get_fresh_member(interaction.user)
        await update_member_profile(fresh_member, rebuild=False)
        fresh_member = await get_fresh_member(fresh_member)

        await interaction.followup.send(
            f"Deine Nebenpositionen wurden zurückgesetzt.\n\n{next_step_message(fresh_member)}",
            ephemeral=True,
        )
        await send_private_progress_dm(fresh_member, "Deine Nebenpositionen wurden zurückgesetzt.")


class NumberModal(discord.ui.Modal, title="Trikotnummer setzen"):
    trikotnummer = discord.ui.TextInput(
        label="Trikotnummer (1-99)",
        placeholder="z.B. 8",
        required=True,
        max_length=2,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            return
        if not can_use_profile_system(interaction.user):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Tester**.", ephemeral=True)
            return

        raw = self.trikotnummer.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("Bitte nur Zahlen eingeben.", ephemeral=True)
            return

        num = int(raw)
        if num < 1 or num > 99:
            await interaction.response.send_message("Bitte eine Nummer zwischen 1 und 99 eingeben.", ephemeral=True)
            return

        jersey = str(num)
        existing = get_profile_by_jersey(jersey, exclude_user_id=interaction.user.id)
        if existing is not None:
            await interaction.response.send_message(
                f"Die Trikotnummer **#{jersey}** ist schon vergeben. Bitte nimm eine andere Nummer.",
                ephemeral=True,
            )
            return

        profile = get_profile(interaction.user.id)

        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey=jersey,
            main_positions=profile["main_positions"],
            side_positions=profile["side_positions"],
        )

        fresh_member = await get_fresh_member(interaction.user)
        await update_member_profile(fresh_member, rebuild=False)
        fresh_member = await get_fresh_member(fresh_member)

        await interaction.response.send_message(
            f"Deine Trikotnummer wurde auf **#{raw}** gesetzt.\n\n{next_step_message(fresh_member)}",
            ephemeral=True,
        )
        await send_private_progress_dm(fresh_member, "Deine Trikotnummer wurde gespeichert.")


class NumberView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Trikotnummer setzen", style=discord.ButtonStyle.primary, custom_id="vcr8:number:open_modal")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(NumberModal())
        except discord.NotFound:
            pass

    @discord.ui.button(label="Trikotnummer resetten", style=discord.ButtonStyle.danger, custom_id="vcr8:number:reset")
    async def reset_number(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return
        if not can_use_profile_system(interaction.user):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Tester**.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        profile = get_profile(interaction.user.id)
        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey="",
            main_positions=profile["main_positions"],
            side_positions=profile["side_positions"],
        )

        fresh_member = await get_fresh_member(interaction.user)
        await update_member_profile(fresh_member, rebuild=False)
        fresh_member = await get_fresh_member(fresh_member)

        await interaction.followup.send(
            f"Deine Trikotnummer wurde zurückgesetzt.\n\n{next_step_message(fresh_member)}",
            ephemeral=True,
        )
        await send_private_progress_dm(fresh_member, "Deine Trikotnummer wurde zurückgesetzt.")


class LaterModal(discord.ui.Modal, title="Wann kommst du später dazu?"):
    later_time = discord.ui.TextInput(
        label="Uhrzeit (HH:MM)",
        placeholder="z.B. 19:30",
        required=True,
        max_length=5,
    )

    def __init__(self, poll_id: int):
        super().__init__()
        self.poll_id = poll_id

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_role(interaction.user, ROLE_FINISHED):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Fertig**.", ephemeral=True)
            return

        raw = self.later_time.value.strip()
        if parse_hhmm(raw) is None:
            await interaction.response.send_message("Bitte die Uhrzeit als **HH:MM** eingeben, z. B. **19:30**.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        poll_row = get_poll_by_id(self.poll_id)
        if poll_is_closed(poll_row):
            await interaction.followup.send("Diese Abstimmung ist bereits geschlossen.", ephemeral=True)
            return

        upsert_vote(self.poll_id, interaction.user.id, "later", later_time=raw)

        if poll_row is not None:
            await refresh_poll_message(interaction.guild, self.poll_id)
            await maybe_send_threshold_message(interaction.guild, poll_row)

        await interaction.followup.send("Du wurdest als **Komme später** eingetragen.", ephemeral=True)


class PlayerStatsSelect(discord.ui.Select):
    def __init__(self, match_row: dict):
        self.match_row = match_row
        snapshot = match_row["snapshot"]
        club_id = get_setting("ea_club_id", "")
        players = get_match_players(snapshot, club_id)[:25]
        options = [
            discord.SelectOption(
                label=player["playername"][:100],
                value=player["player_id"],
                description=(
                    f"{float_stat(player, 'rating'):.1f} Rating | "
                    f"{int_stat(player, 'goals')}T/{int_stat(player, 'assists')}A"
                )[:100],
            )
            for player in players
        ]
        if not options:
            options = [discord.SelectOption(label="Keine Spieler gefunden", value="none")]
        super().__init__(
            placeholder="Spieler auswählen",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("Für dieses Match wurden keine Spieler gefunden.", ephemeral=True)
            return
        try:
            embed = build_player_stats_embed(
                self.match_row["snapshot"],
                get_setting("ea_club_id", ""),
                self.values[0],
            )
        except EAStatsError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(embed=embed, ephemeral=True)


class PlayerStatsSelectView(discord.ui.View):
    def __init__(self, match_row: dict):
        super().__init__(timeout=180)
        self.add_item(PlayerStatsSelect(match_row))


class MatchStatsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Spieler-Stats anzeigen", style=discord.ButtonStyle.primary, custom_id="vcr8:match_stats:players")
    async def show_players(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.message is None:
            await interaction.response.send_message("Match-Nachricht wurde nicht gefunden.", ephemeral=True)
            return

        match_row = get_ea_match_by_message_id(interaction.message.id)
        if match_row is None:
            await interaction.response.send_message("Für diesen Spielbericht sind keine gespeicherten Matchdaten vorhanden.", ephemeral=True)
            return

        await interaction.response.send_message(
            "Wähle einen Spieler aus diesem Match:",
            view=PlayerStatsSelectView(match_row),
            ephemeral=True,
        )


class AvailabilityVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Ja", style=discord.ButtonStyle.success, custom_id="vcr8:availability:yes")
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_role(interaction.user, ROLE_FINISHED):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Fertig**.", ephemeral=True)
            return

        poll_row = get_poll_by_message_id(interaction.message.id)
        if poll_row is None:
            await interaction.response.send_message("Diese Abstimmung wurde nicht gefunden.", ephemeral=True)
            return
        if poll_is_closed(poll_row):
            await interaction.response.send_message("Diese Abstimmung ist bereits geschlossen.", ephemeral=True)
            return

        upsert_vote(poll_row["id"], interaction.user.id, "yes", later_time=None)

        await interaction.response.defer(ephemeral=True)
        await refresh_poll_message(interaction.guild, poll_row["id"], interaction.message)
        await maybe_send_threshold_message(interaction.guild, poll_row)
        await interaction.followup.send("Deine Stimme wurde auf **Ja** gesetzt.", ephemeral=True)

    @discord.ui.button(label="🕒 Komme später", style=discord.ButtonStyle.primary, custom_id="vcr8:availability:later")
    async def later_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_role(interaction.user, ROLE_FINISHED):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Fertig**.", ephemeral=True)
            return

        poll_row = get_poll_by_message_id(interaction.message.id)
        if poll_row is None:
            await interaction.response.send_message("Diese Abstimmung wurde nicht gefunden.", ephemeral=True)
            return
        if poll_is_closed(poll_row):
            await interaction.response.send_message("Diese Abstimmung ist bereits geschlossen.", ephemeral=True)
            return

        await interaction.response.send_modal(LaterModal(poll_row["id"]))

    @discord.ui.button(label="❌ Nein", style=discord.ButtonStyle.danger, custom_id="vcr8:availability:no")
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_role(interaction.user, ROLE_FINISHED):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Fertig**.", ephemeral=True)
            return

        poll_row = get_poll_by_message_id(interaction.message.id)
        if poll_row is None:
            await interaction.response.send_message("Diese Abstimmung wurde nicht gefunden.", ephemeral=True)
            return
        if poll_is_closed(poll_row):
            await interaction.response.send_message("Diese Abstimmung ist bereits geschlossen.", ephemeral=True)
            return

        upsert_vote(poll_row["id"], interaction.user.id, "no", later_time=None)

        await interaction.response.defer(ephemeral=True)
        await refresh_poll_message(interaction.guild, poll_row["id"], interaction.message)
        await interaction.followup.send("Deine Stimme wurde auf **Nein** gesetzt.", ephemeral=True)

    @discord.ui.button(label="🗑️ Stimme entfernen", style=discord.ButtonStyle.secondary, custom_id="vcr8:availability:remove")
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_role(interaction.user, ROLE_FINISHED):
            await interaction.response.send_message("Du brauchst dafür die Rolle **Fertig**.", ephemeral=True)
            return

        poll_row = get_poll_by_message_id(interaction.message.id)
        if poll_row is None:
            await interaction.response.send_message("Diese Abstimmung wurde nicht gefunden.", ephemeral=True)
            return
        if poll_is_closed(poll_row):
            await interaction.response.send_message("Diese Abstimmung ist bereits geschlossen.", ephemeral=True)
            return

        delete_vote(poll_row["id"], interaction.user.id)

        await interaction.response.defer(ephemeral=True)
        await refresh_poll_message(interaction.guild, poll_row["id"], interaction.message)
        await interaction.followup.send("Deine Stimme wurde entfernt. Du kannst jetzt wieder neu abstimmen.", ephemeral=True)


class ManualLineupModal(discord.ui.Modal):
    def __init__(self, formation_name: str, lineup_title: str):
        super().__init__(title="Manuelle Aufstellung")
        self.formation_name = formation_name
        self.lineup_title = lineup_title

        self.attack = discord.ui.TextInput(
            label="Angriff",
            placeholder="Beispiel:\nST=Calybost\nLW=Ibri\nRW=Eviln8ghtmare",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=600,
        )
        self.midfield = discord.ui.TextInput(
            label="Mittelfeld",
            placeholder="Beispiel:\nCAM=Pat_BFG\nCM1=Chabbes\nCM2=Chrisi Kongstrong\nCDM=ZERO_PAINTER",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=800,
        )
        self.defense = discord.ui.TextInput(
            label="Abwehr",
            placeholder="Beispiel:\nLB=Andi\nCB1=A4G_Prophet\nCB2=Chabbes\nRB=ZERO_PAINTER",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=800,
        )
        self.goalkeeper = discord.ui.TextInput(
            label="Torwart",
            placeholder="Beispiel:\nGK=BOT",
            style=discord.TextStyle.short,
            required=False,
            max_length=120,
        )

        self.add_item(self.attack)
        self.add_item(self.midfield)
        self.add_item(self.defense)
        self.add_item(self.goalkeeper)

    async def on_submit(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            return
        if not is_manager(interaction.user):
            await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
            return

        raw_text = "\n".join(
            [
                self.attack.value,
                self.midfield.value,
                self.defense.value,
                self.goalkeeper.value,
            ]
        )
        assignments, errors = parse_manual_assignments(self.formation_name, raw_text)
        if errors:
            await interaction.response.send_message(
                "Diese Zeilen konnte ich nicht zuordnen:\n"
                + "\n".join(f"- {line}" for line in errors[:8])
                + f"\n\nErlaubte Positionsschlüssel: {manual_lineup_help(self.formation_name)}",
                ephemeral=True,
            )
            return

        file = build_manual_lineup_image(self.formation_name, self.lineup_title, assignments)
        await interaction.response.send_message(
            content=f"**Manuelle Aufstellung:** {self.lineup_title} | **Formation:** {self.formation_name}",
            file=file,
        )

        try:
            await interaction.message.delete()
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            pass


class ManualLineupView(discord.ui.View):
    def __init__(self, formation_name: str, lineup_title: str):
        super().__init__(timeout=900)
        self.formation_name = formation_name
        self.lineup_title = lineup_title

    @discord.ui.button(label="Spieler eintragen", style=discord.ButtonStyle.primary, custom_id="vcr8:manual_lineup:edit")
    async def edit_lineup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return
        if not is_manager(interaction.user):
            await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
            return
        await interaction.response.send_modal(ManualLineupModal(self.formation_name, self.lineup_title))


intents = discord.Intents.default()
intents.members = True


class VollpfostenBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.background_task = None

    async def setup_hook(self):
        init_db()
        migrate_db()

        self.add_view(RulesView())
        self.add_view(MainPositionView())
        self.add_view(SidePositionView())
        self.add_view(NumberView())
        self.add_view(AvailabilityVoteView())
        self.add_view(MatchStatsView())

        guild_obj = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild_obj)
        synced = await self.tree.sync(guild=guild_obj)

        print(f"Sync für Guild {GUILD_ID}: {len(synced)} Commands")
        for cmd in synced:
            print(f"- /{cmd.name}")

        if self.background_task is None:
            self.background_task = asyncio.create_task(background_loop())


bot = VollpfostenBot()


@bot.event
async def on_ready():
    print(f"Eingeloggt als {bot.user} ({bot.user.id})")
    for g in bot.guilds:
        print(f"Server: {g.name} | ID: {g.id}")
    try:
        await bot.user.edit(username=SERVER_NAME[:32])
    except Exception:
        pass


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    await ensure_base_name(member)
    await rebuild_profile_from_server_state(member)
    await send_join_dm(member)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.guild.id != GUILD_ID:
        return
    if is_profile_rebuild_suppressed(after.id):
        return

    before_roles = {r.id for r in before.roles}
    after_roles = {r.id for r in after.roles}

    if before_roles != after_roles:
        await update_member_profile(after)


@app_commands.describe(
    titel="Name der Abstimmung",
    datum="Datum im Format TT.MM.JJJJ",
    uhrzeit="Uhrzeit im Format HH:MM"
)
@bot.tree.command(name="verfuegbarkeit", description="Erstellt eine Verfügbarkeitsabfrage")
async def verfuegbarkeit(
    interaction: discord.Interaction,
    titel: str,
    datum: str,
    uhrzeit: str,
):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user):
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        return

    channel = discord.utils.get(guild.text_channels, name=CH_AVAILABILITY)
    if channel is None:
        await interaction.response.send_message("Der Kanal **verfuegbarkeit** existiert nicht.", ephemeral=True)
        return

    start_at = parse_manual_start_datetime(datum, uhrzeit)
    if start_at is None:
        await interaction.response.send_message(
            "Bitte Datum als **TT.MM.JJJJ** und Uhrzeit als **HH:MM** angeben.",
            ephemeral=True,
        )
        return

    weekday_text = get_weekday_text(start_at)
    date_text = start_at.strftime("%d.%m.%Y")
    time_text = start_at.strftime("%H:%M")

    await create_availability_poll(
        guild=guild,
        channel=channel,
        kind="manual",
        title=titel,
        weekday_text=weekday_text,
        date_text=date_text,
        time_text=time_text,
        start_at=start_at,
        created_by=interaction.user.id,
        auto_created=False,
    )

    await interaction.response.send_message("Verfügbarkeitsabfrage wurde erstellt.", ephemeral=True)


@bot.tree.command(name="aufstellung", description="Erstellt eine visuelle Aufstellung aus der neuesten Verfügbarkeitsabfrage")
async def aufstellung(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user):
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        return

    availability_channel = discord.utils.get(guild.text_channels, name=CH_AVAILABILITY)
    if availability_channel is None:
        await interaction.response.send_message("Der Kanal **verfuegbarkeit** existiert nicht.", ephemeral=True)
        return

    poll_row = get_latest_open_poll_for_channel(availability_channel.id)
    if poll_row is None:
        await interaction.response.send_message("Es gibt gerade keine offene Verfügbarkeitsabfrage.", ephemeral=True)
        return

    votes_rows = get_votes_for_poll(poll_row["id"])
    available_count = count_lineup_available_players(guild, votes_rows)
    if available_count < MIN_LINEUP_PLAYERS:
        await interaction.response.send_message(
            f"Für eine Aufstellung brauchst du mindestens **{MIN_LINEUP_PLAYERS}** verfügbare Spieler mit Hauptposition. Aktuell sind es **{available_count}**.",
            ephemeral=True,
        )
        return

    file, formation_name, bench_lines = build_lineup_image(guild, poll_row, votes_rows)

    target_channel = discord.utils.get(guild.text_channels, name=CH_LINEUPS)
    if target_channel is None:
        target_channel = interaction.channel

    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message("Ich konnte keinen passenden Textkanal für die Aufstellung finden.", ephemeral=True)
        return

    content = f"**Aufstellung:** {poll_row['title']} | **Formation:** {formation_name}"
    if bench_lines:
        content += "\n**Bank:**\n" + "\n".join(bench_lines[:12])
    else:
        content += "\n**Bank:** -"

    try:
        await target_channel.send(content=content, file=file)
    except discord.Forbidden:
        await interaction.response.send_message("Ich darf in den Aufstellungs-Kanal nicht schreiben.", ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.response.send_message("Fehler beim Erstellen der Aufstellung.", ephemeral=True)
        return

    await interaction.response.send_message(f"Aufstellung wurde in {target_channel.mention} erstellt.", ephemeral=True)


@app_commands.describe(
    formation="Formation für die manuelle Aufstellung",
    titel="Titel der Aufstellung"
)
@app_commands.choices(
    formation=[
        app_commands.Choice(name="4-3-3 offensiv", value="4-3-3 offensiv"),
        app_commands.Choice(name="4-1-2-1-2", value="4-1-2-1-2"),
        app_commands.Choice(name="3-4-3", value="3-4-3"),
        app_commands.Choice(name="4-2-3-1 (2)", value="4-2-3-1 (2)"),
    ]
)
@bot.tree.command(name="aufstellung_manuell", description="Erstellt eine manuell ausfüllbare Aufstellungsvorlage")
async def aufstellung_manuell(
    interaction: discord.Interaction,
    formation: app_commands.Choice[str],
    titel: str = "Manuelle Aufstellung",
):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user):
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        return

    target_channel = discord.utils.get(guild.text_channels, name=CH_LINEUPS)
    if target_channel is None:
        target_channel = interaction.channel

    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message("Ich konnte keinen passenden Textkanal für die Aufstellung finden.", ephemeral=True)
        return

    formation_name = formation.value
    file = build_manual_lineup_image(formation_name, titel, {})
    help_text = manual_lineup_help(formation_name)
    content = (
        f"**Manuelle Aufstellung:** {titel} | **Formation:** {formation_name}\n"
        "Drücke **Spieler eintragen** und fülle die Positionen aus.\n"
        f"Positionsschlüssel: `{help_text}`"
    )

    try:
        await target_channel.send(
            content=content,
            file=file,
            view=ManualLineupView(formation_name, titel),
        )
    except discord.Forbidden:
        await interaction.response.send_message("Ich darf in den Aufstellungs-Kanal nicht schreiben.", ephemeral=True)
        return
    except discord.HTTPException:
        await interaction.response.send_message("Fehler beim Erstellen der manuellen Aufstellung.", ephemeral=True)
        return

    await interaction.response.send_message(f"Manuelle Aufstellung wurde in {target_channel.mention} vorbereitet.", ephemeral=True)


@bot.tree.command(name="auto_verfuegbarkeit_an", description="Schaltet die automatische 12-Uhr-Abfrage ein")
async def auto_verfuegbarkeit_an(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user):
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    set_setting("auto_availability_enabled", "1")
    await interaction.response.send_message("Die automatische Verfügbarkeitsabfrage um **12:00 Uhr** ist jetzt **aktiviert**.", ephemeral=True)


@bot.tree.command(name="auto_verfuegbarkeit_aus", description="Schaltet die automatische 12-Uhr-Abfrage aus")
async def auto_verfuegbarkeit_aus(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user):
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    set_setting("auto_availability_enabled", "0")
    await interaction.response.send_message("Die automatische Verfügbarkeitsabfrage um **12:00 Uhr** ist jetzt **deaktiviert**.", ephemeral=True)


@bot.tree.command(name="clubstats_setup", description="Verbindet den Bot mit eurem EA Clubs Team")
@app_commands.choices(platform=[
    app_commands.Choice(name="PS5 / Xbox Series X|S / PC", value="common-gen5"),
    app_commands.Choice(name="PS4 / Xbox One", value="common-gen4"),
    app_commands.Choice(name="Switch", value="nx"),
])
async def clubstats_setup(
    interaction: discord.Interaction,
    club_name: str,
    platform: app_commands.Choice[str],
    channel: discord.TextChannel | None = None,
):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        results = await ea_search_club(club_name, platform.value)
    except EAStatsError as exc:
        await interaction.followup.send(f"EA-Clubsuche fehlgeschlagen: `{exc}`", ephemeral=True)
        return

    if not results:
        await interaction.followup.send("Ich habe keinen Club mit diesem Namen auf dieser Plattform gefunden.", ephemeral=True)
        return

    selected = next(
        (club for club in results if str(club.get("clubName", "")).lower() == club_name.lower()),
        results[0],
    )
    club_id = str(selected.get("clubId") or selected.get("clubInfo", {}).get("clubId") or "")
    resolved_name = str(selected.get("clubName") or selected.get("clubInfo", {}).get("name") or club_name)
    if not club_id:
        await interaction.followup.send("Der Club wurde gefunden, aber EA hat keine Club-ID geliefert.", ephemeral=True)
        return

    target_channel = channel
    if target_channel is None:
        existing = discord.utils.get(interaction.guild.text_channels, name=CH_MATCH_REPORTS)
        target_channel = existing if isinstance(existing, discord.TextChannel) else interaction.channel
    if not isinstance(target_channel, discord.TextChannel):
        await interaction.followup.send("Bitte gib einen Textkanal für die Spielberichte an.", ephemeral=True)
        return

    set_setting("ea_club_id", club_id)
    set_setting("ea_club_name", resolved_name)
    set_setting("ea_platform", platform.value)
    set_setting("ea_stats_channel_id", str(target_channel.id))

    marked = await check_ea_matches(post_new=False, mark_existing=True)

    await interaction.followup.send(
        (
            f"Clubstats sind eingerichtet.\n\n"
            f"Club: **{resolved_name}** (`{club_id}`)\n"
            f"Plattform: **{EA_PLATFORM_LABELS.get(platform.value, platform.value)}**\n"
            f"Kanal: {target_channel.mention}\n"
            f"Vorhandene letzte Matches wurden als gesehen gespeichert: **{marked}**\n\n"
            "Neue Spiele werden automatisch gepostet."
        ),
        ephemeral=True,
    )


@bot.tree.command(name="clubstats_check", description="Prüft sofort auf neue EA Clubs Spiele")
async def clubstats_check(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    posted = await check_ea_matches()
    await interaction.followup.send(f"Check fertig. Neue Spielberichte gepostet: **{posted}**", ephemeral=True)


@bot.tree.command(name="clubstats_setup_id", description="Verbindet EA Clubs direkt per Club-ID")
@app_commands.choices(platform=[
    app_commands.Choice(name="PS5 / Xbox Series X|S / PC", value="common-gen5"),
    app_commands.Choice(name="PS4 / Xbox One", value="common-gen4"),
    app_commands.Choice(name="Switch", value="nx"),
])
async def clubstats_setup_id(
    interaction: discord.Interaction,
    club_id: str,
    club_name: str,
    platform: app_commands.Choice[str],
    channel: discord.TextChannel | None = None,
):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    if not club_id.isdigit():
        await interaction.response.send_message("Die Club-ID muss nur aus Zahlen bestehen.", ephemeral=True)
        return

    target_channel = channel
    if target_channel is None:
        existing = discord.utils.get(interaction.guild.text_channels, name=CH_MATCH_REPORTS)
        target_channel = existing if isinstance(existing, discord.TextChannel) else interaction.channel
    if not isinstance(target_channel, discord.TextChannel):
        await interaction.response.send_message("Bitte gib einen Textkanal für die Spielberichte an.", ephemeral=True)
        return

    set_setting("ea_club_id", club_id)
    set_setting("ea_club_name", club_name)
    set_setting("ea_platform", platform.value)
    set_setting("ea_stats_channel_id", str(target_channel.id))

    await interaction.response.defer(ephemeral=True)
    marked = await check_ea_matches(post_new=False, mark_existing=True)

    await interaction.followup.send(
        (
            f"Clubstats sind per Club-ID eingerichtet.\n\n"
            f"Club: **{club_name}** (`{club_id}`)\n"
            f"Plattform: **{EA_PLATFORM_LABELS.get(platform.value, platform.value)}**\n"
            f"Kanal: {target_channel.mention}\n"
            f"Vorhandene letzte Matches wurden als gesehen gespeichert: **{marked}**\n\n"
            "Neue Spiele werden automatisch gepostet. Falls EA Railway weiterhin blockt, steht die genaue Meldung in **#botlog**."
        ),
        ephemeral=True,
    )


@bot.tree.command(name="clubstats_status", description="Zeigt die aktuelle Clubstats-Verbindung")
async def clubstats_status(interaction: discord.Interaction):
    config = get_configured_ea_club()
    if config is None:
        await interaction.response.send_message("Clubstats sind noch nicht eingerichtet. Nutze `/clubstats_setup`.", ephemeral=True)
        return

    channel_text = "nicht gesetzt"
    if config.get("channel_id") and str(config["channel_id"]).isdigit():
        channel = interaction.guild.get_channel(int(config["channel_id"])) if interaction.guild else None
        channel_text = channel.mention if isinstance(channel, discord.TextChannel) else f"`{config['channel_id']}`"

    await interaction.response.send_message(
        (
            f"Club: **{config['club_name']}** (`{config['club_id']}`)\n"
            f"Plattform: **{EA_PLATFORM_LABELS.get(config['platform'], config['platform'])}**\n"
            f"Kanal: {channel_text}"
        ),
        ephemeral=True,
    )


@bot.tree.command(name="clubstats_letztes_spiel", description="Postet das neueste EA Clubs Spiel erneut")
async def clubstats_letztes_spiel(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    config = get_configured_ea_club()
    if config is None:
        await interaction.response.send_message("Clubstats sind noch nicht eingerichtet. Nutze `/clubstats_setup`.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    latest = None
    latest_type = "leagueMatch"
    for match_type in EA_MATCH_TYPES:
        try:
            matches = await ea_fetch_matches(config["club_id"], config["platform"], match_type)
        except EAStatsError:
            continue
        if not isinstance(matches, list):
            continue
        for match in matches:
            if latest is None or int_stat(match, "timestamp") > int_stat(latest, "timestamp"):
                latest = match
                latest_type = match_type

    if latest is None:
        await interaction.followup.send("Ich habe kein letztes Spiel gefunden.", ephemeral=True)
        return

    await post_ea_match_report(interaction.guild, latest, latest_type, config["club_id"])
    await interaction.followup.send("Letztes Spiel wurde gepostet.", ephemeral=True)


@bot.tree.command(name="sync_old_positions", description="Wandelt alte Positionsrollen serverweit in Haupt-/Nebenrollen um")
async def sync_old_positions(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        return

    await interaction.response.send_message("Sync läuft ...", ephemeral=True)

    processed = 0
    skipped = 0

    for member in guild.members:
        if member.bot:
            continue

        await ensure_base_name(member)

        nick_main_positions = parse_main_positions_from_nick(member)
        if not nick_main_positions:
            skipped += 1
            await asyncio.sleep(1.5)
            continue

        old_plain_positions = [r.name for r in member.roles if r.name in POSITIONS]
        old_plain_positions_unique = []
        for pos in old_plain_positions:
            if pos not in old_plain_positions_unique:
                old_plain_positions_unique.append(pos)

        side_positions = [pos for pos in old_plain_positions_unique if pos not in nick_main_positions]

        profile = get_profile(member.id)
        save_profile(
            member.id,
            base_name=profile["base_name"],
            jersey=profile["jersey"],
            main_positions=nick_main_positions[:2],
            side_positions=side_positions,
        )

        await sync_position_roles(member)
        await remove_old_plain_position_roles(member)
        await update_registered_role(member)
        await update_finished_role(member)

        processed += 1
        await asyncio.sleep(1.5)

    await interaction.followup.send(
        f"Sync fertig. Verarbeitet: **{processed}** | Übersprungen ohne erkennbaren Nickname-Positionsblock: **{skipped}**",
        ephemeral=True,
    )


@bot.tree.command(name="setup_server", description="Erstellt Rollen, Kanäle und Panels für Vollpfosten CR8")
async def setup_server(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    guild = interaction.guild
    if guild is None:
        return

    await interaction.response.send_message("Setup läuft ...", ephemeral=True)

    manager_role = await create_role_if_missing(guild, ROLE_MANAGER, discord.Colour.red())
    stammelf_role = await create_role_if_missing(guild, ROLE_STAMMELF, discord.Colour.gold())
    tester_role = await create_role_if_missing(guild, ROLE_TESTER, discord.Colour.blue())
    await create_role_if_missing(guild, ROLE_REGISTERED, discord.Colour.green())
    finished_role = await create_role_if_missing(guild, ROLE_FINISHED, discord.Colour.purple())

    for pos in POSITIONS:
        await create_role_if_missing(guild, main_role_name(pos), discord.Colour.orange())
        await create_role_if_missing(guild, side_role_name(pos), discord.Colour.dark_grey())

    info_cat = await create_category_if_missing(guild, CATEGORY_INFO)
    chat_cat = await create_category_if_missing(guild, CATEGORY_CHAT)
    team_cat = await create_category_if_missing(guild, CATEGORY_TEAM)
    voice_cat = await create_category_if_missing(guild, CATEGORY_VOICE)

    rules_channel = await create_text_if_missing(
        guild,
        info_cat,
        CH_RULES,
        overwrites=overwrite_locked_text_for_managers(guild, manager_role),
    )
    profile_channel = await create_text_if_missing(
        guild,
        info_cat,
        CH_PROFILE,
        overwrites=overwrite_locked_text_for_managers(guild, manager_role),
    )
    club_badge_channel = await create_text_if_missing(
        guild,
        info_cat,
        CH_CLUB_BADGE,
        overwrites=overwrite_locked_text_for_managers(guild, manager_role),
    )
    for legacy_name in (CH_MAIN_POSITIONS, CH_SIDE_POSITIONS, CH_NUMBERS):
        await delete_channel_if_exists(guild, legacy_name)

    availability_channel = discord.utils.get(guild.text_channels, name=CH_AVAILABILITY)
    if availability_channel is None:
        availability_channel = await guild.create_text_channel(
            name=CH_AVAILABILITY,
            category=chat_cat,
            overwrites=overwrite_team_locked_text_for_managers(guild, finished_role, manager_role),
            reason="Vollpfosten CR8 Setup",
        )
    lineups_channel = await create_text_if_missing(
        guild, chat_cat, CH_LINEUPS,
        overwrites=overwrite_team_locked_text_for_managers(guild, finished_role, manager_role),
    )
    match_reports_channel = await create_text_if_missing(
        guild, chat_cat, CH_MATCH_REPORTS,
        overwrites=overwrite_team_locked_text_for_managers(guild, finished_role, manager_role),
    )

    await apply_channel_overwrites(rules_channel, overwrite_locked_text_for_managers(guild, manager_role))
    await apply_channel_overwrites(profile_channel, overwrite_locked_text_for_managers(guild, manager_role))
    await apply_channel_overwrites(club_badge_channel, overwrite_locked_text_for_managers(guild, manager_role))
    await apply_channel_overwrites(availability_channel, overwrite_team_locked_text_for_managers(guild, finished_role, manager_role))
    await apply_channel_overwrites(lineups_channel, overwrite_team_locked_text_for_managers(guild, finished_role, manager_role))
    await apply_channel_overwrites(match_reports_channel, overwrite_team_locked_text_for_managers(guild, finished_role, manager_role))

    await create_text_if_missing(
        guild, chat_cat, CH_GENERAL,
        overwrites=overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role),
    )
    await create_text_if_missing(
        guild, chat_cat, CH_CLIPS,
        overwrites=overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role),
    )
    await create_text_if_missing(
        guild, team_cat, CH_MANAGER,
        overwrites=overwrite_hidden_except(guild, [manager_role], can_send=True),
    )
    await create_text_if_missing(
        guild, team_cat, CH_STAMMELF,
        overwrites=overwrite_hidden_except(guild, [stammelf_role, manager_role], can_send=True),
    )

    bench_voice = await create_voice_if_missing(
        guild, voice_cat, VC_BENCH,
        overwrites=overwrite_voice_bank(guild, manager_role),
    )
    kabine_voice = await create_voice_if_missing(
        guild, voice_cat, VC_STAMMELF,
        overwrites=overwrite_voice_kabine(guild, finished_role, manager_role, stammelf_role),
    )
    manager_voice = await create_voice_if_missing(
        guild, voice_cat, VC_MANAGER,
        overwrites=overwrite_voice_manager(guild, manager_role),
    )
    other_games_voice = get_voice_channel_by_names(guild, [VC_OTHER_GAMES, "andere games", "Andere Games"])
    if other_games_voice is None:
        other_games_voice = await create_voice_if_missing(
            guild, voice_cat, VC_OTHER_GAMES,
            overwrites=overwrite_voice_for_finished(guild, finished_role, manager_role),
        )

    await apply_channel_overwrites(bench_voice, overwrite_voice_bank(guild, manager_role))
    await apply_channel_overwrites(kabine_voice, overwrite_voice_kabine(guild, finished_role, manager_role, stammelf_role))
    await apply_channel_overwrites(manager_voice, overwrite_voice_manager(guild, manager_role))
    await apply_channel_overwrites(other_games_voice, overwrite_voice_for_finished(guild, finished_role, manager_role))

    main_positions_text = (
        "## Schritt 1: Hauptpositionen\n"
        "Wähle **1-2 Positionen**, die du am liebsten spielst.\n"
        "Diese Positionen stehen später in deinem Nickname.\n\n"
        "Beispiele: **ST**, **ZOM**, **IV**.\n"
        "Mit dem roten Button kannst du deine Auswahl zurücksetzen."
    )

    side_positions_text = (
        "## Schritt 2: Nebenpositionen\n"
        "Optional: Wähle weitere Positionen, die du auch spielen kannst.\n\n"
        "Du kannst diesen Schritt überspringen, wenn du keine Nebenposition hast oder deine 2 Positionen schon als Hauptposition gewählt wurden.\n"
        "Mit dem roten Button kannst du deine Nebenpositionen zurücksetzen."
    )

    numbers_text = (
        "## Schritt 3: Trikotnummer\n"
        "Setze deine freie Nummer. Jede Nummer kann nur **einmal** vergeben werden.\n\n"
        "Danach bist du fertig und der Bot aktualisiert deinen Nickname automatisch.\n"
        "Mit dem roten Button kannst du deine Nummer zurücksetzen."
    )

    await replace_panel_message(rules_channel, RULES_MARKER, RULES_TEXT, RulesView(), interaction.client.user)
    await replace_panel_message(profile_channel, MAIN_POSITIONS_MARKER, main_positions_text, MainPositionView(), interaction.client.user)
    await replace_panel_message(profile_channel, SIDE_POSITIONS_MARKER, side_positions_text, SidePositionView(), interaction.client.user)
    await replace_panel_message(profile_channel, NUMBERS_MARKER, numbers_text, NumberView(), interaction.client.user)

    await interaction.followup.send(
        f"Setup fertig. Die Profil-Panels liegen jetzt in **{profile_channel.mention}** und **{availability_channel.mention}** wurde aktualisiert.",
        ephemeral=True,
    )


@bot.tree.command(name="nickname_refresh", description="Aktualisiert deinen Nickname")
async def nickname_refresh(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    await update_member_profile(interaction.user)
    await interaction.response.send_message("Dein Nickname wurde aktualisiert.", ephemeral=True)


@bot.tree.command(name="turn_off_bot", description="Blendet Profilkanäle für alle aus")
async def turn_off_bot(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    await set_profile_channels_mode(interaction.guild, offline_mode=True)
    await interaction.response.send_message("Profilkanäle sind jetzt für **alle** ausgeblendet.", ephemeral=True)


@bot.tree.command(name="turn_on_bot", description="Macht Profilkanäle für alle sichtbar")
async def turn_on_bot(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return
    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    await set_profile_channels_mode(interaction.guild, offline_mode=False)
    await interaction.response.send_message("Profilkanäle sind jetzt für **alle** sichtbar.", ephemeral=True)


def main():
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
