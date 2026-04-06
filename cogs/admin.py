"""
Administrative slash commands for guild configuration (welcome, ping window, channels, roles).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.database import Database
from utils.checks import ensure_admin_or_botmod
from utils.time_utils import parse_hhmm

logger = logging.getLogger(__name__)

_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _parse_embed_color(raw: str) -> discord.Color:
    """Parse #RRGGBB or RRGGBB; fall back to blurple."""
    s = raw.strip()
    if not s:
        return discord.Color.blurple()
    m = _HEX_COLOR.match(s)
    if m:
        return discord.Color(int(m.group(1), 16))
    return discord.Color.blurple()


class EmbedSendModal(discord.ui.Modal, title="Send embed"):
    """Collect title, body, color, footer, and optional image; post to a fixed channel on submit."""

    def __init__(self, channel: discord.TextChannel) -> None:
        super().__init__()
        self._channel = channel
        self._title = discord.ui.TextInput(
            label="Title (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=256,
        )
        self._description = discord.ui.TextInput(
            label="Description",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
        )
        self._color = discord.ui.TextInput(
            label="Color (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=7,
            placeholder="#5865F2",
        )
        self._footer = discord.ui.TextInput(
            label="Footer (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=2048,
        )
        self._image_url = discord.ui.TextInput(
            label="Image URL (optional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=2000,
            placeholder="https://…",
        )
        for item in (
            self._title,
            self._description,
            self._color,
            self._footer,
            self._image_url,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        desc = str(self._description.value).strip()
        if not desc:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Invalid embed",
                    description="Description cannot be empty.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        title_text = str(self._title.value).strip()
        footer_text = str(self._footer.value).strip()
        image_raw = str(self._image_url.value).strip()

        embed = discord.Embed(
            title=title_text or None,
            description=desc,
            color=_parse_embed_color(str(self._color.value)),
        )
        if footer_text:
            embed.set_footer(text=footer_text)
        if image_raw:
            if not image_raw.startswith(("http://", "https://")):
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Invalid image URL",
                        description="Image URL must start with `http://` or `https://`.",
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )
                return
            embed.set_image(url=image_raw)

        try:
            await self._channel.send(embed=embed)
        except discord.HTTPException as e:
            logger.exception("sendembed failed: %s", e)
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Could not send",
                    description=f"The bot could not post in {self._channel.mention}. Check permissions (Send Messages, Embed Links) and try again.\n`{e}`",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Embed sent",
                description=f"Posted to {self._channel.mention}.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class WelcomeMessageModal(discord.ui.Modal, title="Set Welcome Message"):
    message = discord.ui.TextInput(
        label="Welcome message template",
        style=discord.TextStyle.paragraph,
        placeholder="Use placeholders like {user}, {server}, {verification_channel}, {staff_channel}",
        required=True,
        max_length=2000,
    )

    def __init__(self, db: Database, guild_id: int, initial_message: str) -> None:
        super().__init__()
        self._db = db
        self._guild_id = guild_id
        self.message.default = initial_message[:2000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.message.value).strip()
        if not text:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Invalid message",
                    description="Welcome message cannot be empty.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await self._db.update_guild_settings(self._guild_id, welcome_message=text)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Welcome message updated",
                description="Saved. New members will receive this template.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


