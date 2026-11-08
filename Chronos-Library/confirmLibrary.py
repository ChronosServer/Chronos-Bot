import discord

FOOTER = 'Chronos™'


class ConfirmView(discord.ui.View):
    def __init__(self, author_id, description, timeout):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.description = description
        self.confirmed = False
        self.message = None

    def result_embed(self, title):
        embed = discord.Embed(title=title, description=self.description)
        embed.set_footer(text=FOOTER)
        return embed

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message('Only the person who ran the command can use these buttons.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        await interaction.response.edit_message(embed=self.result_embed('Confirmed ✅'), view=None)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=self.result_embed('Cancelled ❌, nothing was done'), view=None)
        self.stop()

    async def on_timeout(self):
        if self.message is not None:
            try:
                await self.message.edit(embed=self.result_embed('Timed out ⌛, nothing was done'), view=None)
            except discord.HTTPException:
                pass


async def confirm(ctx, description, timeout=30):
    """Asks the person who ran the command to press Confirm or Cancel.
    Returns True only if they pressed Confirm; Cancel and running out of time both return False."""
    view = ConfirmView(ctx.author.id, description, timeout)
    embed = discord.Embed(title='Are you sure?', description=description)
    embed.set_footer(text=FOOTER)
    view.message = await ctx.send(embed=embed, view=view)
    await view.wait()
    return view.confirmed