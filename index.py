import os
import random
import time
import discord
import wavelink

from dotenv import load_dotenv
from discord.ext import commands
from discord.ui import View

# ---------- LOAD TOKEN ----------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env")

# ---------- BOT SETUP ----------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")

# ---------- STORAGE ----------
queue = []
song_history = []
loop_enabled = False
current_player = None
current_song_query = None
song_start_time = None

# ---------- READY / LAVALINK ----------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="!play music"
        )
    )

    if not wavelink.Pool.nodes:
        try:
            node = wavelink.Node(
                uri="https://lavalink-2026-production-a48f.up.railway.app/",
                password="youshallnotpass"
            )

            await wavelink.Pool.connect(nodes=[node], client=bot)
            print("✅ Connected to Lavalink")

        except Exception as e:
            print(f"❌ Lavalink connection failed: {e}")

# ---------- BUTTONS ----------
class MusicControls(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⏮ Back", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(song_history) < 2:
            return await interaction.response.send_message(
                "❌ No previous song available.",
                ephemeral=True
            )

        current_song = song_history.pop()
        previous_song = song_history.pop()

        queue.insert(0, previous_song)
        queue.insert(1, current_song)

        vc: wavelink.Player = interaction.guild.voice_client

        if vc:
            await vc.stop()

        await interaction.response.send_message(
            "⏮ Playing previous song...",
            ephemeral=True
        )

    @discord.ui.button(label="⏯ Pause/Resume", style=discord.ButtonStyle.primary)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc: wavelink.Player = interaction.guild.voice_client

        if not vc:
            return await interaction.response.send_message(
                "❌ Bot is not connected.",
                ephemeral=True
            )

        if vc.paused:
            await vc.pause(False)
            await interaction.response.send_message(
                "▶ Music resumed.",
                ephemeral=True
            )
        else:
            await vc.pause(True)
            await interaction.response.send_message(
                "⏸ Music paused.",
                ephemeral=True
            )

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.success)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc: wavelink.Player = interaction.guild.voice_client

        if vc and vc.current:
            await vc.skip()
            return await interaction.response.send_message(
                "⏭ Skipped current song.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "❌ Nothing is playing.",
            ephemeral=True
        )

    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.secondary)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global loop_enabled

        loop_enabled = not loop_enabled

        await interaction.response.send_message(
            f"🔁 Loop {'enabled' if loop_enabled else 'disabled'}",
            ephemeral=True
        )

# ---------- PLAY NEXT ----------
async def play_next(ctx):
    global current_player, current_song_query, song_start_time

    if loop_enabled and current_song_query:
        queue.insert(0, current_song_query)

    if not queue:
        current_player = None
        current_song_query = None
        return

    next_song = queue.pop(0)
    song_history.append(next_song)
    current_song_query = next_song
    song_start_time = time.time()

    try:
        tracks = await wavelink.Playable.search(next_song)

        if not tracks:
            return await ctx.send("❌ Song not found.")

        track = tracks[0]
        current_player = track

    except Exception as e:
        return await ctx.send(f"❌ Error while searching: `{e}`")

    vc: wavelink.Player = ctx.voice_client

    if not vc:
        return

    await vc.play(track)

    embed = discord.Embed(
        title="🎶 Now Playing",
        description=(
            f"**{track.title}**\n\n"
            f"👤 Requested by: {ctx.author.mention}"
        ),
        color=0x2B2D31
    )

    if getattr(track, "artwork", None):
        embed.set_thumbnail(url=track.artwork)

    await ctx.send(embed=embed, view=MusicControls())

# ---------- AUTO NEXT SONG ----------
@bot.event
async def on_wavelink_track_end(payload):
    guild = bot.get_guild(payload.player.guild.id)

    if not guild:
        return

    text_channel = None

    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            text_channel = channel
            break

    if not text_channel:
        return

    class FakeContext:
        def __init__(self):
            self.guild = guild
            self.voice_client = payload.player
            self.author = guild.me

        async def send(self, *args, **kwargs):
            return await text_channel.send(*args, **kwargs)

    await play_next(FakeContext())