class Admin(commands.Cog):
    """Guild settings via slash commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    # --- Welcome ---

    @app_commands.command(name="welcomemessageactive", description="Enable or disable welcome messages for new members.")
    @app_commands.describe(
        enabled="Whether the bot should DM new members using your welcome text.",
    )
    @app_commands.choices(
        enabled=[
            app_commands.Choice(name="on", value=1),
            app_commands.Choice(name="off", value=0),
        ]
    )
    async def welcomemessage_active(
        self,
        interaction: discord.Interaction,
        enabled: int,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        on = bool(enabled)
        await self.db.update_guild_settings(interaction.guild.id, welcome_enabled=on)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Welcome messages",
                description=f"Welcome messages are now **{'enabled' if on else 'disabled'}**.",
                color=discord.Color.green() if on else discord.Color.orange(),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="welcomemessageset",
        description="Set welcome message text. Placeholders: {user}, {server}, {verification_channel}",
    )
    async def welcomemessage_set(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return
        initial = (settings.welcome_message or "").strip()
        await interaction.response.send_modal(WelcomeMessageModal(self.db, interaction.guild.id, initial))

    @app_commands.command(
        name="setwelcomemessagechannel",
        description="Set the channel where the welcome message is posted for new members.",
    )
    @app_commands.describe(channel="Channel to post welcome message into")
    async def welcome_message_channel_set(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        await self.db.update_guild_settings(interaction.guild.id, welcome_channel_id=channel.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Welcome channel updated",
                description=f"Welcome messages will be posted in {channel.mention}.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    # --- Ping window ---

    @app_commands.command(
        name="pingtimeactive",
        description="Enable or disable role pings on new applications (day window + optional after-hours role).",
    )
    @app_commands.choices(
        enabled=[
            app_commands.Choice(name="on", value=1),
            app_commands.Choice(name="off", value=0),
        ]
    )
    async def pingtime_active(
        self,
        interaction: discord.Interaction,
        enabled: int,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        on = bool(enabled)
        await self.db.update_guild_settings(interaction.guild.id, ping_enabled=on)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Ping window",
                description=(
                    f"Application role pings are **{'enabled' if on else 'disabled'}**. "
                    "When on: day role inside `/pingtimeset` (Eastern), night role outside that window if `/setnightpingrole` is set."
                ),
                color=discord.Color.green() if on else discord.Color.orange(),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="pingtimeset",
        description="Set the Eastern (America/New_York) time window when staff may be pinged. Use 24h HH:MM.",
    )
    @app_commands.describe(
        start="Start time, e.g. 09:00",
        end="End time, e.g. 21:00",
    )
    async def pingtime_set(
        self,
        interaction: discord.Interaction,
        start: str,
        end: str,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        if parse_hhmm(start) is None or parse_hhmm(end) is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Invalid time",
                    description="Use **HH:MM** in 24-hour format (e.g. `09:00` and `21:00`). Times are interpreted in **Eastern (America/New_York)**.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await self.db.update_guild_settings(interaction.guild.id, ping_start=start.strip(), ping_end=end.strip())
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Ping window updated",
                description=f"Staff ping window (Eastern): **{start.strip()}** → **{end.strip()}**",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @app_commands.command(name="setpingrole", description="Role to mention when pings are allowed inside the time window.")
    @app_commands.describe(role="Role to ping; omit to clear")
    async def set_ping_role(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        rid = role.id if role else None
        await self.db.update_guild_settings(interaction.guild.id, ping_role_id=rid)
        desc = f"Ping role set to {role.mention}." if role else "Ping role cleared."
        await interaction.response.send_message(
            embed=discord.Embed(title="Ping role", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(
        name="setnightpingrole",
        description="Role to ping on new applications outside the Eastern ping window (e.g. night shift).",
    )
    @app_commands.describe(role="Night / after-hours role; omit to clear")
    async def set_night_ping_role(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        rid = role.id if role else None
        await self.db.update_guild_settings(interaction.guild.id, night_ping_role_id=rid)
        desc = f"After-hours ping role set to {role.mention}." if role else "After-hours ping role cleared."
        await interaction.response.send_message(
            embed=discord.Embed(title="Night ping role", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(name="setstaffchannel", description="Channel where verification submissions are posted for staff.")
    @app_commands.describe(channel="Staff review channel")
    async def set_staff_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        await self.db.update_guild_settings(interaction.guild.id, staff_channel_id=channel.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Staff channel",
                description=f"Submissions will be sent to {channel.mention}.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="setunverifiedrole",
        description="Role assigned to new members on join (optional). Omit to clear.",
    )
    @app_commands.describe(role="Unverified role to assign")
    async def set_unverified_role(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        rid = role.id if role else None
        await self.db.update_guild_settings(interaction.guild.id, unverified_role_id=rid)
        desc = f"Unverified role set to {role.mention}." if role else "Unverified role cleared."
        await interaction.response.send_message(
            embed=discord.Embed(title="Unverified role", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(
        name="setbotmodrole",
        description="Role that may use admin commands (in addition to Administrator / Manage Server). Omit to clear.",
    )
    @app_commands.describe(role="Bot moderator role")
    async def set_botmod_role(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        rid = role.id if role else None
        await self.db.update_guild_settings(interaction.guild.id, botmod_role_id=rid)
        desc = f"Bot moderator role set to {role.mention}." if role else "Bot moderator role cleared."
        await interaction.response.send_message(
            embed=discord.Embed(title="Bot moderator role", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(name="setacceptedrole", description="Role assigned when an application is accepted.")
    @app_commands.describe(role="Accepted role; omit to clear")
    async def set_accepted_role(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        rid = role.id if role else None
        await self.db.update_guild_settings(interaction.guild.id, accepted_role_id=rid)
        desc = f"Accepted role set to {role.mention}." if role else "Accepted role cleared."
        await interaction.response.send_message(
            embed=discord.Embed(title="Accepted role", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(name="setinterviewrole", description="Role assigned when staff requests an interview.")
    @app_commands.describe(role="Interview role; omit to clear")
    async def set_interview_role(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        rid = role.id if role else None
        await self.db.update_guild_settings(interaction.guild.id, interview_role_id=rid)
        desc = f"Interview role set to {role.mention}." if role else "Interview role cleared."
        await interaction.response.send_message(
            embed=discord.Embed(title="Interview role", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(
        name="setinterviewcategory",
        description="Category for private interview text channels. Omit to use thread under staff embed.",
    )
    @app_commands.describe(
        category="Category where each interview gets its own channel; omit to clear.",
    )
    async def set_interview_category(
        self,
        interaction: discord.Interaction,
        category: Optional[discord.CategoryChannel] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        cid = category.id if category else None
        await self.db.update_guild_settings(interaction.guild.id, interview_category_id=cid)
        if category:
            desc = f"Interview channels will be created in {category.mention}."
        else:
            desc = "Cleared. Interviews will use a **thread** under the staff application message again."
        await interaction.response.send_message(
            embed=discord.Embed(title="Interview category", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(
        name="setstaffrole",
        description="Role that may use staff application buttons and interview claim (plus admins). Omit to clear.",
    )
    @app_commands.describe(role="Staff role")
    async def set_staff_role(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        rid = role.id if role else None
        await self.db.update_guild_settings(interaction.guild.id, staff_role_id=rid)
        desc = f"Staff role set to {role.mention}." if role else "Staff role cleared."
        await interaction.response.send_message(
            embed=discord.Embed(title="Staff role", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(name="setlogchannel", description="Channel for recruitment audit logs (optional).")
    @app_commands.describe(channel="Log channel; omit to clear")
    async def set_log_channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        cid = channel.id if channel else None
        await self.db.update_guild_settings(interaction.guild.id, log_channel_id=cid)
        desc = f"Log channel set to {channel.mention}." if channel else "Log channel cleared."
        await interaction.response.send_message(
            embed=discord.Embed(title="Log channel", description=desc, color=discord.Color.green()),
            ephemeral=True,
        )

    @app_commands.command(
        name="purgeuserdata",
        description="Remove this user's recruitment rows and pending data from the bot database.",
    )
    @app_commands.describe(user="User whose application data should be deleted for this server.")
    async def purge_user_data(
        self,
        interaction: discord.Interaction,
        user: discord.User,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        result = await self.db.purge_user_data(interaction.guild.id, user.id)
        lines = [
            f"**Applications removed:** {result.applications_deleted}",
            f"**Pending rows removed:** {result.pending_deleted}",
            f"**Recruiter claims cleared** (on others' apps): {result.recruiter_claims_cleared}",
        ]
        await interaction.response.send_message(
            embed=discord.Embed(
                title="User data purged",
                description=f"Target: {user.mention} (`{user.id}`)\n\n" + "\n".join(lines),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="sendembed",
        description="Compose an embed in a modal and post it to a channel (admin / bot moderator).",
    )
    @app_commands.describe(channel="Channel where the embed will be posted")
    async def send_embed(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if not interaction.guild:
            return
        settings = await self.db.get_guild_settings(interaction.guild.id)
        if not await ensure_admin_or_botmod(interaction, settings):
            return

        me = interaction.guild.me
        if me is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description="Could not resolve the bot member for permission checks.",
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        perms = channel.permissions_for(me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Missing permissions",
                    description=(
                        f"The bot needs **Send Messages** and **Embed Links** in {channel.mention}."
                    ),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(EmbedSendModal(channel))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
