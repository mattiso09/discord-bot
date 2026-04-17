import os
import re
import json
import sqlite3
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env Datei.")

if not GUILD_ID_RAW.isdigit():
    raise RuntimeError("GUILD_ID fehlt in der .env Datei oder ist ungültig.")

GUILD_ID = int(GUILD_ID_RAW)

DB_PATH = Path("vollpfosten_cr8.sqlite3")
SERVER_NAME = "Vollpfosten CR8"

ROLE_MANAGER = "Manager"
ROLE_STAMMELF = "Stammelf"
ROLE_TESTER = "Tester"
ROLE_REGISTERED = "Registriert"
ROLE_FINISHED = "Fertig"

POSITIONS = ["TW", "IV", "RV", "LV", "ZDM", "ZM", "ZOM", "LF", "RF", "ST"]

CATEGORY_INFO = "📌 INFO"
CATEGORY_CHAT = "💬 CHAT"
CATEGORY_TEAM = "🧠 TEAM"
CATEGORY_VOICE = "🔊 VOICE"

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

MAIN_POSITIONS_MARKER = "[VCR8_MAIN_POSITIONS_PANEL]"
SIDE_POSITIONS_MARKER = "[VCR8_SIDE_POSITIONS_PANEL]"
NUMBERS_MARKER = "[VCR8_NUMBERS_PANEL]"


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
    con.commit()
    con.close()


def migrate_db():
    con = db()
    cols = [row["name"] for row in con.execute("PRAGMA table_info(profiles)").fetchall()]

    if "base_name" not in cols:
        con.execute("ALTER TABLE profiles ADD COLUMN base_name TEXT")
    if "jersey" not in cols:
        con.execute("ALTER TABLE profiles ADD COLUMN jersey TEXT")
    if "main_positions" not in cols:
        con.execute("ALTER TABLE profiles ADD COLUMN main_positions TEXT")
    if "side_positions" not in cols:
        con.execute("ALTER TABLE profiles ADD COLUMN side_positions TEXT")

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

    keys = row.keys()
    return {
        "user_id": row["user_id"],
        "base_name": row["base_name"] if "base_name" in keys else None,
        "jersey": row["jersey"] if "jersey" in keys else None,
        "main_positions": json.loads(row["main_positions"]) if "main_positions" in keys and row["main_positions"] else [],
        "side_positions": json.loads(row["side_positions"]) if "side_positions" in keys and row["side_positions"] else [],
    }


def save_profile(
    user_id: int,
    base_name=None,
    jersey=None,
    main_positions=None,
    side_positions=None,
):
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


def strip_managed_nick(name: str) -> str:
    if not name:
        return "Spieler"

    pattern = r"^(?:#\d{1,2}\s*\|\s*)?(?:[A-Z/]{2,20}\s*\|\s*)?"
    stripped = re.sub(pattern, "", name).strip()
    return stripped if stripped else name


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


async def ensure_base_name(member: discord.Member):
    profile = get_profile(member.id)
    if profile["base_name"]:
        return profile["base_name"]

    raw = member.nick or member.global_name or member.name
    base_name = strip_managed_nick(raw)
    save_profile(member.id, base_name=base_name)
    return base_name


def meets_registered_requirements(profile: dict) -> bool:
    return (
        bool(profile["jersey"])
        and 1 <= len(profile["main_positions"]) <= 2
        and len(profile["side_positions"]) >= 1
    )


async def sync_position_roles(member: discord.Member):
    profile = get_profile(member.id)
    wanted = set(profile["main_positions"] + profile["side_positions"])

    current_pos_roles = [r for r in member.roles if r.name in POSITIONS]
    remove_roles = [r for r in current_pos_roles if r.name not in wanted]

    add_roles = []
    for pos in wanted:
        role = get_role_by_name(member.guild, pos)
        if role and role not in member.roles:
            add_roles.append(role)

    if remove_roles:
        await member.remove_roles(*remove_roles, reason="Positionsrollen aktualisiert")
    if add_roles:
        await member.add_roles(*add_roles, reason="Positionsrollen aktualisiert")


async def update_registered_role(member: discord.Member):
    registered_role = get_role_by_name(member.guild, ROLE_REGISTERED)
    if registered_role is None:
        return

    profile = get_profile(member.id)
    should_have = meets_registered_requirements(profile)
    has_registered = registered_role in member.roles

    try:
        if should_have and not has_registered:
            await member.add_roles(registered_role, reason="Registrierungs-Voraussetzungen erfüllt")
        elif not should_have and has_registered:
            await member.remove_roles(registered_role, reason="Registrierungs-Voraussetzungen nicht mehr erfüllt")
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


