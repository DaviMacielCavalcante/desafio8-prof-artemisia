import requests
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
