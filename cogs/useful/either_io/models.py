import datetime
from dataclasses import dataclass
from typing import Dict, List, Any, Optional


@dataclass
class QuestionPayload:
    comment_total: int
    comments_enabled: bool
    created_on: datetime.datetime
    device_id: Optional[str]
    disliked: bool
    display_name: str
    email: str
    favorited: bool
    featured: bool
    gender: str
    id: int
    is_adult: bool
    is_anonymous: bool
    liked: bool
    live_id: int
    moreinfo: str
    option1_total: int
    option2_total: int
    option_1: str
    option_2: str
    platform: Optional[str]
    platform_version: Optional[str]
    prefix: str
    published: bool
    short_url: str
    slug: str
    tags: List[Dict[str, Any]]
    title: str
    twitter_sentence: str
    updated_by: str
    updated_on: datetime.datetime
    user_id: int
    user_is_deleted: bool

    def convert_value(self, key: str, converter: Any) -> None:
        if value := getattr(self, key, None):
            v = converter(value)
            setattr(self, key, v)

    def convert_datetime(self, key: str) -> None:
        self.convert_value(key, lambda v: datetime.datetime.fromtimestamp(int(v)))

    def convert_bool(self, key: str) -> None:
        self.convert_value(key, lambda x: x == "1")

    @classmethod
    def from_payload(cls, data: Dict[str, Any]):
        value = cls(**data)
        value.convert_datetime("created_on")
        value.convert_datetime("updated_on")
        bools = ["user_is_deleted", "published", "liked", "disliked", "is_anonymous", "is_adult", "featured",
                 "favorited", "comments_enabled"]

        ints = ["comment_total", "id", "live_id", "option1_total", "option2_total", "user_id"]

        for key in bools:
            value.convert_bool(key)

        for key in ints:
            value.convert_value(key, int)

        return value


@dataclass
class Answer:
    answer: int
    amount: int


class Question(QuestionPayload):
    seen: bool = False
    previous_seen: bool = False
    answered: Optional[int] = None
    discord_answers_opts: List[Answer] = []
    unanswered_image_url: Optional[str] = None
    answered_image_url: Optional[str] = None

    @property
    def total_answers(self) -> int:
        return self.option1_total + self.option2_total
