import discord
from discord.ext import commands
from discord import app_commands
import os

from db import cursor, conn

class Character(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        