async def update_finished_role(member: discord.Member):
    finished_role = get_role_by_name(member.guild, ROLE_FINISHED)
    if finished_role is None:
        return

    should_have = has_role(member, ROLE_TESTER) and has_role(member, ROLE_REGISTERED)
    has_finished = finished_role in member.roles

    try:
        if should_have and not has_finished:
            await member.add_roles(finished_role, reason="Tester und Registriert vorhanden")
        elif not should_have and has_finished:
            await member.remove_roles(finished_role, reason="Tester und/oder Registriert fehlt")
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


async def create_role_if_missing(
    guild: discord.Guild,
    name: str,
    colour: discord.Colour = discord.Colour.default(),
):
    role = get_role_by_name(guild, name)
    if role is None:
        role = await guild.create_role(name=name, colour=colour, reason="Vollpfosten CR8 Setup")
    return role


async def create_category_if_missing(guild: discord.Guild, name: str):
    category = discord.utils.get(guild.categories, name=name)
    if category is None:
        category = await guild.create_category(name=name, reason="Vollpfosten CR8 Setup")
    return category


async def create_text_if_missing(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    overwrites=None,
):
    channel = discord.utils.get(guild.text_channels, name=name)
    if channel is None:
        channel = await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason="Vollpfosten CR8 Setup",
        )
    return channel


async def create_voice_if_missing(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    name: str,
    overwrites=None,
    user_limit=None,
):
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


def overwrite_visible_readonly(guild, roles_allowed):
    ow = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            read_message_history=False,
        )
    }

    for role in roles_allowed:
        ow[role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        )

    if guild.me is not None:
        ow[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

    return ow


def overwrite_hidden_except(guild, roles_allowed, can_send=True):
    ow = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False)
    }

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


async def set_profile_channels_mode(guild: discord.Guild, offline_mode: bool):
    channel_names = [CH_MAIN_POSITIONS, CH_SIDE_POSITIONS, CH_NUMBERS]
    text_channels = {c.name: c for c in guild.text_channels}

    overwrites = (
        build_profile_channel_overwrites_off(guild)
        if offline_mode
        else build_profile_channel_overwrites_normal(guild)
    )

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


async def replace_panel_message(
    channel: discord.TextChannel,
    marker: str,
    content: str,
    view: discord.ui.View,
    bot_user,
):
    await delete_panel_messages(channel, marker, bot_user)
    await channel.send(f"{marker}\n{content}", view=view)


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
            await interaction.response.send_message(
                "Du brauchst dafür die Rolle **Tester**.",
                ephemeral=True,
            )
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
            f"Deine Hauptpositionen wurden gesetzt: **{', '.join(selected)}**",
            ephemeral=True,
        )


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
        await interaction.followup.send("Deine Hauptpositionen wurden zurückgesetzt.", ephemeral=True)


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
            await interaction.response.send_message(
                "Du brauchst dafür die Rolle **Tester**.",
                ephemeral=True,
            )
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
            f"Deine Nebenpositionen wurden gesetzt: **{text}**",
            ephemeral=True,
        )


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
        await interaction.followup.send("Deine Nebenpositionen wurden zurückgesetzt.", ephemeral=True)


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
            await interaction.response.send_message(
                "Du brauchst dafür die Rolle **Tester**.",
                ephemeral=True,
            )
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
            f"Deine Trikotnummer wurde auf **#{raw}** gesetzt.",
            ephemeral=True,
        )


class NumberView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Trikotnummer setzen", style=discord.ButtonStyle.primary, custom_id="vcr8:number:open_modal")
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(NumberModal())
        except discord.NotFound:
            pass


intents = discord.Intents.default()
intents.members = True


class VollpfostenBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        init_db()
        migrate_db()

        self.add_view(MainPositionView())
        self.add_view(SidePositionView())
        self.add_view(NumberView())

        guild_obj = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild_obj)
        synced = await self.tree.sync(guild=guild_obj)

        print(f"Sync für Guild {GUILD_ID}: {len(synced)} Commands")
        for cmd in synced:
            print(f"- /{cmd.name}")


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


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.guild.id != GUILD_ID:
        return

    before_roles = {r.id for r in before.roles}
    after_roles = {r.id for r in after.roles}

    if before_roles != after_roles:
        await update_member_profile(after)


