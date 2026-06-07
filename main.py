import discord
from discord.ext import commands
from discord import app_commands
import logging
from dotenv import load_dotenv
import os
import random
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from pytz import timezone
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
import sqlite3
import atexit
import time

load_dotenv()
token = os.getenv('DISCORD_TOKEN')


conn = sqlite3.connect("characters.sqlite")
cursor = conn.cursor()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS characters (
    user_id INTEGER,
    name TEXT,
    level INTEGER,
    race TEXT,
    specialty TEXT,
    subclass TEXT
);

CREATE TABLE IF NOT EXISTS spell_slots (
    user_id INTEGER,
    character_name TEXT,
    spell_level INTEGER,
    slots_remaining INTEGER,
    slots_max INTEGER
);

CREATE TABLE IF NOT EXISTS prepared_spells (
    user_id INTEGER,
    character_name TEXT,
    spell_name TEXT,
    spell_level INTEGER
);

CREATE TABLE IF NOT EXISTS cantrips (
    user_id INTEGER,
    character_name TEXT,
    spell_name TEXT
);

CREATE TABLE IF NOT EXISTS prepared_limits (
    user_id INTEGER,
    character_name TEXT,
    spell_level INTEGER,
    max_prepared INTEGER
);

CREATE TABLE IF NOT EXISTS class_levels (
    user_id INTEGER,
    character_name TEXT,
    class_name TEXT,
    class_level INTEGER
);
""")

# --- Safe column additions ---
try:
    cursor.execute("ALTER TABLE characters ADD COLUMN max_prepared INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE characters ADD COLUMN image_url TEXT")
except sqlite3.OperationalError:
    pass

conn.commit()
atexit.register(conn.close)


class NextButton(discord.ui.Button):

    def __init__(self):
        super().__init__(label="Next ➡", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):

        view: SpellPageView = self.view
        view.page += 1
        view.update_view()

        await interaction.response.edit_message(view=view)


class PrevButton(discord.ui.Button):

    def __init__(self):
        super().__init__(label="⬅ Previous", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):

        view: SpellPageView = self.view
        view.page -= 1
        view.update_view()

        await interaction.response.edit_message(view=view)




class SpellPrepareView(discord.ui.View):

    def __init__(self, user_id, character, level, spells):
        super().__init__(timeout=120)

        self.user_id = user_id
        self.character = character
        self.level = level
        self.selected_spells = []

        self.add_item(SpellSelect(spells))

    async def interaction_check(self, interaction: discord.Interaction):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This menu isn't for you.",
                ephemeral=True
            )
            return False

        return True

class PrepareSpellView(discord.ui.View):
    def __init__(self, user_id, character, level, spells):
        super().__init__(timeout=120)

        self.add_item(
            PrepareSpellSelect(user_id, character, level, spells)
        )


class PrepareSpellSelect(discord.ui.Select):

    def __init__(self, spells, user_id, character, level):

        options = [
            discord.SelectOption(label=spell, value=spell)
            for spell in spells
        ]

        super().__init__(
            placeholder="Choose spells to prepare",
            min_values=1,
            max_values=len(options),
            options=options
        )

        self.user_id = user_id
        self.character = character
        self.level = level

    async def callback(self, interaction: discord.Interaction):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This isn't your character.",
                ephemeral=True
            )
            return

        prepared = []

        for spell in self.values:

            cursor.execute("""
            SELECT 1 FROM prepared_spells
            WHERE user_id = ? AND character_name = ? AND spell_name = ?
            """, (self.user_id, self.character, spell))

            if cursor.fetchone():
                continue

            cursor.execute("""
            INSERT INTO prepared_spells (
                user_id,
                character_name,
                spell_name,
                spell_level
            )
            VALUES (?, ?, ?, ?)
            """, (
                self.user_id,
                self.character,
                spell,
                self.level
            ))

            prepared.append(spell)

        conn.commit()

        await interaction.response.send_message(
            f"Prepared: {', '.join(prepared)}",
            ephemeral=True
        )


class SpellPageView(discord.ui.View):

    def __init__(self, spells, user_id, character, level, page=0):

        super().__init__(timeout=120)

        self.spells = spells
        self.user_id = user_id
        self.character = character
        self.level = level
        self.page = page

        self.page_size = 25
        self.max_page = (len(spells) - 1) // self.page_size

        self.update_view()

    def update_view(self):

        self.clear_items()

        start = self.page * self.page_size
        end = start + self.page_size
        page_spells = self.spells[start:end]

        self.add_item(
            PrepareSpellSelect(
                page_spells,
                self.user_id,
                self.character,
                self.level
            )
        )

        if self.page > 0:
            self.add_item(PrevButton())

        if self.page < self.max_page:
            self.add_item(NextButton())


class LongRestView(discord.ui.View):
    def __init__(self, user_id, character):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.character = character

    @discord.ui.button(label="Prepare Spells Again", style=discord.ButtonStyle.primary)
    async def reset_spells(self, interaction: discord.Interaction, button: discord.ui.Button):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your character.", ephemeral=True)
            return

        cursor.execute("""
        DELETE FROM prepared_spells
        WHERE user_id = ? AND character_name = ?
        """, (self.user_id, self.character))

        conn.commit()

        await interaction.response.edit_message(
            content=f"Spell list cleared for **{self.character}**. You can prepare new spells now.",
            view=None
        )

    @discord.ui.button(label="Keep Current Spells", style=discord.ButtonStyle.secondary)
    async def keep_spells(self, interaction: discord.Interaction, button: discord.ui.Button):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your character.", ephemeral=True)
            return

        await interaction.response.edit_message(
            content=f"**{self.character}** keeps their prepared spells.",
            view=None
        )

class UnprepareSpellView(discord.ui.View):
    def __init__(self, user_id, character, spells):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.character = character

        for spell in spells:
            button = discord.ui.Button(
                label=spell,
                style=discord.ButtonStyle.secondary
            )

            async def callback(interaction: discord.Interaction, spell_name=spell):

                if interaction.user.id != self.user_id:
                    await interaction.response.send_message(
                        "This isn't your character.", ephemeral=True
                    )
                    return

                cursor.execute("""
                DELETE FROM prepared_spells
                WHERE user_id = ? AND character_name = ? AND spell_name = ?
                """, (self.user_id, self.character, spell_name))

                conn.commit()

                await interaction.response.send_message(
                    f"Unprepared **{spell_name}**.", ephemeral=True
                )

            button.callback = callback
            self.add_item(button)

        # Done button
        done_button = discord.ui.Button(
            label="Done",
            style=discord.ButtonStyle.success
        )

        async def done_callback(interaction: discord.Interaction):

            if interaction.user.id != self.user_id:
                await interaction.response.send_message(
                    "This isn't your character.", ephemeral=True
                )
                return

            await interaction.response.edit_message(
                content=f"Finished unpreparing spells for **{self.character}**.",
                view=None
            )

        done_button.callback = done_callback
        self.add_item(done_button)


def load_spells_for_classes(level, classes):

    filename = f"level_spell_files/level {level} spells.txt"

    classes = [c.lower() for c in classes]

    try:
        with open(filename, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f]
    except FileNotFoundError:
        return []

    spells = []

    for line in lines:

        if "(" not in line:
            continue

        name, class_part = line.split("(", 1)
        name = name.strip()

        class_text = class_part.replace(")", "").lower()
        spell_classes = [c.strip() for c in class_text.split(",")]

        # If ANY class matches
        if any(c in spell_classes for c in classes):
            spells.append(name)

    return sorted(set(spells))


async def spell_autocomplete(
    interaction: discord.Interaction,
    current: str
):

    character = interaction.namespace.character
    level = interaction.namespace.level

    if character is None or level is None:
        return []

    cursor.execute("""
    SELECT class_name
    FROM class_levels
    WHERE user_id = ? AND character_name = ?
    """, (interaction.user.id, character))

    rows = cursor.fetchall()

    if not rows:
        return []

    classes = [row[0] for row in rows]

    spells = load_spells_for_classes(level, classes)

    matches = [
        spell for spell in spells
        if current.lower() in spell.lower()
    ][:25]

    return [
        app_commands.Choice(name=spell, value=spell)
        for spell in matches
    ]


@discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):

        if not self.selected_spells:

            await interaction.response.send_message(
                "No spells selected.",
                ephemeral=True
            )
            return


        # Get prepared limit
        cursor.execute("""
        SELECT max_prepared
        FROM prepared_limits
        WHERE user_id = ? AND character_name = ? AND spell_level = ?
        """, (interaction.user.id, self.character, self.level))

        row = cursor.fetchone()

        max_prepared = row[0] if row else None


        cursor.execute("""
        SELECT COUNT(*)
        FROM prepared_spells
        WHERE user_id = ? AND character_name = ? AND spell_level = ?
        """, (interaction.user.id, self.character, self.level))

        current_count = cursor.fetchone()[0]


        added = []
        skipped = []

        for spell in self.selected_spells:

            if max_prepared and current_count >= max_prepared:
                skipped.append(spell)
                continue

            cursor.execute("""
            SELECT 1 FROM prepared_spells
            WHERE user_id = ? AND character_name = ? AND spell_name = ?
            """, (interaction.user.id, self.character, spell))

            if cursor.fetchone():
                skipped.append(spell)
                continue


            cursor.execute("""
            INSERT INTO prepared_spells
            (user_id, character_name, spell_name, spell_level)
            VALUES (?, ?, ?, ?)
            """, (interaction.user.id, self.character, spell, self.level))

            added.append(spell)
            current_count += 1


        conn.commit()


        embed = discord.Embed(
            title=f"{self.character} Spell Preparation",
            color=discord.Color.purple()
        )

        if added:
            embed.add_field(
                name="Prepared",
                value="\n".join(added),
                inline=False
            )

        if skipped:
            embed.add_field(
                name="Skipped",
                value="\n".join(skipped),
                inline=False
            )


        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=None
        )


def get_spells_for_class(level, spell_class):

    filename = f"level_spell_files/level {level} spells.txt"

    spell_class = spell_class.lower()

    try:
        with open(filename, "r", encoding="utf-8") as file:
            lines = [line.strip() for line in file]
    except FileNotFoundError:
        return []

    spells = []

    for line in lines:

        if "(" not in line:
            continue

        name, class_part = line.split("(", 1)
        name = name.strip()

        classes = class_part.replace(")", "").lower()
        class_list = [c.strip() for c in classes.split(",")]

        if spell_class in class_list:
            spells.append(name)

    spells.sort()

    return spells


class SpellSelect(discord.ui.Select):

    def __init__(self, spells):

        options = [
            discord.SelectOption(label=spell, value=spell)
            for spell in spells[:25]
        ]

        super().__init__(
            placeholder="Choose spells to prepare",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):

        view: SpellPrepareView = self.view
        view.selected_spells = self.values

        await interaction.response.send_message(
            f"Selected {len(self.values)} spell(s). Press **Confirm**.",
            ephemeral=True
        )


class CharacterInfoModal(discord.ui.Modal, title="Create Character"):

    name = discord.ui.TextInput(label="Character Name")
    race = discord.ui.TextInput(label="Race")
    character_class = discord.ui.TextInput(label="Class")
    subclass = discord.ui.TextInput(label="Subclass")
    level = discord.ui.TextInput(label="Level")

    async def on_submit(self, interaction: discord.Interaction):

        try:
            level = int(self.level.value)
        except ValueError:
            await interaction.response.send_message(
                "Level must be a number.",
                ephemeral=True
            )
            return

        name = self.name.value
        race = self.race.value
        specialty = self.character_class.value
        subclass = self.subclass.value

        # Insert into DB
        cursor.execute("""
        INSERT INTO characters (user_id, name, level, race, specialty, subclass)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (interaction.user.id, name, level, race, specialty, subclass))

        cursor.execute("""
        INSERT INTO class_levels
        (user_id, character_name, class_name, class_level)
        VALUES (?, ?, ?, ?)
        """, (interaction.user.id, name, specialty.lower(), level))

        conn.commit()

        # Auto generate slots
        caster_level = get_caster_level(interaction.user.id, name)

        update_multiclass_slots(
            interaction.user.id,
            name,
            caster_level
        )

        embed = discord.Embed(
            title="Character Created!",
            color=discord.Color.green()
        )

        embed.add_field(name="Name", value=name)
        embed.add_field(name="Race", value=race)
        embed.add_field(name="Class", value=specialty)
        embed.add_field(name="Subclass", value=subclass)
        embed.add_field(name="Level", value=level)

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

