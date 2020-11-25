import contextlib
import jishaku.paginators
import jishaku.exception_handling
import discord
import re
import aiohttp
from discord import utils
from discord.abc import Messageable
from discord.http import Route, HTTPClient
from discord.mentions import AllowedMentions, default
from discord.message import MessageReference, Message
from typing import Union
from collections import namedtuple

EmojiSettings = namedtuple('EmojiSettings', 'start back forward end close')


class FakeEmote(discord.PartialEmoji):
    @classmethod
    def from_name(cls, name):
        emoji_name = re.sub("|<|>", "", name)
        a, name, id = emoji_name.split(":")
        return cls(name=name, id=int(id), animated=bool(a))


emote = EmojiSettings(
    start=FakeEmote.from_name("<:before_fast_check:754948796139569224>"),
    back=FakeEmote.from_name("<:before_check:754948796487565332>"),
    forward=FakeEmote.from_name("<:next_check:754948796361736213>"),
    end=FakeEmote.from_name("<:next_fast_check:754948796391227442>"),
    close=FakeEmote.from_name("<:stop_check:754948796365930517>")
)
jishaku.paginators.EMOJI_DEFAULT = emote  # Overrides jishaku emojis


async def attempt_add_reaction(msg: discord.Message, reaction: Union[str, discord.Emoji]):
    reacts = {
        "\N{WHITE HEAVY CHECK MARK}": "<:checkmark:753619798021373974>",
        "\N{BLACK RIGHT-POINTING TRIANGLE}": emote.forward,
        "\N{HEAVY EXCLAMATION MARK SYMBOL}": "<:information_pp:754948796454010900>",
        "\N{DOUBLE EXCLAMATION MARK}": "<:crossmark:753620331851284480>",
        "\N{ALARM CLOCK}": emote.end
    }
    with contextlib.suppress(discord.HTTPException):
        return await msg.add_reaction(reacts[reaction])


jishaku.exception_handling.attempt_add_reaction = attempt_add_reaction


async def send(self, content=None, *, tts=False, embed=None, file=None,
               files=None, delete_after=None, nonce=None,
               allowed_mentions=None, message_reference=None):
    channel = await self._get_channel()
    state = self._state
    content = str(content) if content is not None else None
    if embed is not None:
        embed = embed.to_dict()

    if allowed_mentions is not None:
        if state.allowed_mentions is not None:
            allowed_mentions = state.allowed_mentions.merge(allowed_mentions).to_dict()
        else:
            allowed_mentions = allowed_mentions.to_dict()
    else:
        allowed_mentions = state.allowed_mentions and state.allowed_mentions.to_dict()

    if message_reference is not None:
        message_reference = message_reference.to_dict()

    if file is not None and files is not None:
        raise discord.InvalidArgument('cannot pass both file and files parameter to send()')

    if file is not None:
        if not isinstance(file, discord.File):
            raise discord.InvalidArgument('file parameter must be File')

        try:
            data = await state.http.send_files(channel.id, files=[file], allowed_mentions=allowed_mentions,
                                               content=content, tts=tts, embed=embed, nonce=nonce,
                                               message_reference=message_reference)
        finally:
            file.close()

    elif files is not None:
        if len(files) > 10:
            raise discord.InvalidArgument('files parameter must be a list of up to 10 elements')
        elif not all(isinstance(file, discord.File) for file in files):
            raise discord.InvalidArgument('files parameter must be a list of File')

        try:
            data = await state.http.send_files(channel.id, files=files, content=content, tts=tts,
                                               embed=embed, nonce=nonce, allowed_mentions=allowed_mentions,
                                               message_reference=message_reference)
        finally:
            for f in files:
                f.close()
    else:
        data = await state.http.send_message(channel.id, content, tts=tts, embed=embed,
                                             nonce=nonce, allowed_mentions=allowed_mentions,
                                             message_reference=message_reference)

    ret = state.create_message(channel=channel, data=data)
    if delete_after is not None:
        await ret.delete(delay=delete_after)
    return ret