@bot.tree.command(name="setup_server", description="Erstellt Rollen, Kanäle und Panels für Vollpfosten CR8")
async def setup_server(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return

    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    guild = interaction.guild
    await interaction.response.send_message("Setup läuft ...", ephemeral=True)

    manager_role = await create_role_if_missing(guild, ROLE_MANAGER, discord.Colour.red())
    stammelf_role = await create_role_if_missing(guild, ROLE_STAMMELF, discord.Colour.gold())
    tester_role = await create_role_if_missing(guild, ROLE_TESTER, discord.Colour.blue())
    await create_role_if_missing(guild, ROLE_REGISTERED, discord.Colour.green())
    await create_role_if_missing(guild, ROLE_FINISHED, discord.Colour.purple())

    for pos in POSITIONS:
        await create_role_if_missing(guild, pos, discord.Colour.dark_grey())

    info_cat = await create_category_if_missing(guild, CATEGORY_INFO)
    chat_cat = await create_category_if_missing(guild, CATEGORY_CHAT)
    team_cat = await create_category_if_missing(guild, CATEGORY_TEAM)
    voice_cat = await create_category_if_missing(guild, CATEGORY_VOICE)

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

    await create_text_if_missing(
        guild,
        chat_cat,
        CH_GENERAL,
        overwrites=overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role),
    )

    await create_text_if_missing(
        guild,
        chat_cat,
        CH_CLIPS,
        overwrites=overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role),
    )

    await create_text_if_missing(
        guild,
        chat_cat,
        CH_LINEUPS,
        overwrites=overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role),
    )

    await create_text_if_missing(
        guild,
        chat_cat,
        CH_AVAILABILITY,
        overwrites=overwrite_public_for_team(guild, tester_role, manager_role, stammelf_role),
    )

    await create_text_if_missing(
        guild,
        team_cat,
        CH_MANAGER,
        overwrites=overwrite_hidden_except(guild, [manager_role], can_send=True),
    )

    await create_text_if_missing(
        guild,
        team_cat,
        CH_STAMMELF,
        overwrites=overwrite_hidden_except(guild, [stammelf_role, manager_role], can_send=True),
    )

    await create_voice_if_missing(
        guild,
        voice_cat,
        VC_BENCH,
        overwrites=overwrite_voice_public(guild, tester_role, manager_role, stammelf_role),
    )

    await create_voice_if_missing(
        guild,
        voice_cat,
        VC_STAMMELF,
        overwrites=overwrite_voice_stammelf(guild, tester_role, manager_role, stammelf_role),
    )

    await create_voice_if_missing(
        guild,
        voice_cat,
        VC_MANAGER,
        overwrites=overwrite_voice_manager(guild, manager_role),
    )

    main_positions_text = (
        "## Hauptpositionen\n"
        "Wähle hier **mindestens 1 und maximal 2 Hauptpositionen**.\n"
        "Nur diese Hauptpositionen werden in deinen Nicknamen übernommen.\n\n"
        "Mit dem roten Button kannst du deine Hauptpositionen zurücksetzen."
    )

    side_positions_text = (
        "## Nebenpositionen\n"
        "Wähle hier beliebig viele Nebenpositionen.\n"
        "Diese werden als Rollen gespeichert, aber **nicht** in deinen Nicknamen übernommen.\n\n"
        "Mit dem roten Button kannst du deine Nebenpositionen zurücksetzen."
    )

    numbers_text = (
        "## Trikotnummer\n"
        "Setze hier deine Trikotnummer.\n"
        "Die Nummer wird zusammen mit deinen Hauptpositionen in deinen Nicknamen übernommen."
    )

    await replace_panel_message(
        main_positions_channel,
        MAIN_POSITIONS_MARKER,
        main_positions_text,
        MainPositionView(),
        interaction.client.user,
    )
    await replace_panel_message(
        side_positions_channel,
        SIDE_POSITIONS_MARKER,
        side_positions_text,
        SidePositionView(),
        interaction.client.user,
    )
    await replace_panel_message(
        numbers_channel,
        NUMBERS_MARKER,
        numbers_text,
        NumberView(),
        interaction.client.user,
    )

    await interaction.followup.send("Serverstruktur und die 3 Profil-Panels wurden aktualisiert.", ephemeral=True)


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
    await interaction.response.send_message(
        "Profilkanäle sind jetzt für **alle** ausgeblendet.",
        ephemeral=True,
    )


@bot.tree.command(name="turn_on_bot", description="Macht Profilkanäle für alle sichtbar")
async def turn_on_bot(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return

    if not is_manager(interaction.user) and interaction.user != interaction.guild.owner:
        await interaction.response.send_message("Dafür brauchst du Manager-Rechte.", ephemeral=True)
        return

    await set_profile_channels_mode(interaction.guild, offline_mode=False)
    await interaction.response.send_message(
        "Profilkanäle sind jetzt für **alle** sichtbar.",
        ephemeral=True,
    )


bot.run(TOKEN)