import os
import re
import json
import time
import asyncio
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env Datei oder in Railway Variables.")

if not GUILD_ID_RAW.isdigit():
    raise RuntimeError("GUILD_ID fehlt in der .env Datei oder ist ungültig.")

GUILD_ID = int(GUILD_ID_RAW)
BOT_TZ = ZoneInfo("Europe/Berlin")

DB_PATH = Path("vollpfosten_cr8.sqlite3")
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

CATEGORY_INFO = "📌 INFO"
CATEGORY_CHAT = "💬 CHAT"
CATEGORY_TEAM = "🧠 TEAM"
CATEGORY_VOICE = "🔊 VOICE"

CH_RULES = "regeln"
CH_MAIN_POSITIONS = "hauptpositionen"
CH_SIDE_POSITIONS = "nebenpositionen"
CH_NUMBERS = "trikotnummer"
CH_GENERAL = "allgemein"
CH_CLIPS = "clips"
CH_MANAGER = "manager-chat"
CH_STAMMELF = "stammelf-chat"
CH_LINEUPS = "aufstellungen"
CH_AVAILABILITY = "verfuegbarkeit"

VC_BENCH = "bank"
VC_STAMMELF = "kabine"
VC_MANAGER = "krisenbesprechung"

RULES_MARKER = "[VCR8_RULES_PANEL]"
MAIN_POSITIONS_MARKER = "[VCR8_MAIN_POSITIONS_PANEL]"
SIDE_POSITIONS_MARKER = "[VCR8_SIDE_POSITIONS_PANEL]"
NUMBERS_MARKER = "[VCR8_NUMBERS_PANEL]"

RULES_TEXT = """📜 **Regeln für Vollpfosten CR8**

Willkommen im Vereinsserver!
Mit dem Akzeptieren der Regeln verpflichtest du dich, dich an folgende Punkte zu halten:

**Respekt & Umgang**
- Behandle alle Mitglieder respektvoll
- Kein Beleidigen, Provozieren oder unnötiges Drama

**Verhalten im Spiel**
- Kein unnötiges Geflame oder Rage
- Bei wichtigen Spielen wird konzentriert gespielt
- Spaß ist erlaubt, aber nicht auf Kosten des Teams

**Teamplay**
- Teamplay steht immer über Ego-Play
- Halte deine Position und spiel fürs Team
- Kommunikation ist wichtig

**Organisation**
- Höre auf Ansagen von Managern und Stammelf
- Reagiere auf Verfügbarkeitsabfragen ehrlich
- Sei pünktlich zu Spielen und Training

**Aktivität & Zuverlässigkeit**
- Wer dauerhaft unzuverlässig ist, muss mit Konsequenzen rechnen
- Abmelden ist Pflicht, wenn du nicht kannst

**Discord Verhalten**
- Kein Spam in Channels oder Voice
- Nutze die richtigen Channels (z. B. Clips nur in #clips)
- Halte den Server übersichtlich

**Allgemein**
- Jeder vertritt mit seinem Verhalten den Verein
- Entscheidungen der Manager sind zu respektieren

Drücke unten auf den Button, um die Regeln zu akzeptieren und die Rolle **Tester** zu erhalten.
"""

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
            yes_threshold_announced INTEGER DEFAULT 0,
            remind_60_sent INTEGER DEFAULT 0,
            remind_5_sent INTEGER DEFAULT 0,
            closed INTEGER DEFAULT 0
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            response TEXT NOT NULL,
            voted_at TEXT NOT NULL,
            PRIMARY KEY (poll_id, user_id)
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
    if not cols_polls:
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
                yes_threshold_announced INTEGER DEFAULT 0,
                remind_60_sent INTEGER DEFAULT 0,
                remind_5_sent INTEGER DEFAULT 0,
                closed INTEGER DEFAULT 0
            )
            """
        )
    else:
        if "yes_threshold_announced" not in cols_polls:
            con.execute("ALTER TABLE polls ADD COLUMN yes_threshold_announced INTEGER DEFAULT 0")
        if "remind_60_sent" not in cols_polls:
            con.execute("ALTER TABLE polls ADD COLUMN remind_60_sent INTEGER DEFAULT 0")
        if "remind_5_sent" not in cols_polls:
            con.execute("ALTER TABLE polls ADD COLUMN remind_5_sent INTEGER DEFAULT 0")
        if "closed" not in cols_polls:
            con.execute("ALTER TABLE polls ADD COLUMN closed INTEGER DEFAULT 0")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS poll_votes (
            poll_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            response TEXT NOT NULL,
            voted_at TEXT NOT NULL,
            PRIMARY KEY (poll_id, user_id)
        )
        """
    )

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


