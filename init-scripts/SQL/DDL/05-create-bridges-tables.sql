\c dev;

CREATE TABLE IF NOT EXISTS gold.pokemon_abilities(
    pokemon_id INTEGER REFERENCES gold.pokemon(id),
    abilities_id INTEGER REFERENCES gold.abilities(id),
    is_hidden BOOLEAN NOT NULL,
    slot INTEGER NOT NULL     
);

CREATE TABLE IF NOT EXISTS gold.pokemon_forms(
    pokemon_id INTEGER REFERENCES gold.pokemon(id),
    form_id INTEGER REFERENCES gold.forms(form_id)
);

CREATE TABLE IF NOT EXISTS gold.pokemon_games(
    pokemon_id INTEGER REFERENCES gold.pokemon(id),
    game_id INTEGER REFERENCES gold.games(game_id),
    game_index INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.pokemon_items(
    pokemon_id INTEGER REFERENCES gold.pokemon(id),
    item_id INTEGER REFERENCES gold.items(item_id)
);

CREATE TABLE IF NOT EXISTS gold.pokemon_moves(
    pokemon_id INTEGER REFERENCES gold.pokemon(id),
    move_id INTEGER REFERENCES gold.moves(move_id)        
);

CREATE TABLE IF NOT EXISTS gold.pokemon_types(
    pokemon_id INTEGER REFERENCES gold.pokemon(id),
    type_id INTEGER REFERENCES gold.types(type_id),
    slot SMALLINT NOT NULL
);

CREATE TABLE IF NOT EXISTS gold.pokemon_species(
    pokemon_id INTEGER REFERENCES gold.pokemon(id),
    species_id INTEGER REFERENCES gold.species(species_id),
    species_url VARCHAR(255) NOT NULL
)