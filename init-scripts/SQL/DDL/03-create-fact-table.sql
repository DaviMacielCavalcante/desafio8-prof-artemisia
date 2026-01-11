\c dev;

CREATE TABLE IF NOT EXISTS gold.pokemon(
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    base_experience INTEGER NOT NULL,
    height INTEGER NOT NULL,
    weight INTEGER NOT NULL,
    order_for_sort INTEGER NOT NULL,
    location VARCHAR(255) NOT NULL,
    hp INTEGER NOT NULL,
    hp_effort INTEGER NOT NULL,
    attack INTEGER NOT NULL,
    attack_effort INTEGER NOT NULL,
    defense INTEGER NOT NULL,
    defense_effort INTEGER NOT NULL,
    special_attack INTEGER NOT NULL,
    special_attack_effort INTEGER NOT NULL,
    special_defense INTEGER NOT NULL,
    special_defense_effort INTEGER NOT NULL,
    speed INTEGER NOT NULL,
    speed_effort INTEGER NOT NULL
);