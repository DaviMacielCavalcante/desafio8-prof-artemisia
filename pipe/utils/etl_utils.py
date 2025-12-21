import requests
import pyarrow as pa
import pyarrow.compute as pc
def fetch_all_pokemons(url: str):

    try:
        response = requests.get(url)
        return response.json()
    except Exception as e:
        print(e)
        return None

def generate_list_of_all_infos_on_pokemon(pokemon_dict):

    pokemon_entity_bronze = {
            "id": None,
            "name": None,
            "base_experience": None,
            "height": None,
            "is_default": None,
            "order": None,
            "weight": None,
            "abilities": None,
            "forms": None,
            "game_indices": None,
            "held_items": None,
            "location_area_encounters": None,
            "moves": None,
            "past_types": None,
            "past_abilities": None,
            "sprites": None,
            "cries": None,
            "species": None,
            "stats": None,
            "types": None
        }

    pokemon_entity = pokemon_entity_bronze.copy()
    for k in pokemon_entity_bronze.keys():
        pokemon_entity[k] = pokemon_dict[k]
    return pokemon_entity

def clean_numerics(array):
    
    col_without_nulls = pc.fill_null(array, 0)

    col_int64 = pc.cast(col_without_nulls, pa.int64())

    negatives = pc.less(col_int64, 0)

    col_no_negatives = pc.if_else(negatives, 0, col_int64)

    return col_no_negatives

def clean_string(array):

    lower_case_strings = pc.utf8_lower(array)

    trimmed_strings = pc.utf8_trim_whitespace(lower_case_strings)

    not_null_strings = pc.fill_null(trimmed_strings, "")

    return not_null_strings

def clean_bools(array):

    boll_without_null = pc.fill_null(array, False)

    col_bool = pc.cast(boll_without_null, pa.bool_())

    return col_bool

def clean_url(array):

    trimmed = pc.utf8_trim_whitespace(array)

    return trimmed

def check_list_of_struct(array, col_name):
    
    if col_name == "types":
        types_lenghts = pc.list_value_length(array)

        nulls = pc.equal(types_lenghts, 0)

    return array

def check_struct(array, col_name):

    if col_name == "species":
        species_name = pc.struct_field(array, "name")

        null_number = pc.sum(pc.is_null(species_name)).as_py()

        if null_number > 0:
             print(f"AVISO: {null_number} registros com {species_name} null")

    return array