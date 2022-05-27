CREATE TABLE IF NOT EXISTS blacklist(
     snowflake_id BIGINT PRIMARY KEY,
     REASON VARCHAR(4000),
     timestamp TIMESTAMP);

CREATE TABLE IF NOT EXISTS commands_list(
     guild_id BIGINT NOT NULL,
     bot_id BIGINT NOT NULL,
     command VARCHAR(100) NOT NULL,
     time_used TIMESTAMP);

CREATE TABLE IF NOT EXISTS prefixes_list(
     guild_id BIGINT NOT NULL,
     bot_id BIGINT NOT NULL,
     prefix VARCHAR(100),
     usage INTEGER NOT NULL,
     last_usage TIMESTAMP NOT NULL,
     UNIQUE(guild_id, bot_id, prefix));

CREATE TABLE IF NOT EXISTS bot_repo(
     bot_id BIGINT NOT NULL,
     owner_repo VARCHAR(2000),
     bot_name VARCHAR(2000),
     certainty NUMERIC(20, 2),
     PRIMARY KEY(bot_id));

CREATE TABLE IF NOT EXISTS confirmed_bots(
     bot_id BIGINT,
     author_id BIGINT,
     reason VARCHAR(2000),
     requested_at TIMESTAMP,
     jump_url VARCHAR(1000),
     joined_at TIMESTAMP,
     UNIQUE(bot_id)
);

CREATE TABLE IF NOT EXISTS pending_bots(
     bot_id BIGINT PRIMARY KEY,
     author_id BIGINT,
     reason VARCHAR(2050),
     requested_at TIMESTAMP,
     jump_url VARCHAR(1000)
);

CREATE TABLE IF NOT EXISTS reports(
     report_id SERIAL,
     user_id BIGINT NOT NULL,
     finish BOOLEAN NOT NULL,
     reported_at TIMESTAMP,
     PRIMARY KEY (report_id)
);

CREATE TABLE IF NOT EXISTS resport_respond(
     report_id BIGINT NOT NULL,
     user_id BIGINT NOT NULL,
     interface_id BIGINT,
     message_id BIGINT NOT NULL,
     message VARCHAR(4000),
     FOREIGN KEY (report_id) REFERENCES reports(report_id)
);

CREATE TABLE IF NOT EXISTS position_letter(
     bot_id BIGINT,
     letter CHAR,
     position INT,
     count INT,
     PRIMARY KEY(bot_id, letter, "position")
);

CREATE TABLE IF NOT EXISTS internal_prefix(
     snowflake_id BIGINT UNIQUE,
     prefix VARCHAR(30)
);

CREATE TABLE IF NOT EXISTS bot_tasks(
     task_id SERIAL PRIMARY KEY,
     last_execution TIMESTAMP WITH TIME ZONE,
     next_execution TIMESTAMP WITH TIME ZONE
);

CREATE TABLE IF NOT EXISTS wordle_tag(
    tag VARCHAR(100) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    used INT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE,
    description VARCHAR(2000)
);


CREATE TABLE IF NOT EXISTS wordle_word(
    tag VARCHAR(100) NOT NULL,
    word VARCHAR(20) NOT NULL,
    FOREIGN KEY (tag) REFERENCES wordle_tag(tag),
    UNIQUE(tag, word)
);


CREATE TABLE IF NOT EXISTS wordle_rank(
    user_id BIGINT NOT NULL,
    tag VARCHAR(100) NOT NULL,
    word VARCHAR(20) NOT NULL,
    attempt INT NOT NULL,
    amount INT NOT NULL,
    FOREIGN KEY (tag) REFERENCES wordle_tag(tag),
    UNIQUE (user_id, tag, word, attempt)
);

CREATE TABLE IF NOT EXISTS button_game(
    user_id BIGINT PRIMARY KEY,
    amount BIGINT DEFAULT 1
)

CREATE TABLE IF NOT EXISTS wombo_style(
    style_id INT PRIMARY KEY,
    style_count INT DEFAULT 1,
    style_emoji BIGINT
)

CREATE TABLE IF NOT EXISTS wombo_saved(
    name VARCHAR(100) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    image_url VARCHAR(3000) NOT NULL,
    vote INT DEFAULT 0,
    is_nsfw BOOLEAN NOT NULL,
    prompt VARCHAR(100) NOT NULL,
    style VARCHAR(100) NOT NULL,
)

CREATE TABLE IF NOT EXISTS wombo_liker(
    name VARCHAR(100),
    user_id BIGINT,
    PRIMARY KEY(name, user_id)
)

CREATE TABLE IF NOT EXISTS wombo_data_seen(
    name VARCHAR(1000),
    seen BOOLEAN DEFAULT FALSE
)