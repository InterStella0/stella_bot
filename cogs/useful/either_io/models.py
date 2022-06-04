import datetime
from dataclasses import dataclass
from typing import Dict, List, Any, Optional
# comment_total: "706"
# comments_enabled: "1"
# created_on: "1292726259"
# device_id: null
# disliked: "0"
# display_name: "kisin"
# email: "silione@gmail.com"
# favorited: "0"
# featured: "0"
# gender: "b"
# id: "553"
# is_adult: "0"
# is_anonymous: "0"
# liked: "0"
# live_id: "546"
# moreinfo: ""
# option1_total: "868498"
# option2_total: "480592"
# option_1: "Sit"
# option_2: "Stand"
# platform: null
# platform_version: null
# prefix: "If you could only do one"
# published: "1"
# short_url: "http://wyr.be/uDG22p"
# slug: "sit-or-stand"
# tags: [{id: "5", name: "Pain &amp;amp; Suffering", published: "1", created_on: "1326212553",…}]
# title: "Sit or Stand"
# twitter_sentence: "Would you rather only be able to sit or stand?"
# updated_by: "2788"
# updated_on: "1294243685"
# user_id: "1393"
# user_is_deleted: "0"
# 3: {id: "874", live_id: "657", user_id: "2972", option_1: "know when the world ends",…}
# comment_total: "828"
# comments_enabled: "1"
# created_on: "1326748467"
# device_id: "1168519055"
# disliked: "0"
# display_name: "freckles108"
# email: "tigerpawz24@hotmail.com"
# favorited: "0"
# featured: "0"
# gender: "b"
# id: "874"
# is_adult: "0"
# is_anonymous: "1"
# liked: "0"
# live_id: "657"
# moreinfo: ""
# option1_total: "503525"
# option2_total: "801700"
# option_1: "know when the world ends"
# option_2: "know how the world ends"
# platform: "Chrome"
# platform_version: "16.0"
# prefix: ""
# published: "1"
# short_url: "http://wyr.be/x7QZkM"
# slug: "end-of-the-world"
# tags: [{id: "11", name: "Life", published: "1", created_on: "1326212553", slug: "life"},…]
# title: "End of the World"
# twitter_sentence: "Would you rather know when the world ends or how the world ends?"
# updated_by: "2196"
# updated_on: "1327621226"
# user_id: "2972"
# user_is_deleted: "0"


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