def send_message(self, channel_id, content, *, tts=False, embed=None, nonce=None, allowed_mentions=None, message_reference=None):
    r = Route('POST', '/channels/{channel_id}/messages', channel_id=channel_id)
    payload = {}

    if content:
        payload['content'] = content

    if tts:
        payload['tts'] = True

    if embed:
        payload['embed'] = embed

    if nonce:
        payload['nonce'] = nonce

    if allowed_mentions:
        payload['allowed_mentions'] = allowed_mentions

    if message_reference:
        payload['message_reference'] = message_reference

    return self.request(r, json=payload)


def send_files(self, channel_id, *, files, content=None, tts=False, embed=None, nonce=None, allowed_mentions=None, message_reference=None):
    r = Route('POST', '/channels/{channel_id}/messages', channel_id=channel_id)
    form = aiohttp.FormData()

    payload = {'tts': tts}
    if content:
        payload['content'] = content
    if embed:
        payload['embed'] = embed
    if nonce:
        payload['nonce'] = nonce
    if allowed_mentions:
        payload['allowed_mentions'] = allowed_mentions
    if message_reference:
        payload['message_reference'] = message_reference

    form.add_field('payload_json', utils.to_json(payload))
    if len(files) == 1:
        file = files[0]
        form.add_field('file', file.fp, filename=file.filename, content_type='application/octet-stream')
    else:
        for index, file in enumerate(files):
            form.add_field('file%s' % index, file.fp, filename=file.filename, content_type='application/octet-stream')

    return self.request(r, data=form, files=files)


class ModifiedAllowedMentions(AllowedMentions):
    __slots__ = ('everyone', 'users', 'roles', 'replied_user')

    def __init__(self, *, everyone=default, users=default, roles=default, replied_user=default):
        super().__init__(everyone=everyone, users=users, roles=roles)
        self.replied_user = replied_user


Messageable.send = send
HTTPClient.send_message = send_message
HTTPClient.send_files = send_files
discord.AllowedMentions = ModifiedAllowedMentions


def allowed_mentions_merge(self, other):
    everyone = self.everyone if other.everyone is default else other.everyone
    users = self.users if other.users is default else other.users
    roles = self.roles if other.roles is default else other.roles
    replied_user = self.replied_user if other.replied_user is default else other.replied_user
    return AllowedMentions(everyone=everyone, roles=roles, users=users, replied_user=replied_user)


def allowed_mentions__repr__(self):
    return '{0.__class__.__qualname__}(everyone={0.everyone}, users={0.users}, roles={0.roles}, replied_user={0.replied_user})'.format(self)


@classmethod
def allowed_mentions_all(cls):
    return cls(everyone=True, users=True, roles=True, replied_user=True)


@classmethod
def allowed_mentions_none(cls):
    return cls(everyone=False, users=False, roles=False, replied_user=False)


discord.AllowedMentions.merge = allowed_mentions_merge
discord.AllowedMentions.__repr__ = allowed_mentions__repr__
discord.AllowedMentions.all = allowed_mentions_all
discord.AllowedMentions.none = allowed_mentions_none


@classmethod
def from_message(cls, message):
    return cls(message._state, message_id=message.id, channel_id=message.channel.id, guild_id=message.guild and message.guild.id)


def messagereference_to_dict(self, specify_channel=False):
    result = {'message_id': self.message_id} if self.message_id is not None else {}
    if specify_channel:
        result['channel_id'] = self.channel_id
    if self.guild_id is not None:
        result['guild_id'] = self.guild_id
    return result


async def message_reply(self, content=None, **kwargs):
    reference = MessageReference.from_message(self)
    allowed_mentions = kwargs.pop('allowed_mentions', discord.AllowedMentions())
    if (mention_author := kwargs.pop("mention_author", None)) is not None:
        allowed_mentions.replied_user = mention_author

    return await self.channel.send(content, message_reference=reference, allowed_mentions=allowed_mentions, **kwargs)

MessageReference.from_message = from_message
MessageReference.to_dict = messagereference_to_dict
Message.reply = message_reply


def setup(bot):
    pass