class CharacterWizardStart(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id

    @discord.ui.button(label="Start Character Creation", style=discord.ButtonStyle.green)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This wizard isn't yours.", ephemeral=True)
            return

        await interaction.response.send_modal(CharacterInfoModal())

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
class DeleteCharacterConfirm(discord.ui.View):
    def __init__(self, user_id: int, character: str):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.character = character
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This confirmation isn't for you.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        self.confirmed = True
        self.stop()

        await interaction.response.edit_message(
            content=f"Deleting **{self.character}**...",
            view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        self.stop()
        await interaction.response.edit_message(
            content="Deletion cancelled.",
            view=None
        )

scheduler = AsyncIOScheduler(
    jobstores={'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')}
)


tz = timezone("America/Denver")

bot = commands.Bot(command_prefix="!", intents=intents)

def get_total_level(user_id, character_name):
    cursor.execute("""
    SELECT SUM(class_level)
    FROM class_levels
    WHERE user_id = ? AND character_name = ?
    """, (user_id, character_name))

    result = cursor.fetchone()[0]
    return result or 0

def get_caster_level(user_id, character_name):
    cursor.execute("""
    SELECT class_name, class_level
    FROM class_levels
    WHERE user_id = ? AND character_name = ?
    """, (user_id, character_name))

    rows = cursor.fetchall()

    caster_level = 0

    for class_name, lvl in rows:
        class_name = class_name.lower()

        if class_name in ["wizard", "cleric", "druid", "bard", "sorcerer"]:
            caster_level += lvl

        elif class_name in ["paladin", "ranger"]:
            caster_level += lvl // 2

        elif class_name == "warlock":
            pass  # handled separately if desired

    return caster_level

FULL_CASTER_SLOTS = {
    1:  {1: 2},
    2:  {1: 3},
    3:  {1: 4, 2: 2},
    4:  {1: 4, 2: 3},
    5:  {1: 4, 2: 3, 3: 2},
    6:  {1: 4, 2: 3, 3: 3},
    7:  {1: 4, 2: 3, 3: 3, 4: 1},
    8:  {1: 4, 2: 3, 3: 3, 4: 2},
    9:  {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    10: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    # ... continue to 20
}

HALF_CASTER_SLOTS = {
    1: {},
    2: {1: 2},
    3: {1: 3},
    4: {1: 3},
    5: {1: 4, 2: 2},
    6: {1: 4, 2: 2},
    # ...
}

WARLOCK_SLOTS = {
    1: (1, 1),
    2: (2, 1),
    3: (2, 2),
    4: (2, 2),
    5: (2, 3),
    # tuple = (slots, slot_level)
}

def get_slot_table(character_class):
    character_class = character_class.lower()

    if character_class in ["wizard", "cleric", "druid", "bard", "sorcerer"]:
        return "full"

    if character_class in ["paladin", "ranger"]:
        return "half"

    if character_class == "warlock":
        return "warlock"

    return None

def update_multiclass_slots(user_id, character_name, caster_level):

    cursor.execute("""
    DELETE FROM spell_slots
    WHERE user_id = ? AND character_name = ?
    """, (user_id, character_name))

    slots = FULL_CASTER_SLOTS.get(caster_level, {})

    for spell_level, max_slots in slots.items():
        cursor.execute("""
        INSERT INTO spell_slots
        (user_id, character_name, spell_level, slots_remaining, slots_max)
        VALUES (?, ?, ?, ?, ?)
        """, (user_id, character_name, spell_level, max_slots, max_slots))

    conn.commit()

def update_spell_slots(user_id, character_name, char_class, level):

    table_type = get_slot_table(char_class)

    # Clear old slots
    cursor.execute("""
    DELETE FROM spell_slots
    WHERE user_id = ? AND character_name = ?
    """, (user_id, character_name))

    if table_type == "full":
        slots = FULL_CASTER_SLOTS.get(level, {})

        for spell_level, max_slots in slots.items():
            cursor.execute("""
            INSERT INTO spell_slots
            (user_id, character_name, spell_level, slots_remaining, slots_max)
            VALUES (?, ?, ?, ?, ?)
            """, (user_id, character_name, spell_level, max_slots, max_slots))

    elif table_type == "half":
        slots = HALF_CASTER_SLOTS.get(level, {})

        for spell_level, max_slots in slots.items():
            cursor.execute("""
            INSERT INTO spell_slots
            VALUES (?, ?, ?, ?, ?)
            """, (user_id, character_name, spell_level, max_slots, max_slots))

    elif table_type == "warlock":
        slot_info = WARLOCK_SLOTS.get(level)

        if slot_info:
            slots, slot_level = slot_info

            cursor.execute("""
            INSERT INTO spell_slots
            VALUES (?, ?, ?, ?, ?)
            """, (user_id, character_name, slot_level, slots, slots))

    conn.commit()

async def send_scheduled_message(channel_id: int, message: str):
    channel = bot.get_channel(channel_id)

    if channel:
        await channel.send(message)
    else:
        print(f"Channel {channel_id} not found")



@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=1382030027008512062)

        global_synced = await bot.tree.sync()
        guild_synced = await bot.tree.sync(guild=guild)

        print(f"Logged in as {bot.user}")

        if not scheduler.running:
            scheduler.start()

        print("Scheduler running:", scheduler.running)

        bot_channel_id = 1475775644733542510
        bot_channel = bot.get_channel(bot_channel_id)

        if bot_channel:
            await bot_channel.send("Bot back online!")
        else:
            print("Could not find bot-commands channel")

    except Exception as e:
        print("ERROR in on_ready:", e)


@bot.tree.command(name="rolldice", description="Roll a dice")
async def rolldice(interaction: discord.Interaction, size: int):
    roll = random.randint(1, size)

    if size == 20:
        if roll == 20:
            msg = "NAT 20 BABY!!!"
        elif roll == 1:
            msg = "Nat 1. You fall and break your nose."
        else:
            msg = f"Your roll is: {roll}"
    else:
            msg = f"Your roll is: {roll}"

    await interaction.response.send_message(msg)


@bot.tree.command(name="create_character", description="Create a character")
async def create_character(interaction: discord.Interaction, name: str, race: str, specialty: str, subclass: str, level: int):

    cursor.execute("""
    INSERT INTO characters (user_id, name, level, race, specialty, subclass)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (interaction.user.id, name, level, race, specialty, subclass))

    cursor.execute("""
    INSERT INTO class_levels
    (user_id, character_name, class_name, class_level)
    VALUES (?, ?, ?, ?)
    """, (interaction.user.id, name, specialty.lower(), level))

    conn.commit()

    embed = discord.Embed(
        title="Character Created!",
        color=discord.Color.gold()
    )

    embed.add_field(name="Name", value=name, inline=False)
    embed.add_field(name="Race", value=race)
    embed.add_field(name="Class", value=specialty)
    embed.add_field(name="Subclass", value=subclass)
    embed.add_field(name="Level", value=level)

    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="delete_character", description="Delete one of your characters")
@app_commands.describe(character="Character name")
async def delete_character(interaction: discord.Interaction, character: str):

    # Check ownership + get image path
    cursor.execute("""
    SELECT image_url FROM characters
    WHERE user_id = ? AND name = ?
    """, (interaction.user.id, character))

    row = cursor.fetchone()

    if not row:
        await interaction.response.send_message(
            "Character not found or not yours.",
            ephemeral=True
        )
        return

    image_path = row[0]

    view = DeleteCharacterConfirm(interaction.user.id, character)

    await interaction.response.send_message(
        f"**Are you sure you want to delete `{character}`?**\n"
        "This cannot be undone.",
        view=view,
        ephemeral=True
    )

    await view.wait()

    if not view.confirmed:
        return

    await interaction.followup.send(f"Deleting **{character}**...", ephemeral=True)

    # --- Delete image file if it exists ---
    if image_path and os.path.exists(image_path):
        try:
            os.remove(image_path)
        except Exception as e:
            print(f"Failed to delete image: {e}")

    # --- Delete DB data ---
    cursor.execute("DELETE FROM characters WHERE user_id = ? AND name = ?",
                   (interaction.user.id, character))

    cursor.execute("DELETE FROM spell_slots WHERE user_id = ? AND character_name = ?",
                   (interaction.user.id, character))

    cursor.execute("DELETE FROM prepared_spells WHERE user_id = ? AND character_name = ?",
                   (interaction.user.id, character))

    cursor.execute("DELETE FROM cantrips WHERE user_id = ? AND character_name = ?",
                   (interaction.user.id, character))

    cursor.execute("DELETE FROM prepared_limits WHERE user_id = ? AND character_name = ?",
                   (interaction.user.id, character))

    cursor.execute("DELETE FROM class_levels WHERE user_id = ? AND character_name = ?",
                   (interaction.user.id, character))

    conn.commit()

    await interaction.followup.send(f"**{character}** deleted", ephemeral=True)


@bot.tree.command(name="level_up", description="Level up a class")
@app_commands.describe(
    character="Character name",
    class_name="Class to level up"
)
async def level_up(
    interaction: discord.Interaction,
    character: str,
    class_name: str
):

    class_name = class_name.strip().lower()

    # --- Check class exists ---
    cursor.execute("""
    SELECT class_level
    FROM class_levels
    WHERE user_id = ? AND character_name = ? AND class_name = ?
    """, (interaction.user.id, character, class_name))

    row = cursor.fetchone()

    if not row:
        await interaction.response.send_message(
            f"{character} does not have class **{class_name}**.",
            ephemeral=True
        )
        return

    current_class_level = row[0]
    new_class_level = current_class_level + 1

    # --- Update class level ---
    cursor.execute("""
    UPDATE class_levels
    SET class_level = ?
    WHERE user_id = ? AND character_name = ? AND class_name = ?
    """, (new_class_level, interaction.user.id, character, class_name))

    conn.commit()

    # --- Recalculate TOTAL level ---
    total_level = get_total_level(interaction.user.id, character)

    cursor.execute("""
    UPDATE characters
    SET level = ?
    WHERE user_id = ? AND name = ?
    """, (total_level, interaction.user.id, character))

    conn.commit()

    # --- Recalculate spell slots ---
    caster_level = get_caster_level(interaction.user.id, character)

    update_multiclass_slots(
    interaction.user.id,
    character,
    caster_level
)

    embed = discord.Embed(
        title="Level Up!",
        description=(
            f"**{character}** leveled up!\n"
            f"**{class_name.title()} → Level {new_class_level}**\n"
            f"Total Level: **{total_level}**"
        ),
        color=discord.Color.gold()
    )

    embed.add_field(
        name="Spell Slots",
        value="Recalculated using multiclass rules.",
        inline=False
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="add_class", description="Add a multiclass")
@app_commands.describe(character="Character name", class_name="Class to add")
async def add_class(interaction: discord.Interaction, character: str, class_name: str):

    class_name = class_name.lower()

    # Check character exists
    cursor.execute("""
    SELECT name FROM characters
    WHERE user_id = ? AND name = ?
    """, (interaction.user.id, character))

    if not cursor.fetchone():
        await interaction.response.send_message("Character not found.", ephemeral=True)
        return

    # Check if class already exists
    cursor.execute("""
    SELECT class_level FROM class_levels
    WHERE user_id = ? AND character_name = ? AND class_name = ?
    """, (interaction.user.id, character, class_name))

    if cursor.fetchone():
        await interaction.response.send_message(
            f"{character} already has {class_name.title()}.",
            ephemeral=True
        )
        return

    # Add class at level 1
    cursor.execute("""
    INSERT INTO class_levels (user_id, character_name, class_name, class_level)
    VALUES (?, ?, ?, 1)
    """, (interaction.user.id, character, class_name))

    conn.commit()

    await interaction.response.send_message(
        f"{character} is now multiclassed into {class_name.title()} (Level 1)."
    )

@bot.tree.command(name="my_characters", description="View your characters")
async def my_characters(interaction: discord.Interaction):

    cursor.execute("""
    SELECT name, level, race, specialty, subclass, image_url
    FROM characters
    WHERE user_id = ?
    """, (interaction.user.id,))

    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "You have no characters.",
            ephemeral=True
        )
        return

    await interaction.response.defer()  # Allows multiple followups

    for name, level, race, specialty, subclass, image_path in rows:

        embed = discord.Embed(
            title=name,
            description=f"Level {level} {race}",
            color=discord.Color.green()
        )

        embed.add_field(
            name="Class",
            value=f"{specialty} ({subclass})",
            inline=False
        )

        embed.set_footer(text=interaction.user.display_name)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        # If character has a saved image file
        if image_path and os.path.exists(image_path):

            file = discord.File(image_path, filename="portrait.png")
            embed.set_image(url="attachment://portrait.png")

            await interaction.followup.send(embed=embed, file=file)

        else:
            await interaction.followup.send(embed=embed)


@bot.tree.command(name="set_prepared_limit", description="Set prepared spell limits")
@app_commands.describe(
    character="Character name",
    cantrips="Max prepared cantrips",
    level1="Max level 1 spells",
    level2="Max level 2 spells",
    level3="Max level 3 spells",
    level4="Max level 4 spells",
    level5="Max level 5 spells",
    level6="Max level 6 spells",
    level7="Max level 7 spells",
    level8="Max level 8 spells",
    level9="Max level 9 spells"
)
async def set_prepared_limit(
    interaction: discord.Interaction,
    character: str,
    cantrips: int | None = None,
    level1: int | None = None,
    level2: int | None = None,
    level3: int | None = None,
    level4: int | None = None,
    level5: int | None = None,
    level6: int | None = None,
    level7: int | None = None,
    level8: int | None = None,
    level9: int | None = None
):

    levels = {
        0: cantrips,
        1: level1,
        2: level2,
        3: level3,
        4: level4,
        5: level5,
        6: level6,
        7: level7,
        8: level8,
        9: level9
    }

    changes = []

    for lvl, value in levels.items():

        if value is None:
            continue

        if value < 0:
            await interaction.response.send_message(
                "Prepared limits must be positive numbers.",
                ephemeral=True
            )
            return

        cursor.execute("""
        INSERT OR REPLACE INTO prepared_limits
        (user_id, character_name, spell_level, max_prepared)
        VALUES (?, ?, ?, ?)
        """, (interaction.user.id, character, lvl, value))

        level_name = "Cantrips" if lvl == 0 else f"Level {lvl}"
        changes.append(f"{level_name}: {value}")

    conn.commit()

    if not changes:
        await interaction.response.send_message(
            "No limits were set.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"{character} Prepared Spell Limits Updated",
        description="\n".join(changes),
        color=discord.Color.green()
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(
    name="prepare_spell",
    description="Prepare spells from a selected level"
)
async def prepare_spell(
    interaction: discord.Interaction,
    character: str,
    level: int
):

    cursor.execute("""
    SELECT class_name
    FROM class_levels
    WHERE user_id = ? AND character_name = ?
    """, (interaction.user.id, character))

    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "No classes found for this character.",
            ephemeral=True
        )
        return

    classes = [row[0] for row in rows]

    spells = load_spells_for_classes(level, classes)

    if not spells:
        await interaction.response.send_message(
            f"No level {level} spells available.",
            ephemeral=True
        )
        return

    view = SpellPageView(
    spells,
    interaction.user.id,
    character,
    level
)

    await interaction.response.send_message(
    f"Choose spells to prepare for **{character}** (Level {level})",
    view=view,
    ephemeral=True
)

    
@bot.tree.command(
    name="unprepare_spell",
    description="Unprepare spells"
)
async def unprepare_spell(interaction: discord.Interaction, character: str):

    cursor.execute("""
    SELECT spell_name
    FROM prepared_spells
    WHERE user_id = ? AND character_name = ?
    """, (interaction.user.id, character))

    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            f"**{character}** has no prepared spells.",
            ephemeral=True
        )
        return

    spells = [row[0] for row in rows]

    view = UnprepareSpellView(
        interaction.user.id,
        character,
        spells
    )

    await interaction.response.send_message(
        f"Click spells to unprepare them for **{character}**.",
        view=view,
        ephemeral=True
    )


@bot.tree.command(name="set_spell_slots", description="Set spell slots")
async def set_spell_slots(interaction: discord.Interaction, character: str, level: int, max_slots: int):

    if level < 1 or level > 9:
        await interaction.response.send_message("Spell level must be 1–9.", ephemeral=True)
        return

    cursor.execute("""
    INSERT OR REPLACE INTO spell_slots
    (user_id, character_name, spell_level, slots_remaining, slots_max)
    VALUES (?, ?, ?, ?, ?)
    """, (interaction.user.id, character, level, max_slots, max_slots))

    conn.commit()

    await interaction.response.send_message(
        f"Level {level} slots set to {max_slots}"
    )

@bot.tree.command(name="list_prepared_spells", description="List prepared spells and cantrips")
@app_commands.describe(character="Character name")
async def list_prepared_spells(
    interaction: discord.Interaction,
    character: str
):
    # Fetch leveled spells
    cursor.execute("""
    SELECT spell_name, spell_level
    FROM prepared_spells
    WHERE user_id = ? AND character_name = ?
    ORDER BY spell_level, spell_name
    """, (interaction.user.id, character))

    spell_rows = cursor.fetchall()

    # Fetch cantrips
    cursor.execute("""
    SELECT spell_name
    FROM cantrips
    WHERE user_id = ? AND character_name = ?
    ORDER BY spell_name
    """, (interaction.user.id, character))

    cantrip_rows = cursor.fetchall()

    if not spell_rows and not cantrip_rows:
        await interaction.response.send_message(
            "No spells or cantrips prepared.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"{character}'s Prepared Spells",
        color=discord.Color.purple()
    )

    # Add Cantrips first
    if cantrip_rows:
        cantrips = [row[0] for row in cantrip_rows]

        embed.add_field(
            name="Cantrips",
            value="\n".join(cantrips),
            inline=False
        )

    #Group leveled spells
    spells_by_level = {}

    for name, lvl in spell_rows:
        spells_by_level.setdefault(lvl, []).append(name)

    for lvl in sorted(spells_by_level):
        embed.add_field(
            name=f"Level {lvl}",
            value="\n".join(spells_by_level[lvl]),
            inline=False
        )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="cast_spell", description="Cast a prepared spell")
@app_commands.describe(
    character="Character name",
    spell_name="Spell to cast",
    cast_level="Optional: level to cast the spell at"
)
async def cast_spell(
    interaction: discord.Interaction,
    character: str,
    spell_name: str,
    cast_level: int | None = None
):
    # Check prepared spell
    cursor.execute("""
    SELECT spell_level FROM prepared_spells
    WHERE user_id = ? AND character_name = ? AND spell_name = ?
    """, (interaction.user.id, character, spell_name))

    row = cursor.fetchone()

    if not row:
        await interaction.response.send_message(
            f"**{spell_name}** is not prepared.",
            ephemeral=True
        )
        return

    base_level = row[0]

    # Determine actual cast level
    actual_level = cast_level or base_level

    if actual_level < base_level:
        await interaction.response.send_message(
            f"You cannot cast **{spell_name}** below level {base_level}.",
            ephemeral=True
        )
        return

    # Check slots at chosen level
    cursor.execute("""
    SELECT slots_remaining FROM spell_slots
    WHERE user_id = ? AND character_name = ? AND spell_level = ?
    """, (interaction.user.id, character, actual_level))

    slot_row = cursor.fetchone()

    if not slot_row:
        await interaction.response.send_message(
            f"No level {actual_level} slots available.",
            ephemeral=True
        )
        return

    remaining = slot_row[0]

    if remaining <= 0:
        await interaction.response.send_message(
            f"No level {actual_level} slots remaining.",
            ephemeral=True
        )
        return

    # Deduct slot
    cursor.execute("""
    UPDATE spell_slots
    SET slots_remaining = ?
    WHERE user_id = ? AND character_name = ? AND spell_level = ?
    """, (remaining - 1, interaction.user.id, character, actual_level))

    conn.commit()

    # Response text
    if actual_level == base_level:
        text = (
            f"**{spell_name}** cast at level {base_level}!\n"
            f"Slots remaining: **{remaining - 1}**"
        )
    else:
        text = (
            f"**{spell_name}** upcast at level {actual_level}!\n"
            f"Base Level: {base_level}\n"
            f"Slots remaining: **{remaining - 1}**"
        )

    await interaction.response.send_message(text)

@bot.tree.command(name="show_spell_slots", description="View spell slots")
@app_commands.describe(character="Character name")
async def show_spell_slots(
    interaction: discord.Interaction,
    character: str
):
    cursor.execute("""
    SELECT spell_level, slots_remaining, slots_max
    FROM spell_slots
    WHERE user_id = ? AND character_name = ?
    ORDER BY spell_level
    """, (interaction.user.id, character))

    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "No spell slots set.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title=f"{character}'s Spell Slots",
        color=discord.Color.blue()
    )

    for level, remaining, max_slots in rows:
        embed.add_field(
            name=f"Level {level}",
            value=f"{remaining} / {max_slots}",
            inline=True
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="long_rest", description="Restore spell slots")
async def long_rest(interaction: discord.Interaction, character: str):

    cursor.execute("""
    UPDATE spell_slots
    SET slots_remaining = slots_max
    WHERE user_id = ? AND character_name = ?
    """, (interaction.user.id, character))

    conn.commit()

    view = LongRestView(interaction.user.id, character)

    await interaction.response.send_message(
        f"**{character}** is fully rested. Slots restored!\n\nDo you want to prepare spells again?",
        view=view
    )



@bot.tree.command(name="schedule_message", description="Schedule a message")
@app_commands.describe(
    date="Format: YYYY-MM-DD",
    time="Format: HH:MM (24-hour)",
    message="Message content",
    channel="Channel to send the message in",
    repeat="Repeat the message"
)
@app_commands.choices(repeat=[
    app_commands.Choice(name="No repeat", value="none"),
    app_commands.Choice(name="Daily", value="daily"),
    app_commands.Choice(name="Weekly", value="weekly")
])
async def schedule_message(
    interaction: discord.Interaction,
    date: str,
    time: str,
    message: str,
    repeat: app_commands.Choice[str],
    channel: discord.TextChannel | None = None
):
    try:
        dt_string = f"{date} {time}"
        run_time = tz.localize(datetime.strptime(dt_string, "%Y-%m-%d %H:%M"))
    except ValueError:
        await interaction.response.send_message(
            "Invalid date or time.\n"
            "Date → YYYY-MM-DD\n"
            "Time → HH:MM (24h)",
            ephemeral=True
        )
        return

    target_channel = channel or interaction.channel

    if not target_channel:
        await interaction.response.send_message(
            "Invalid channel.",
            ephemeral=True
        )
        return

    job_id = f"{interaction.user.id}-{int(run_time.timestamp())}"

    if repeat.value == "daily":
        scheduler.add_job(
            send_scheduled_message,
            trigger="interval",
            days=1,
            start_date=run_time,
            args=[target_channel.id, message],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600
        )
        repeat_text = "Repeats **daily**"

    elif repeat.value == "weekly":
        scheduler.add_job(
            send_scheduled_message,
            trigger="interval",
            weeks=1,
            start_date=run_time,
            args=[target_channel.id, message],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600
        )
        repeat_text = "Repeats **weekly**"

    else:
        scheduler.add_job(
            send_scheduled_message,
            trigger="date",
            run_date=run_time,
            args=[target_channel.id, message],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=3600
        )
        repeat_text = "One-time message"

    await interaction.response.send_message(
        f"Message scheduled for "
        f"{run_time.strftime('%Y-%m-%d %H:%M')} "
        f"in {target_channel.mention}\n"
        f"{repeat_text}\n"
        f"**Job ID:** `{job_id}`"
    )

@bot.tree.command(name="cancel_scheduled", description="Cancel a scheduled message")
async def cancel_scheduled(
    interaction: discord.Interaction,
    job_id: str
):
    job = scheduler.get_job(job_id)

    if not job:
        await interaction.response.send_message(
            "Job not found.",
            ephemeral=True
        )
        return

    if not job.id.startswith(str(interaction.user.id)):
        await interaction.response.send_message(
            "You can only cancel your own jobs.",
            ephemeral=True
        )
        return

    scheduler.remove_job(job_id)

    await interaction.response.send_message("Scheduled message cancelled.")

@bot.tree.command(name="list_scheduled", description="List your scheduled messages")
async def list_scheduled(interaction: discord.Interaction):

    jobs = scheduler.get_jobs()
    user_jobs = [job for job in jobs if job.id.startswith(str(interaction.user.id))]

    if not user_jobs:
        await interaction.response.send_message(
            "You have no scheduled messages.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Your Scheduled Messages",
        color=discord.Color.blurple()
    )

    for job in user_jobs:
        embed.add_field(
            name=f"Job ID: {job.id}",
            value=f"Runs at: {job.next_run_time.strftime('%Y-%m-%d %H:%M')}",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="spell_book",
    description="List available spells by level and class"
)
@app_commands.describe(
    level="Spell level (0–9)",
    spell_class="Class name"
)
async def spell_book(interaction: discord.Interaction, level: str, spell_class: str):

    # --- Convert level safely ---
    try:
        level = int(level)
    except ValueError:
        await interaction.response.send_message(
            "Spell level must be a number.",
            ephemeral=True
        )
        return

    if level < 0 or level > 9:
        await interaction.response.send_message(
            "Spell level must be 0–9.",
            ephemeral=True
        )
        return

    filename = f"level_spell_files/level {level} spells.txt"

    # Normalize user input
    spell_class = spell_class.strip().lower()

    try:
        # --- Read & clean lines ---
        with open(filename, "r", encoding="utf-8") as file:
            lines = [line.strip() for line in file]

        filtered_spells = []

        for line in lines:

            if "(" not in line:
                continue  # skip malformed lines

            name, class_part = line.split("(", 1)
            name = name.strip()

            # Remove ")" and normalize
            class_list = class_part.replace(")", "").strip().lower()
            classes = [c.strip() for c in class_list.split(",")]

            if spell_class in classes:
                filtered_spells.append(name)

        if not filtered_spells:
            await interaction.response.send_message(
                f"No level {level} spells for **{spell_class}**.",
                ephemeral=True
            )
            return

        filtered_spells.sort()
        spell_text = "\n".join(filtered_spells)

        # --- Discord embed description limit ---
        if len(spell_text) > 4000:
            spell_text = spell_text[:4000] + "\n..."

        embed = discord.Embed(
            title=f"Level {level} – {spell_class.title()} Spells",
            description=spell_text,
            color=discord.Color.blurple()
        )

        embed.set_footer(text=f"{len(filtered_spells)} spells found")

        await interaction.response.send_message(embed=embed)

    except FileNotFoundError:
        await interaction.response.send_message(
            f"No spell file found for level {level}.",
            ephemeral=True
        )

@bot.tree.command(name="help", description="Show all bot commands")
async def help_command(interaction: discord.Interaction):

    embed = discord.Embed(
        title="D&D Bot Command Guide",
        description="Here are all available commands:",
        color=discord.Color.blurple()
    )

    # Utility
    embed.add_field(
        name=" Utility",
        value=(
            "`/rolldice size` — Roll any size die (ex: d20)\n"
        ),
        inline=False
    )

    # Character Management
    embed.add_field(
        name="Character Management",
        value=(
            "`/create_character` — Create a character quickly\n"
            "`/character_wizard` — Guided character creation\n"
            "`/my_characters` — View your characters\n"
            "`/delete_character` — Delete a character\n"
            "`/set_character_image` — Attach a portrait\n"
            "`/add_class` — Add a multiclass\n"
            "`/level_up` — Level up a class\n"
        ),
        inline=False
    )

    # Spell Preparation
    embed.add_field(
        name="Spell Preparation",
        value=(
            "`/spell_book` — View spells by level & class\n"
            "`/set_prepared_limit` — Set prepared spell limits\n"
            "`/prepare_spell` — Prepare spells (paged selector)\n"
            "`/unprepare_spell` — Remove prepared spells\n"
            "`/list_prepared_spells` — View prepared spells\n"
        ),
        inline=False
    )

    # Spell Casting
    embed.add_field(
        name="Spell Casting",
        value=(
            "`/cast_spell` — Cast a prepared spell\n"
            "`/show_spell_slots` — View spell slots\n"
            "`/set_spell_slots` — Manually set slots\n"
            "`/long_rest` — Restore spell slots\n"
        ),
        inline=False
    )

    # Scheduling
    embed.add_field(
        name="Scheduling",
        value=(
            "`/schedule_message` — Schedule a message\n"
            "`/list_scheduled` — View scheduled messages\n"
            "`/cancel_scheduled` — Cancel a scheduled message\n"
        ),
        inline=False
    )

    embed.set_footer(text="Tip: Type / to view command autocomplete")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="set_character_image", description="Attach an image to your character")
@app_commands.describe(
    character="Character name",
    image="Upload an image file"
)
async def set_character_image(
    interaction: discord.Interaction,
    character: str,
    image: discord.Attachment
):
    # Validate image type
    if not image.content_type or not image.content_type.startswith("image/"):
        await interaction.response.send_message(
            "That file is not an image.",
            ephemeral=True
        )
        return

    # Confirm character exists
    cursor.execute("""
    SELECT 1 FROM characters
    WHERE user_id = ? AND name = ?
    """, (interaction.user.id, character))

    if not cursor.fetchone():
        await interaction.response.send_message(
            "Character not found.",
            ephemeral=True
        )
        return

    # Create folder if needed
    os.makedirs("character_images", exist_ok=True)

    if image.size > 2_000_000:
        await interaction.response.send_message(
        "Image must be under 2MB.",
        ephemeral=True
    )
        return

    # Safe filename
    safe_name = character.replace(" ", "_")
    filename = f"{interaction.user.id}_{safe_name}.png"
    filepath = os.path.join("character_images", filename)

    # Save file
    await image.save(filepath)

    # Store file path in DB
    cursor.execute("""
    UPDATE characters
    SET image_url = ?
    WHERE user_id = ? AND name = ?
    """, (filepath, interaction.user.id, character))

    conn.commit()

    await interaction.response.send_message(
        f"Image saved for **{character}**!"
    )

@bot.tree.command(name="character_wizard", description="Guided character creation")
async def character_wizard(interaction: discord.Interaction):

    view = CharacterWizardStart(interaction.user.id)

    await interaction.response.send_message(
        "Welcome to the **Character Creation Wizard**!\n"
        "Press **Start** to begin.",
        view=view,
        ephemeral=True
    )
@bot.tree.command(name= "feedback", description="Enter feedback to help make this bot better!")
async def feedback(interaction: discord.interactions, message: str):
    if not message.strip():
        await interaction.response.send_message(
            "Please enter some feedback.",
            ephemeral=True
        )
        return

    # format entry
    entry = f"{interaction.user} ({interaction.user.id}): {message}\n"

    # append to file
    with open("feedback.txt", "a", encoding="utf-8") as f:
        f.write(entry)

    await interaction.response.send_message(
        "Thanks for your feedback!",
        ephemeral=True
    )


while True:
    try:
        print("Starting bot...")
        bot.run(token, log_handler=handler, log_level=logging.DEBUG)
    except discord.errors.DiscordServerError:
        print("Discord API unavailable (502). Retrying in 10 seconds...")
        time.sleep(10)
    except discord.LoginFailure:
        print("Invalid token. Check your .env file.")
        break
    except Exception as e:
        print("Unexpected error:", e)
        break