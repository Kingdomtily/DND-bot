import sqlite3
import atexit

conn = sqlite3.connect("characters.sqlite")
cursor = conn.cursor()

def init_db():
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS characters (
        user_id INTEGER,
        name TEXT,
        level INTEGER,
        race TEXT,
        specialty TEXT,
        subclass TEXT,
        image_url TEXT
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
    conn.commit()

atexit.register(conn.close)