# ---------- VOICE ----------
async def ensure_voice(ctx):
    if not ctx.author.voice:
        await ctx.send("❌ Join a voice channel first.")
        return False

    channel = ctx.author.voice.channel

    if not ctx.voice_client:
        await channel.connect(cls=wavelink.Player)
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    return True

# ---------- COMMANDS ----------
@bot.command()
async def play(ctx, *, query):
    if not await ensure_voice(ctx):
        return

    queue.append(query)

    await ctx.send(embed=discord.Embed(
        description=f"➕ Added to queue: `{query}`",
        color=0x2B2D31
    ))

    vc: wavelink.Player = ctx.voice_client

    if not vc.current:
        await play_next(ctx)

@bot.command()
async def p(ctx, *, query):
    await play(ctx, query=query)

@bot.command(name="queue")
async def queue_command(ctx):
    if not queue:
        return await ctx.send(embed=discord.Embed(
            description="📭 Queue is empty.",
            color=0x2B2D31
        ))

    desc = "\n".join(
        f"`{i + 1}.` {song}"
        for i, song in enumerate(queue[:10])
    )

    await ctx.send(embed=discord.Embed(
        title="📜 Current Queue",
        description=desc,
        color=0x2B2D31
    ))

@bot.command()
async def skip(ctx):
    vc: wavelink.Player = ctx.voice_client

    if vc and vc.current:
        await vc.skip()
        await ctx.send("⏭ Skipped.")

@bot.command()
async def pause(ctx):
    vc: wavelink.Player = ctx.voice_client

    if vc and vc.current:
        await vc.pause(True)
        await ctx.send("⏸ Paused.")

@bot.command()
async def resume(ctx):
    vc: wavelink.Player = ctx.voice_client

    if vc:
        await vc.pause(False)
        await ctx.send("▶ Resumed.")

@bot.command()
async def stop(ctx):
    queue.clear()

    vc: wavelink.Player = ctx.voice_client

    if vc:
        await vc.stop()

    await ctx.send("⏹ Stopped.")

@bot.command()
async def leave(ctx):
    queue.clear()
    song_history.clear()

    if ctx.voice_client:
        await ctx.voice_client.disconnect()

    await ctx.send("👋 Left voice channel.")

@bot.command()
async def volume(ctx, vol: int):
    vol = max(0, min(vol, 100))

    vc: wavelink.Player = ctx.voice_client

    if vc:
        await vc.set_volume(vol)

    await ctx.send(f"🔊 Volume set to {vol}%")

@bot.command(aliases=["np"])
async def nowplaying(ctx):
    if not current_player:
        return await ctx.send("❌ Nothing is playing.")

    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{current_player.title}**",
        color=0x2B2D31
    )

    await ctx.send(embed=embed)

@bot.command()
async def shuffle(ctx):
    random.shuffle(queue)
    await ctx.send("🔀 Shuffled queue.")

@bot.command()
async def loop(ctx):
    global loop_enabled
    loop_enabled = not loop_enabled
    await ctx.send(
        f"🔁 Loop {'enabled' if loop_enabled else 'disabled'}"
    )

@bot.command()
async def history(ctx):
    if not song_history:
        return await ctx.send("📭 No history.")

    desc = "\n".join(
        f"`{i + 1}.` {song}"
        for i, song in enumerate(song_history[-10:][::-1])
    )

    await ctx.send(embed=discord.Embed(
        title="🕘 History",
        description=desc,
        color=0x2B2D31
    ))

@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🎵 Music Commands",
        color=0x2B2D31
    )

    embed.add_field(
        name="Playback",
        value="`!play` `!p` `!skip` `!pause` `!resume` `!stop` `!leave`",
        inline=False
    )

    embed.add_field(
        name="Queue",
        value="`!queue` `!shuffle` `!history` `!loop`",
        inline=False
    )

    embed.add_field(
        name="Extra",
        value="`!volume <0-100>` `!nowplaying`",
        inline=False
    )

    await ctx.send(embed=embed)

# ---------- RUN ----------
bot.run(TOKEN)