def create_poll_record(kind: str, title: str, weekday_text: str, date_text: str, time_text: str, start_at: datetime, created_by: int | None):
    con = db()
    cur = con.execute(
        """
        INSERT INTO polls (kind, title, weekday_text, date_text, time_text, start_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (kind, title, weekday_text, date_text, time_text, start_at.isoformat(), created_by),
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


def get_open_polls():
    con = db()
    rows = con.execute("SELECT * FROM polls WHERE closed = 0").fetchall()
    con.close()
    return rows


def upsert_vote(poll_id: int, user_id: int, response: str):
    con = db()
    con.execute(
        """
        INSERT INTO poll_votes (poll_id, user_id, response, voted_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(poll_id, user_id) DO UPDATE SET
            response = excluded.response,
            voted_at = excluded.voted_at
        """,
        (poll_id, user_id, response, datetime.now(BOT_TZ).isoformat()),
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


def daily_poll_exists_for_date(date_text: str):
    con = db()
    row = con.execute(
        "SELECT id FROM polls WHERE kind = 'daily_funclub' AND date_text = ? LIMIT 1",
        (date_text,),
    ).fetchone()
    con.close()
    return row is not None


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


def is_manager(member: discord.Member) -> bool:
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or any(r.name == ROLE_MANAGER for r in member.roles)
    )


def has_role(member: discord.Member, role_name: str) -> bool:
    return any(r.name == role_name for r in member.roles)


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
        return "Bitte akzeptiere zuerst die Regeln im Channel **#regeln**."
    if not (1 <= len(profile["main_positions"]) <= 2):
        return "Bitte wähle jetzt **mindestens 1 und maximal 2 Hauptpositionen**."
    if len(profile["side_positions"]) < 1:
        return "Bitte wähle jetzt **mindestens 1 Nebenposition**."
    if not profile["jersey"]:
        return "Bitte setze jetzt deine **Trikotnummer**, damit du mitspielen kannst."
    return "✅ Du bist jetzt vollständig registriert und kannst mitspielen."


async def send_join_dm(member: discord.Member):
    text = (
        f"Willkommen auf **{member.guild.name}**.\n\n"
        "Damit du mitspielen kannst, mach bitte diese Schritte nacheinander:\n"
        "1. Regeln akzeptieren\n"
        "2. Hauptpositionen wählen\n"
        "3. Nebenpositionen wählen\n"
        "4. Trikotnummer setzen\n\n"
        f"{next_step_message(member)}"
    )
    try:
        await member.send(text)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


async def send_private_progress_dm(member: discord.Member, intro: str):
    text = f"{intro}\n\n{next_step_message(member)}"
    try:
        await member.send(text)
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
        and len(profile["side_positions"]) >= 1
    )


async def sync_position_roles(member: discord.Member):
    guild = member.guild
    profile = get_profile(member.id)

    wanted_main = set(profile["main_positions"])
    wanted_side = set(profile["side_positions"])

    current_managed_roles = []
    for role in member.roles:
        if role.name.startswith("Haupt-") or role.name.startswith("Neben-"):
            current_managed_roles.append(role)

    remove_roles = []
    for role in current_managed_roles:
        if role.name.startswith("Haupt-"):
            pos = role.name.replace("Haupt-", "", 1)
            if pos not in wanted_main:
                remove_roles.append(role)
        elif role.name.startswith("Neben-"):
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

    if remove_roles:
        await member.remove_roles(*remove_roles, reason="Positionsrollen aktualisiert")
    if add_roles:
        await member.add_roles(*add_roles, reason="Positionsrollen aktualisiert")


async def remove_old_plain_position_roles(member: discord.Member):
    old_roles = [r for r in member.roles if r.name in POSITIONS]
    if old_roles:
        try:
            await member.remove_roles(*old_roles, reason="Alte Positionsrollen durch Haupt/Neben ersetzt")
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass


async def update_registered_role(member: discord.Member):
    registered_role = get_role_by_name(member.guild, ROLE_REGISTERED)
    if registered_role is None:
        return

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


async def update_finished_role(member: discord.Member):
    finished_role = get_role_by_name(member.guild, ROLE_FINISHED)
    if finished_role is None:
        return

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


async def update_member_profile(member: discord.Member):
    base_name = await ensure_base_name(member)
    await sync_position_roles(member)
    await update_registered_role(member)
    await update_finished_role(member)

    profile = get_profile(member.id)

    if can_use_profile_system(member):
        new_nick = build_nick(base_name, profile["jersey"], profile["main_positions"])
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


def overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        tester_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True),
        manager_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True),
        stammelf_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, add_reactions=True),
    }


def overwrite_voice_public(guild, tester_role, manager_role, stammelf_role):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        tester_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        manager_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        stammelf_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
    }


def overwrite_voice_stammelf(guild, tester_role, manager_role, stammelf_role):
    return {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        tester_role: discord.PermissionOverwrite(view_channel=True, connect=False, speak=False),
        manager_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        stammelf_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
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
    channel_names = [CH_MAIN_POSITIONS, CH_SIDE_POSITIONS, CH_NUMBERS]
    text_channels = {c.name: c for c in guild.text_channels}
    overwrites = build_profile_channel_overwrites_off(guild) if offline_mode else build_profile_channel_overwrites_normal(guild)

    for name in channel_names:
        channel = text_channels.get(name)
        if channel is not None:
            await channel.edit(overwrites=overwrites, reason="Vollpfosten CR8: Profilkanäle umgeschaltet")


async def delete_panel_messages(channel: discord.TextChannel, marker: str, bot_user):
    to_delete = []
    async for msg in channel.history(limit=100):
        if msg.author == bot_user and msg.content.startswith(marker):
            to_delete.append(msg)

    for msg in to_delete:
        try:
            await msg.delete()
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass


async def replace_panel_message(channel: discord.TextChannel, marker: str, content: str, view: discord.ui.View, bot_user):
    await delete_panel_messages(channel, marker, bot_user)
    await channel.send(f"{marker}\n{content}", view=view)


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

    embed = discord.Embed(
        title=title,
        description=f"📅 **{weekday_text}, {date_text}**\n⏰ **{time_text}**",
        colour=discord.Colour.green(),
    )

    offensive_lines = []
    midfield_lines = []
    defensive_lines = []
    no_lines = []

    yes_members = []
    no_members = []

    for vote in votes_rows:
        member = guild.get_member(vote["user_id"])
        if member is None:
            continue
        if vote["response"] == "yes":
            yes_members.append((member, vote["voted_at"]))
        elif vote["response"] == "no":
            no_members.append(member)

    yes_members.sort(key=lambda x: x[1])
    no_members.sort(key=lambda m: clean_name_for_availability(m).lower())

    emoji_counters_yes = {"ST": 0, "IV": 0}
    emoji_counters_no = {"ST": 0, "IV": 0}

    for member, _ in yes_members:
        pos = get_first_main_position_for_member(member)
        name = clean_name_for_availability(member)
        emoji = get_position_emoji(guild, pos, emoji_counters_yes)
        prefix = str(emoji) if emoji else "•"
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

    return embed


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
    yes_ids = [row["user_id"] for row in votes if row["response"] == "yes"]

    for user_id in yes_ids:
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
    if poll_row["kind"] != "daily_funclub":
        return
    if poll_row["yes_threshold_announced"]:
        return

    votes = get_votes_for_poll(poll_row["id"])
    yes_count = sum(1 for row in votes if row["response"] == "yes")
    if yes_count >= 4:
        channel = guild.get_channel(poll_row["channel_id"])
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        try:
            await channel.send("✅ Es sind jetzt mindestens **4 Zusagen** da. Es wird gespielt.")
            mark_poll_threshold_announced(poll_row["id"])
        except discord.HTTPException:
            pass


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
):
    poll_id = create_poll_record(kind, title, weekday_text, date_text, time_text, start_at, created_by)
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
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return

    now = datetime.now(BOT_TZ)
    if now.hour != 12 or now.minute != 0:
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
                f"⏳ **{poll_row['title']}** geht in ungefähr **1 Stunde** los.",
            )
            mark_poll_reminder_60(poll_row["id"])

        if not poll_row["remind_5_sent"] and timedelta(minutes=0) < diff <= timedelta(minutes=5):
            await send_yes_voter_reminder_dm(
                guild,
                poll_row,
                f"🚨 **{poll_row['title']}** geht in ungefähr **5 Minuten** los.",
            )
            mark_poll_reminder_5(poll_row["id"])

        if diff <= timedelta(minutes=-10):
            close_poll(poll_row["id"])


async def background_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await maybe_create_daily_funclub_poll()
            await process_poll_reminders()
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

        await update_member_profile(interaction.user)

        await interaction.response.send_message(
            f"Regeln akzeptiert.\n\n{next_step_message(interaction.user)}",
            ephemeral=True,
        )
        await send_private_progress_dm(interaction.user, "Du hast die Regeln akzeptiert.")


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

        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey=profile["jersey"],
            main_positions=selected,
            side_positions=profile["side_positions"],
        )

        await update_member_profile(interaction.user)
        await interaction.response.send_message(
            f"Deine Hauptpositionen wurden gesetzt: **{', '.join(selected)}**\n\n{next_step_message(interaction.user)}",
            ephemeral=True,
        )
        await send_private_progress_dm(interaction.user, "Deine Hauptpositionen wurden gespeichert.")


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

        await update_member_profile(interaction.user)
        await interaction.followup.send(
            f"Deine Hauptpositionen wurden zurückgesetzt.\n\n{next_step_message(interaction.user)}",
            ephemeral=True,
        )
        await send_private_progress_dm(interaction.user, "Deine Hauptpositionen wurden zurückgesetzt.")


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

        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey=profile["jersey"],
            main_positions=profile["main_positions"],
            side_positions=selected,
        )

        await update_member_profile(interaction.user)
        text = ", ".join(selected) if selected else "keine"
        await interaction.response.send_message(
            f"Deine Nebenpositionen wurden gesetzt: **{text}**\n\n{next_step_message(interaction.user)}",
            ephemeral=True,
        )
        await send_private_progress_dm(interaction.user, "Deine Nebenpositionen wurden gespeichert.")


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

        await update_member_profile(interaction.user)
        await interaction.followup.send(
            f"Deine Nebenpositionen wurden zurückgesetzt.\n\n{next_step_message(interaction.user)}",
            ephemeral=True,
        )
        await send_private_progress_dm(interaction.user, "Deine Nebenpositionen wurden zurückgesetzt.")


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

        profile = get_profile(interaction.user.id)

        save_profile(
            interaction.user.id,
            base_name=profile["base_name"],
            jersey=str(num),
            main_positions=profile["main_positions"],
            side_positions=profile["side_positions"],
        )

        await update_member_profile(interaction.user)
        await interaction.response.send_message(
            f"Deine Trikotnummer wurde auf **#{raw}** gesetzt.\n\n{next_step_message(interaction.user)}",
            ephemeral=True,
        )
        await send_private_progress_dm(interaction.user, "Deine Trikotnummer wurde gespeichert.")


class NumberView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Trikotnummer setzen", style=discord.ButtonStyle.primary, custom_id="vcr8:number:open_modal")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(NumberModal())
        except discord.NotFound:
            pass


class AvailabilityVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Ja", style=discord.ButtonStyle.success, custom_id="vcr8:availability:yes")
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_role(interaction.user, ROLE_FINISHED):
            await interaction.response.send_message(
                "Du brauchst dafür die Rolle **Fertig**.",
                ephemeral=True,
            )
            return

        poll_row = get_poll_by_message_id(interaction.message.id)
        if poll_row is None:
            await interaction.response.send_message(
                "Diese Abstimmung wurde nicht gefunden.",
                ephemeral=True,
            )
            return

        upsert_vote(poll_row["id"], interaction.user.id, "yes")

        await interaction.response.defer(ephemeral=True)
        await refresh_poll_message(interaction.guild, poll_row["id"], interaction.message)
        await maybe_send_threshold_message(interaction.guild, poll_row)
        await interaction.followup.send(
            "Deine Stimme wurde auf **Ja** gesetzt.",
            ephemeral=True,
        )

    @discord.ui.button(label="❌ Nein", style=discord.ButtonStyle.danger, custom_id="vcr8:availability:no")
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_role(interaction.user, ROLE_FINISHED):
            await interaction.response.send_message(
                "Du brauchst dafür die Rolle **Fertig**.",
                ephemeral=True,
            )
            return

        poll_row = get_poll_by_message_id(interaction.message.id)
        if poll_row is None:
            await interaction.response.send_message(
                "Diese Abstimmung wurde nicht gefunden.",
                ephemeral=True,
            )
            return

        upsert_vote(poll_row["id"], interaction.user.id, "no")

        await interaction.response.defer(ephemeral=True)
        await refresh_poll_message(interaction.guild, poll_row["id"], interaction.message)
        await interaction.followup.send(
            "Deine Stimme wurde auf **Nein** gesetzt.",
            ephemeral=True,
        )

    @discord.ui.button(label="🗑️ Stimme entfernen", style=discord.ButtonStyle.secondary, custom_id="vcr8:availability:remove")
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            return

        if not has_role(interaction.user, ROLE_FINISHED):
            await interaction.response.send_message(
                "Du brauchst dafür die Rolle **Fertig**.",
                ephemeral=True,
            )
            return

        poll_row = get_poll_by_message_id(interaction.message.id)
        if poll_row is None:
            await interaction.response.send_message(
                "Diese Abstimmung wurde nicht gefunden.",
                ephemeral=True,
            )
            return

        delete_vote(poll_row["id"], interaction.user.id)

        await interaction.response.defer(ephemeral=True)
        await refresh_poll_message(interaction.guild, poll_row["id"], interaction.message)
        await interaction.followup.send(
            "Deine Stimme wurde entfernt. Du kannst jetzt wieder neu abstimmen.",
            ephemeral=True,
        )


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
    await send_join_dm(member)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.guild.id != GUILD_ID:
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
    )

    await interaction.response.send_message("Verfügbarkeitsabfrage wurde erstellt.", ephemeral=True)


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
    await create_role_if_missing(guild, ROLE_FINISHED, discord.Colour.purple())

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
        overwrites=build_profile_channel_overwrites_normal(guild),
    )
    main_positions_channel = await create_text_if_missing(
        guild,
        info_cat,
        CH_MAIN_POSITIONS,
        overwrites=build_profile_channel_overwrites_normal(guild),
    )
    side_positions_channel = await create_text_if_missing(
        guild,
        info_cat,
        CH_SIDE_POSITIONS,
        overwrites=build_profile_channel_overwrites_normal(guild),
    )
    numbers_channel = await create_text_if_missing(
        guild,
        info_cat,
        CH_NUMBERS,
        overwrites=build_profile_channel_overwrites_normal(guild),
    )

    old_availability = discord.utils.get(guild.text_channels, name=CH_AVAILABILITY)
    if old_availability is not None:
        try:
            await old_availability.delete(reason="Vollpfosten CR8 Setup: Verfügbarkeitskanal neu erstellen")
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass

    availability_channel = await guild.create_text_channel(
        name=CH_AVAILABILITY,
        category=chat_cat,
        overwrites=build_availability_overwrites(guild),
        reason="Vollpfosten CR8 Setup",
    )

    await create_text_if_missing(
        guild, chat_cat, CH_GENERAL,
        overwrites=overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role),
    )
    await create_text_if_missing(
        guild, chat_cat, CH_CLIPS,
        overwrites=overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role),
    )
    await create_text_if_missing(
        guild, chat_cat, CH_LINEUPS,
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

    await create_voice_if_missing(
        guild, voice_cat, VC_BENCH,
        overwrites=overwrite_voice_public(guild, tester_role, manager_role, stammelf_role),
    )
    await create_voice_if_missing(
        guild, voice_cat, VC_STAMMELF,
        overwrites=overwrite_voice_stammelf(guild, tester_role, manager_role, stammelf_role),
    )
    await create_voice_if_missing(
        guild, voice_cat, VC_MANAGER,
        overwrites=overwrite_voice_manager(guild, manager_role),
    )

    main_positions_text = (
        "## Hauptpositionen\n"
        "Wähle hier **mindestens 1 und maximal 2 Hauptpositionen**.\n"
        "Nur diese Hauptpositionen werden in deinen Nicknamen übernommen.\n"
        "Für Hauptpositionen bekommst du Rollen wie **Haupt-ST**.\n\n"
        "Mit dem roten Button kannst du deine Hauptpositionen zurücksetzen."
    )

    side_positions_text = (
        "## Nebenpositionen\n"
        "Wähle hier beliebig viele Nebenpositionen.\n"
        "Diese werden als Rollen gespeichert, aber **nicht** in deinen Nicknamen übernommen.\n"
        "Für Nebenpositionen bekommst du Rollen wie **Neben-ST**.\n\n"
        "Mit dem roten Button kannst du deine Nebenpositionen zurücksetzen."
    )

    numbers_text = (
        "## Trikotnummer\n"
        "Setze hier deine Trikotnummer.\n"
        "Die Nummer wird zusammen mit deinen Hauptpositionen in deinen Nicknamen übernommen."
    )

    await replace_panel_message(rules_channel, RULES_MARKER, RULES_TEXT, RulesView(), interaction.client.user)
    await replace_panel_message(main_positions_channel, MAIN_POSITIONS_MARKER, main_positions_text, MainPositionView(), interaction.client.user)
    await replace_panel_message(side_positions_channel, SIDE_POSITIONS_MARKER, side_positions_text, SidePositionView(), interaction.client.user)
    await replace_panel_message(numbers_channel, NUMBERS_MARKER, numbers_text, NumberView(), interaction.client.user)

    await interaction.followup.send(
        f"Setup fertig. Der Kanal **{availability_channel.mention}** wurde neu erstellt und die Panels wurden aktualisiert.",
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
    while True:
        try:
            bot.run(TOKEN)
            break
        except KeyboardInterrupt:
            print("Bot manuell gestoppt.")
            break
        except Exception as e:
            print(f"Bot abgestürzt: {e}")
            print("Neustart in 10 Sekunden...")
            time.sleep(10)


if __name__ == "__main__":
    main()