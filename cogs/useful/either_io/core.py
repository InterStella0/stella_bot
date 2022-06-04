import copy
import io
import textwrap
from typing import Optional, Tuple, Any, Dict, Awaitable, List

import aiohttp
import discord
from PIL import ImageDraw, ImageFont, Image
from PIL.ImageFont import FreeTypeFont

from cogs.useful.either_io.models import Question, Answer
from utils.decorators import in_executor
from utils.errors import ErrorNoSignature
from utils.useful import except_retry, StellaEmbed, StellaContext


class EitherIO(discord.ui.View):
    BASE = "http://either.io"

    def __init__(self, http: aiohttp.ClientSession):
        super().__init__()
        self.http = http
        self.questions = []
        self.current_page = -1
        self.ctx = None
        self.question = None
        self.message = None
        self.font = ImageFont.truetype("fonts/HelveticaNeueBd.ttf", 22, encoding="unic")

    async def start(self, ctx: StellaContext) -> None:
        self.ctx = ctx
        self.question = await self.next_page()
        await self.show_question(None, ctx=ctx)
        await self.wait()

    def form_embed(self) -> StellaEmbed:
        self.on_previous_page.disabled = self.current_page == 0
        question = self.question
        title = f"{question.prefix}, Would you rather" if question.prefix else "Would you rather"
        embed = StellaEmbed(title=title,
                            description=f"**Description: ** {question.moreinfo}" if question.moreinfo else "",
                            url=f"{self.BASE}/{question.id}")
        embed.add_field(name="Total Votes", value=question.total_answers)
        embed.add_field(name="Comments", value=question.comment_total)
        return embed.set_footer(text=f"Questioned by {question.display_name}")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user == self.ctx.author:
            return True
        await interaction.response.send_message(f"Only '{self.ctx.author}' can use this button.", ephemeral=True)

    async def show_question(self, interaction: discord.Interaction, *, ctx: Optional[StellaContext] = None) -> None:
        question = self.question
        embed = self.form_embed()
        if question.seen:
            embed.add_field(name="You answered", value=getattr(question, f"option_{question.answered}"))
            if question.answered_image_url is None:
                byte = await self.render_answered_question()
                file = await self.ctx.bot.upload_file(byte=byte.read(), filename="Question.png")
                url = question.answered_image_url = file.url
            else:
                url = question.answered_image_url
        else:
            if question.unanswered_image_url is None:
                byte = await self.render_not_answer_question()
                file = await self.ctx.bot.upload_file(byte=byte.read(), filename="Question.png")
                url = question.unanswered_image_url = file.url
            else:
                url = question.unanswered_image_url

        self.on_answer_one.disabled = question.seen
        self.on_answer_one.label = textwrap.shorten(question.option_1, width=80, placeholder="...")
        self.on_answer_two.disabled = question.seen
        self.on_answer_two.label = textwrap.shorten(question.option_2, width=80, placeholder="...")

        embed.set_image(url=url)

        if ctx is None:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            self.message = await ctx.maybe_reply(embed=embed, view=self)

    async def set_question(self, interaction: discord.Interaction, answer: int) -> None:
        self.on_answer_one.disabled = True
        self.on_answer_two.disabled = True
        query = "INSERT INTO either_io VALUES($1, $2, $3)"
        question = self.question
        question.previous_seen = True
        embed = self.form_embed()
        question.answered = answer
        byte = await self.render_answered_question()
        file = await self.ctx.bot.upload_file(byte=byte.read(), filename="answered.png")
        question.answered_image_url = file.url
        embed.set_image(url=file.url)
        embed.add_field(name="You answered", value=getattr(question, f"option_{answer}"))
        ctx = self.ctx
        await interaction.response.edit_message(view=self, embed=embed)
        await self.ctx.bot.pool_pg.execute(query, ctx.author.id, question.id, answer)

    @discord.ui.button(emoji="1\U0000fe0f\U000020e3", label="", style=discord.ButtonStyle.blurple)
    async def on_answer_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.set_question(interaction, 1)

    @discord.ui.button(emoji="2\U0000fe0f\U000020e3", label="", style=discord.ButtonStyle.red, row=1)
    async def on_answer_two(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.set_question(interaction, 2)

    @discord.ui.button(emoji="<:before_check:754948796487565332>", style=discord.ButtonStyle.grey, row=2)
    async def on_previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.question = self.previous_page()
        await self.check_question(self.question)
        await self.show_question(interaction)

    async def on_timeout(self) -> None:
        if self.ctx.bot.get_message(self.message.id):
            await self.message.edit(view=None)

    @discord.ui.button(emoji="<:stop_check:754948796365930517>", style=discord.ButtonStyle.grey, row=2)
    async def on_stop_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(view=None)

    @discord.ui.button(emoji="<:next_check:754948796361736213>", style=discord.ButtonStyle.grey, row=2)
    async def on_next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.question = await self.next_page()
        await self.show_question(interaction)

    async def check_question(self, question: Question) -> None:
        query = "SELECT * FROM either_io WHERE user_id=$1 AND question_id=$2"
        ctx = self.ctx
        if val := await ctx.bot.pool_pg.fetchrow(query, ctx.author.id, question.id):
            question.seen = True
            question.answered = val["answered"]

        if question.discord_answers_opts:
            amounts = 'SELECT answered, COUNT(*) "amount" FROM either_io ' \
                      'WHERE question_id=$1 ' \
                      'GROUP BY answered'
            values = await ctx.bot.pool_pg.fetch(amounts, question.id)
            question.discord_answers_opts = [Answer(value["answered"], value["amount"]) for value in values]

    async def get_question_checked(self, *, recursed=0) -> Question:
        try:
            return self.questions[self.current_page]
        except IndexError:
            if recursed >= 10:
                raise Exception("Something went wrong during fetching questions. Sorry. ")

            q = await self.fetch_next()
            self.questions.extend(q)
            return await self.get_question_checked(recursed=recursed + 1)

    async def next_page(self) -> Question:
        self.current_page += 1
        question = await self.get_question_checked()
        await self.check_question(question)
        if not question.seen or question.previous_seen:
            return question

        return await self.next_page()

    def previous_page(self) -> Question:
        index = max(self.current_page - 1, 0)
        self.current_page = index
        return self.questions[index]

    async def request(self, method, url, **kwargs) -> Dict[str, Any]:
        return await except_retry(self._request, method, url, retries=1, **kwargs)

    async def _request(self, method, url, **kwargs) -> Dict[str, Any]:
        async with self.http.request(method, self.BASE + url, **kwargs) as resp:
            return await resp.json(content_type="text/html")

    def get_next(self, amount: int) -> Awaitable[Dict[str, Any]]:
        return self.request("GET", f"/questions/next/{amount}")

    def get_comments(self, question_id: int) -> Awaitable[Dict[str, Any]]:
        return self.request("GET", f"/questions/comments/{question_id}")

    async def fetch_next(self, amount: int = 10) -> List[Question]:
        values = await self.get_next(amount)
        if "questions" in values:
            return [Question.from_payload(value) for value in values["questions"]]
        raise ErrorNoSignature("Invalid Question fetched. Please try again later.")

    @staticmethod
    def draw_ellipse(image, bounds, width=1, outline='white', antialias=4) -> None:
        """Improved ellipse drawing function, based on PIL.ImageDraw.
           Copied from https://stackoverflow.com/questions/32504246/draw-ellipse-in-python-pil-with-line-thickness
           because i'm extremely lazy to figure out myself."""

        # Use a single channel image (mode='L') as mask.
        # The size of the mask can be increased relative to the imput image
        # to get smoother looking results.
        mask = Image.new(
            size=[int(dim * antialias) for dim in image.size],
            mode='L', color='black')
        draw = ImageDraw.Draw(mask)

        # draw outer shape in white (color) and inner shape in black (transparent)
        for offset, fill in (width / -2.0, 'white'), (width / 2.0, 'black'):
            left, top = [(value + offset) * antialias for value in bounds[:2]]
            right, bottom = [(value - offset) * antialias for value in bounds[2:]]
            draw.ellipse([left, top, right, bottom], fill=fill)

        # downsample the mask using PIL.Image.LANCZOS
        # (a high-quality downsampling filter).
        mask = mask.resize(image.size, Image.LANCZOS)
        # paste outline color to input image through the mask
        image.paste(outline, mask=mask)

    @staticmethod
    def find_space_text(size, text: str, *, font, padding: int = 0) -> Tuple[Tuple[int, int], List[str]]:
        W, H = size
        sentences = [len(x) + 1 for x in text.split(" ")]
        while True:
            lines = textwrap.wrap(text, width=sum(sentences))
            y_text = H / 2
            height = 0
            for line in lines:
                width, height = font.getsize(line)
                if width > W - padding - padding:
                    sentences = sentences[:-1]
                    break
                y_text += height
            else:
                h = height * len(lines)
                break
        return (W, h), lines

    def render_text_center(self, draw: ImageDraw.Draw, size: Tuple[int, int], text: str, *, color='white',
                           padding: int = 0) -> Image.Image:
        W, H = size
        font = self.font
        (w, h), lines = self.find_space_text(size, text, font=font, padding=padding)
        y_text = (H - h) / 2
        for line in lines:
            width, height = font.getsize(line)
            draw.text(((W - width) / 2, y_text), line, font=font, fill=color)
            y_text += height

    def render_rather(self) -> Image.Image:
        color = 29, 29, 29
        question = self.question
        title = f"{question.prefix}, would you rather" if question.prefix else "Would you rather"
        width = 575 + 575 + 10 + 10
        size = (width, 50)
        img = Image.new("RGB", size, color)
        draw = ImageDraw.Draw(img)
        self.render_text_center(draw, size, title + "...", padding=20)
        return img

    def render_answer(self, answer: int) -> Image.Image:
        color = [(125, 197, 232), (193, 55, 46)][answer - 1]
        answer_text = getattr(self.question, f"option_{answer}")
        padding = 30
        question_opt_box = (575, 325)
        ans = Image.new(mode="RGB", size=question_opt_box, color=color)
        draw = ImageDraw.Draw(ans)
        self.render_text_center(draw, question_opt_box, answer_text, padding=padding)
        return ans

    def width_center_text(self, draw: ImageDraw, size: Tuple[int, int], text: str, font: FreeTypeFont, padding: str,
                          color: Any) -> None:
        W, H = size
        (w, h), lines = self.find_space_text(size, text, font=font, padding=padding)
        y_text = H
        for line in lines:
            width, height = font.getsize(line)
            draw.text(((W - width) / 2, y_text), line, font=font, fill=color)
            y_text += height

    def render_answered(self, answer: int) -> Image.Image:
        color = [(125, 197, 232), (193, 55, 46)][answer - 1]
        color_perc = [(65, 124, 157), (115, 26, 20)][answer - 1]
        color_amount = [(199, 235, 253), (239, 134, 131)][answer - 1]
        question = self.question
        answer_text = getattr(question, f"option_{answer}")
        total = getattr(question, f"option{answer}_total")
        answer_total_text = f'{total:,} Answered'
        question_opt_box = W, H = (575, 325)
        ans = Image.new(mode="RGBA", size=question_opt_box, color=color)
        draw = ImageDraw.Draw(ans)
        font = ImageFont.truetype("fonts/HelveticaNeueBd.ttf", 90, encoding="unic")
        percent = f"{total / question.total_answers:.0%}"
        w, top_h = font.getsize(percent)
        margin = 5
        _, middle_h = self.font.getsize(answer_total_text)
        _, low_h = self.font.getsize(answer_text)
        whole_h = top_h + margin + middle_h + margin + low_h
        most_h = (H - whole_h) / 2
        top = W, most_h + margin
        middle = W, most_h + margin + top_h + margin
        bottom = W, top_h + top_h + margin + middle_h + margin
        self.width_center_text(draw, top, percent, font=font, color=color_perc, padding=0)
        self.width_center_text(draw, middle, answer_total_text, self.font, color=color_amount, padding=0)
        self.width_center_text(draw, bottom, answer_text, self.font, color='white', padding=0)

        if question.answered == answer:
            triangle_size = 70
            question_check = [(W - triangle_size, 0), (W, 0), (W, triangle_size)]
            draw.polygon(question_check, fill=color_perc)
            ttf = ImageFont.truetype('fonts/arial-unicode-ms.ttf', 30)
            draw.text((W - triangle_size / 2, 0), "✓", font=ttf, fill=color)
        return ans

    def render_or(self) -> Image.Image:
        size = W, H = (75, 75)
        stroke = 10
        whole_image_size = W + stroke, H + stroke
        img = Image.new(mode="RGBA", size=whole_image_size)
        draw = ImageDraw.Draw(img)
        coord = (stroke, stroke, *size)
        draw.ellipse(coord, fill=(29, 29, 29))
        outline_img = Image.new("RGBA", whole_image_size, color=(0, 0, 0, 75))
        self.draw_ellipse(img, coord, width=stroke, outline=outline_img)
        self.draw_ellipse(img, (stroke + 5, stroke + 5, W - 5, H - 5), width=2, outline='black')

        W_text, H_text = whole_image_size
        self.render_text_center(draw, (W_text - stroke / 2, H_text - stroke / 2), " or")
        return img

    @in_executor()
    def render_answered_question(self) -> io.BytesIO:
        img = Image.new(mode="RGB", size=(1200, 400), color=(29, 29, 29))
        margin = 10
        img.paste(self.render_answered(1), (margin, 50))
        a2 = self.render_answered(2)
        a2_w, a2_h = a2.size
        img.paste(a2, (margin + a2_w + margin, 50))
        title = self.render_rather()
        img.paste(title)
        or_box = self.render_or()
        or_actual_W, or_actual_H = or_box.size

        or_W = int(((a2_w + a2_w + margin * 3) - or_actual_W) / 2)
        or_H = int(((a2_h - or_actual_H) / 2) + 50)
        img.paste(or_box, box=(or_W, or_H), mask=or_box)
        byte = io.BytesIO()
        img.save(byte, "PNG")
        byte.seek(0)
        return byte

    @in_executor()
    def render_not_answer_question(self) -> io.BytesIO:
        img = Image.new(mode="RGB", size=(1200, 400), color=(29, 29, 29))
        margin = 10
        img.paste(self.render_answer(1), (margin, 50))
        a2 = self.render_answer(2)
        a2_w, a2_h = a2.size
        img.paste(a2, (margin + a2_w + margin, 50))
        title = self.render_rather()
        img.paste(title)
        or_box = self.render_or()
        or_actual_W, or_actual_H = or_box.size

        or_W = int(((a2_w + a2_w + margin * 3) - or_actual_W) / 2)
        or_H = int(((a2_h - or_actual_H) / 2) + 50)
        img.paste(or_box, box=(or_W, or_H), mask=or_box)
        byte = io.BytesIO()
        img.save(byte, "PNG")
        byte.seek(0)
        return byte
