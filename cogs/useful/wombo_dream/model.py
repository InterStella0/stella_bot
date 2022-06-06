import datetime
import mimetypes
from collections import namedtuple
from dataclasses import dataclass
from dateutil import parser
from typing import List, Dict, Optional, Any

from discord.ext import commands
from typing_extensions import Self

from utils.useful import StellaContext

ImageDescription = namedtuple("ImageDescription", "name nsfw")


@dataclass
class PayloadTask:
    id: str
    created_at: datetime.datetime
    generated_photo_keys: List[str]
    input_spec: Dict[str, str]
    photo_url_list: List[str]
    premium: False
    result: Dict[str, str]
    state: str
    updated_at: datetime.datetime
    user_id: str

    @staticmethod
    def convert_if_value(date_str: Optional[str]) -> datetime.datetime:
        if date_str is None:
            return

        return parser.parse(date_str)

    @classmethod
    def from_response(cls, payload: Dict[str, Any]) -> Self:
        return cls(
            payload['id'],
            cls.convert_if_value(payload['created_at']),
            payload['generated_photo_keys'],
            payload['input_spec'],
            payload['photo_url_list'],
            payload['premium'],
            payload['result'],
            payload['state'],
            cls.convert_if_value(payload['updated_at']),
            payload['user_id']
        )


def convert_expiry_date(seconds: str) -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(seconds=int(seconds))


@dataclass
class PayloadToken:
    kind: str
    id_token: str
    refresh_token: str
    expires_in: datetime.datetime
    local_id: str

    @classmethod
    def from_json(cls, data: Dict[str, str]) -> Self:
        expire = convert_expiry_date(data['expiresIn'])
        return cls(data['kind'], data['idToken'], data['refreshToken'], expire, data['localId'])


@dataclass
class PayloadAccessToken:
    access_token: str
    expires_in: int
    id_token: str
    project_id: int
    refresh_token: str
    token_type: str
    user_id: str

    @classmethod
    def from_json(cls, data) -> Self:
        expire = convert_expiry_date(data['expires_in'])
        return cls(
                data['access_token'], expire, data['id_token'], data['project_id'],
                data['refresh_token'], data['token_type'], data['user_id']
            )


@dataclass
class ArtStyle:
    id: int
    name: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    deleted_at: Optional[datetime.datetime]
    photo_url: str
    blur_data_url: str
    count: int = 0
    emoji: Optional[int] = None

    @classmethod
    def from_json(cls, raw_data) -> Self:
        return cls(
            raw_data['id'], raw_data['name'], raw_data['created_at'], raw_data['updated_at'], raw_data['deleted_at'],
            raw_data['photo_url'], raw_data['blurDataURL']
        )

    def __str__(self) -> str:
        return self.name


@dataclass
class ImageSaved:
    name: str
    user_id: int
    art_style: str
    prompt: str
    image_url: str
    vote: int
    nsfw: bool

    @classmethod
    async def convert(cls, ctx: StellaContext, argument: str) -> Self:
        sql = ('SELECT ws.*, ('
               'SELECT COUNT(*) FROM wombo_liker WHERE name=ws.name'
               ') "count" FROM wombo_saved ws WHERE LOWER(name)=$1')
        if result := await ctx.bot.pool_pg.fetchrow(sql, argument.casefold()):
            value = cls.from_record(result)
            if value.nsfw and hasattr(ctx.channel, "is_nsfw") and not ctx.channel.is_nsfw():
                raise commands.CommandError("This image is only viewable on nsfw channel.")
            return value

        raise commands.CommandError(f'No image saved with "{argument}"')

    @classmethod
    def from_record(cls, record) -> Self:
        return cls(record["name"], record["user_id"], record["style"], record["prompt"], record["image_url"],
                   record["count"], record["is_nsfw"])


@dataclass
class ImageMetaData:
    fp: str
    category: str
    is_nsfw: bool
    name: str

    @property
    def full_fp(self) -> str:
        return rf"{self.fp}/{self.name}"

    @property
    def clean_name(self) -> str:
        cleaned, *_ = self.name.rpartition("_")
        return cleaned

    @property
    def extension(self) -> str:
        content_type, _ = mimetypes.guess_type(self.name)
        return mimetypes.guess_extension(content_type)

    def all_names(self) -> List[str]:
        return [f"{self.clean_name}_{x}{self.extension}" for x in range(5)]

    def __hash__(self) -> int:
        return hash(self.full_fp)

    def __eq__(self, other) -> bool:
        if isinstance(other, ImageMetaData):
            return self.clean_name == other.clean_name and self.extension == other.extension
        elif isinstance(other, str):
            other_cleaned, *_ = other.rpartition("_")
            return self.clean_name == other_cleaned
        return False

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return "<{0.fp}|{0.clean_name}>".format(self)
