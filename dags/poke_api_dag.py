from datetime import datetime
from airflow.sdk import dag, task, TaskGroup
from airflow.exceptions import AirflowFailException


@dag(
    dag_id='pokemon_etl_pipeline',
    start_date=datetime(2025, 1, 1),
    schedule='@daily',
    catchup=False,
    tags=['pokemon', 'etl', 'pokeapi'],
)
def pokemon_etl_pipeline():
    
    @task(task_id="fetch_pokemons_ids_data")
    def fetch_pokemons_ids_data():
        import requests
        
        url = "https://pokeapi.co/api/v2/pokemon?limit=1328"
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            return data["results"]
            
        except requests.exceptions.RequestException as e:
            raise AirflowFailException(f"Erro ao acessar PokeAPI: {e}")
        
    @task(task_id="fetch_all_pokemon_data") 
    def fetch_all_pokemon_data(api_data: dict):
        from concurrent.futures import ThreadPoolExecutor
        from pipe.utils.etl_utils import fetch_all_pokemons
        from airflow.sdk.definitions.variable import Variable
        import json 
        import boto3

        BATCH_SIZE = 150
        MAX_WORKERS = 30

        pokemons_url = [pokemon["url"].rstrip("/").split("/") for pokemon in api_data]

        pokemons_id = [int(id[-1]) for id in pokemons_url]

        list_of_requests = [f"https://pokeapi.co/api/v2/pokemon/{id}/" for id in pokemons_id]

        MINIO_USER = Variable.get("MINIO_ROOT_USER")
        MINIO_PASSWORD = Variable.get("MINIO_ROOT_PASSWORD")

        s3 = boto3.client(
            "s3",
            endpoint_url="http://minio:9000",
            aws_access_key_id=MINIO_USER,
            aws_secret_access_key=MINIO_PASSWORD
        )

        bucket_name = "pokemon-data"
        temp_keys = []

        try:
            s3.create_bucket(Bucket=bucket_name)
            print(f"Bucket '{bucket_name}' criado")
        except:
            print(f"Bucket '{bucket_name}' já existe")


        batch_num = 1

        for i in range(0, len(list_of_requests), BATCH_SIZE):
            current_batch = list_of_requests[i:i + BATCH_SIZE]
            

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                pokemons_responses = executor.map(fetch_all_pokemons, current_batch)
                pokemons_responses = list(pokemons_responses)

            pokemons_responses_filtered = [r for r in pokemons_responses if r is not None]

            json_data = json.dumps(pokemons_responses_filtered)
            temp_key = f"{bucket_name}/temp/batch_{batch_num:04d}.json"

            s3.put_object(
                Bucket=bucket_name,
                Key=temp_key,
                Body=json_data.encode("utf-8")
            )

            temp_keys.append(temp_key)

            del current_batch, pokemons_responses, pokemons_responses_filtered, json_data

            batch_num += 1

        return {
            "bucket": bucket_name,
            "temp_keys": temp_keys
        }

    @task(task_id="generate_bronze_table_to_minio")
    def generate_bronze_table_to_minio(fetch_result: dict):
        from pipe.utils.etl_utils import generate_list_of_all_infos_on_pokemon
        from datetime import datetime, timezone
        from io import BytesIO
        from airflow.sdk.definitions.variable import Variable
        import polars as pl
        import pyarrow as pa
        import pyarrow.parquet as pq
        import boto3
        import json

        bucket_name = fetch_result["bucket"]
        json_keys = fetch_result["temp_keys"]

        MINIO_USER = Variable.get("MINIO_ROOT_USER")
        MINIO_PASSWORD = Variable.get("MINIO_ROOT_PASSWORD")
        
        s3 = boto3.client(
            "s3",
            endpoint_url="http://minio:9000",
            aws_access_key_id=MINIO_USER,
            aws_secret_access_key=MINIO_PASSWORD
        )

        parquets = []

        for key in json_keys:

            temp_json = s3.get_object(
                Bucket=bucket_name,
                Key=key
            )

            batch = json.load(temp_json['Body'])

            batchs_list = []

            for pokemon in batch:
                row_generated = generate_list_of_all_infos_on_pokemon(pokemon)
                batchs_list.append(row_generated)

            df_polars = pl.DataFrame(batchs_list)
            df_arrow = df_polars.to_arrow()

            buffer = BytesIO()
            pq.write_table(df_arrow, buffer, compression="snappy")
            buffer.seek(0)

            parquet_key = key.replace(".json", ".parquet")

            s3.put_object(
                Bucket=bucket_name,
                Key=parquet_key,
                Body=buffer.getvalue()
            )

            parquets.append(parquet_key)

            s3.delete_object(
                Bucket=bucket_name,
                Key=key
            )

            del temp_json, batch, df_polars, df_arrow, buffer

            
        tables = []

        for parquet in parquets:
            row = s3.get_object(
                Bucket=bucket_name,
                Key=parquet
            )

            buffer = BytesIO(row["Body"].read())

            table = pq.read_table(buffer)

            tables.append(table)

            del row 

        final_table = pa.concat_tables(tables, promote_options="default")

        metadata = {
            b"source": b"https://pokeapi.co/api/v2/pokemon?limit=1328",
            b"datetime_utc": datetime.now(timezone.utc).isoformat().encode('utf-8'),
            b"layer": b"bronze",
            b"pipeline": b"pokemon_etl_pipeline",
            b"compression": b"snappy"
        }
        
        final_table = final_table.replace_schema_metadata(metadata)

        buffer = BytesIO()
        pq.write_table(final_table, buffer, compression="snappy")
        buffer.seek(0)

        final_table_key = "bronze/pokemons_all_info_bronze.parquet"


        s3.put_object(
            Bucket=bucket_name,
            Key=final_table_key,
            Body=buffer.getvalue()
        )

        del final_table, buffer 

        for parquet in parquets: 
            s3.delete_object(
                Bucket=bucket_name,
                Key=parquet
            )

        return {
            "bucket": bucket_name,
            "key": final_table_key
        }
    
    @task(task_id="generate_silver_table_to_minio")
    def generate_silver_table_to_minio(bronze_info: dict):
        import pyarrow as pa
        import pyarrow.parquet as pq
        import boto3
        from airflow.sdk.definitions.variable import Variable
        from pipe.utils.etl_utils import check_list_of_struct, check_struct, clean_bools, clean_numerics, clean_string, clean_url
        from datetime import datetime, timezone
        from io import BytesIO


        bucket = bronze_info["bucket"]
        bronze_key = bronze_info["key"]

        MINIO_USER = Variable.get("MINIO_ROOT_USER")
        MINIO_PASSWORD = Variable.get("MINIO_ROOT_PASSWORD")

        s3 = boto3.client(
            "s3",
            endpoint_url="http://minio:9000",
            aws_access_key_id=MINIO_USER,
            aws_secret_access_key=MINIO_PASSWORD
        )


        obj = s3.get_object(
                Bucket=bucket,
                Key=bronze_key
            )
        
        buffer = BytesIO(obj["Body"].read())

        pokemons_parquet = pq.ParquetFile(buffer)
        
        pokemon_schema = pokemons_parquet.schema_arrow
        
        numeric_columns = ["id", "base_experience", "height", "weight", "order"]
        string_columns = ["name"]
        bool_columns = ["is_default"]
        url_columns = ["location_area_encounters"]
        list_struct_columns = ["abilities", "types", "stats", "moves", 
            "game_indices", "forms", "held_items",
            "past_types", "past_abilities"]
        struct_columns = ["sprites", "cries", "species"]

        processed_columns = {}

        for col in pokemon_schema.names:

            processed_column = None
    
            table_col = pokemons_parquet.read(columns=[col])
            col_as_array = table_col.column(col)
            
            if col in numeric_columns:
                processed_column = clean_numerics(col_as_array)

            if col in string_columns:
                processed_column = clean_string(col_as_array)

            if col in bool_columns:
                processed_column = clean_bools(col_as_array)

            if col in url_columns:
                processed_column = clean_url(col_as_array)

            if col in list_struct_columns:
                processed_column = check_list_of_struct(col_as_array, col)

            if col in struct_columns:
                processed_column = check_struct(col_as_array, col)

        
    
            processed_columns[col] = processed_column if processed_column else col_as_array

            del table_col, col_as_array


        pokemons_all_infos_silver = pa.Table.from_arrays(
        arrays=[processed_columns[name] for name in pokemon_schema.names],
        names = pokemon_schema.names
        )

        metadata = {
        b"source": bronze_key.encode(),
        b"datetime_utc": datetime.now(timezone.utc).isoformat().encode(),
        b"layer": b"silver",
        b"pipeline": b"pokemon_etl_pipeline",
        b"compression": b"snappy",
        b"validations": b"numeric_ranges,text_cleanup,struct_validation"
        }

        pokemons_all_infos_silver = pokemons_all_infos_silver.replace_schema_metadata(metadata)

        buffer = BytesIO()
        pq.write_table(pokemons_all_infos_silver, buffer, compression="snappy")
        buffer.seek(0)

        pokemons_all_infos_silver_key = "silver/pokemons_all_info_silver.parquet"


        s3.put_object(
            Bucket=bucket,
            Key=pokemons_all_infos_silver_key,
            Body=buffer.getvalue()
        )

        del pokemons_all_infos_silver, buffer 

        return {
            "bucket": bucket,
            "key": pokemons_all_infos_silver_key
        }

    @task(task_id="populate_gold_fact_table_postgres")
    def populate_gold_fact_table_postgres(silver_info: dict):
        import duckdb
        import os
        from airflow.sdk.definitions.variable import Variable
     
        
        bucket = silver_info["bucket"]
        silver_key = silver_info["key"]

        POSTGRES_USER = os.getenv("POSTGRES_USER")
        POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
        MINIO_USER = Variable.get("MINIO_ROOT_USER")
        MINIO_PASSWORD = Variable.get("MINIO_ROOT_PASSWORD")

        
        conn = duckdb.connect()
        
        conn.execute("INSTALL postgres; LOAD postgres;")
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        
        conn.execute(f"""
            SET s3_endpoint = 'minio:9000';
            SET s3_access_key_id = '{MINIO_USER}';
            SET s3_secret_access_key='{MINIO_PASSWORD}';
            SET s3_use_ssl = false;
            SET s3_url_style='path';
        """)
        
        conn.execute(f"""
            ATTACH 'postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@postgres:5432/dev'
            AS postgres_db (TYPE POSTGRES)
        """)
        
        s3_path = f"s3://{bucket}/{silver_key}"
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_pokemon AS
            SELECT 
                id,
                name, 
                base_experience, 
                height, 
                weight, 
                "order" AS order_for_sort, 
                location_area_encounters AS location,
                MAX(CASE WHEN unnest.stat.name = 'hp' THEN unnest.base_stat END) AS hp,
                MAX(CASE WHEN unnest.stat.name = 'hp' THEN unnest.effort END) AS hp_effort,
                MAX(CASE WHEN unnest.stat.name = 'attack' THEN unnest.base_stat END) AS attack,
                MAX(CASE WHEN unnest.stat.name = 'attack' THEN unnest.effort END) AS attack_effort,
                MAX(CASE WHEN unnest.stat.name = 'defense' THEN unnest.base_stat END) AS defense,
                MAX(CASE WHEN unnest.stat.name = 'defense' THEN unnest.effort END) AS defense_effort,
                MAX(CASE WHEN unnest.stat.name = 'special-attack' THEN unnest.base_stat END) AS special_attack,
                MAX(CASE WHEN unnest.stat.name = 'special-attack' THEN unnest.effort END) AS special_attack_effort,
                MAX(CASE WHEN unnest.stat.name = 'special-defense' THEN unnest.base_stat END) AS special_defense,
                MAX(CASE WHEN unnest.stat.name = 'special-defense' THEN unnest.effort END) AS special_defense_effort,
                MAX(CASE WHEN unnest.stat.name = 'speed' THEN unnest.base_stat END) AS speed,
                MAX(CASE WHEN unnest.stat.name = 'speed' THEN unnest.effort END) AS speed_effort
            FROM read_parquet('{s3_path}') AS p,
            UNNEST(p.stats)
            GROUP BY id, name, base_experience, height, weight, "order", location_area_encounters
            ORDER BY id ASC
        """)
        
        conn.execute("""
            INSERT INTO postgres_db.gold.pokemon
            SELECT * FROM temp_pokemon;
        """)
        
        conn.close()
        
    @task(task_id="populate_gold_dimensional_tables_postgres")
    def populate_gold_dimensional_tables_postgres(silver_info: dict):
        import duckdb
        import os
        from airflow.sdk.definitions.variable import Variable
     
        
        bucket = silver_info["bucket"]
        silver_key = silver_info["key"]

        POSTGRES_USER = os.getenv("POSTGRES_USER")
        POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
        MINIO_USER = Variable.get("MINIO_ROOT_USER")
        MINIO_PASSWORD = Variable.get("MINIO_ROOT_PASSWORD")

        
        conn = duckdb.connect()
        
        conn.execute("INSTALL postgres; LOAD postgres;")
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        
        conn.execute(f"""
            SET s3_endpoint = 'minio:9000';
            SET s3_access_key_id = '{MINIO_USER}';
            SET s3_secret_access_key='{MINIO_PASSWORD}';
            SET s3_use_ssl = false;
            SET s3_url_style='path';
        """)
        
        conn.execute(f"""
            ATTACH 'postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@postgres:5432/dev'
            AS postgres_db (TYPE POSTGRES)
        """)
        
        s3_path = f"s3://{bucket}/{silver_key}"
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_pokemon AS
            SELECT 
                id,
                name, 
                base_experience, 
                height, 
                weight, 
                "order" AS order_for_sort, 
                location_area_encounters AS location,
                MAX(CASE WHEN unnest.stat.name = 'hp' THEN unnest.base_stat END) AS hp,
                MAX(CASE WHEN unnest.stat.name = 'hp' THEN unnest.effort END) AS hp_effort,
                MAX(CASE WHEN unnest.stat.name = 'attack' THEN unnest.base_stat END) AS attack,
                MAX(CASE WHEN unnest.stat.name = 'attack' THEN unnest.effort END) AS attack_effort,
                MAX(CASE WHEN unnest.stat.name = 'defense' THEN unnest.base_stat END) AS defense,
                MAX(CASE WHEN unnest.stat.name = 'defense' THEN unnest.effort END) AS defense_effort,
                MAX(CASE WHEN unnest.stat.name = 'special-attack' THEN unnest.base_stat END) AS special_attack,
                MAX(CASE WHEN unnest.stat.name = 'special-attack' THEN unnest.effort END) AS special_attack_effort,
                MAX(CASE WHEN unnest.stat.name = 'special-defense' THEN unnest.base_stat END) AS special_defense,
                MAX(CASE WHEN unnest.stat.name = 'special-defense' THEN unnest.effort END) AS special_defense_effort,
                MAX(CASE WHEN unnest.stat.name = 'speed' THEN unnest.base_stat END) AS speed,
                MAX(CASE WHEN unnest.stat.name = 'speed' THEN unnest.effort END) AS speed_effort
            FROM read_parquet('{s3_path}') AS p,
            UNNEST(p.stats)
            GROUP BY id, name, base_experience, height, weight, "order", location_area_encounters
            ORDER BY id ASC
        """)
        
        # ABILITIES
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_abilities AS
            SELECT DISTINCT
                unnest.ability.name AS ability_name, 
                unnest.ability.url AS ability_url,
            FROM
                read_parquet('{s3_path}'),
                UNNEST(abilities)
            ORDER BY unnest.ability.name ASC
        """)
        
        conn.execute("""
            INSERT INTO postgres_db.gold.abilities (ability_name, ability_url)
            SELECT * FROM temp_abilities;
        """)
        
        # FORMS
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_forms AS
            SELECT 
                unnest.name,
                unnest.url
            FROM 
                read_parquet('{s3_path}') AS p,
                UNNEST(p.forms)    
            ORDER BY id        
        """);
        
        conn.execute("""
            INSERT INTO postgres_db.gold.forms (form_name, form_url)
            SELECT * FROM temp_forms;
        """)
        
        # GAMES
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_games AS
            SELECT DISTINCT
                unnest.version.name,
                unnest.version.url
            FROM read_parquet('{s3_path}'),
                UNNEST(game_indices)
        """)
        
        conn.execute("""
            INSERT INTO postgres_db.gold.games (game_name, game_url)
            SELECT * FROM temp_games;
        """)
        
        # HELD ITEMS
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_items AS
            SELECT DISTINCT
                unnest.item.name
            FROM read_parquet('{s3_path}') AS p,
            UNNEST(p.held_items)
            ORDER BY unnest.item.name ASC
        """)
        
        conn.execute("""
            INSERT INTO postgres_db.gold.items (item_name)
            SELECT * FROM temp_items;
        """)
        
        # MOVES
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_moves AS
            SELECT DISTINCT
                m.unnest.move.name,
                m.unnest.move.url
            FROM read_parquet('{s3_path}') AS p,
            UNNEST(p.moves) AS m
            ORDER BY m.unnest.move.name ASC
        """)
        
        conn.execute("""
            INSERT INTO postgres_db.gold.moves (move_name, move_url)
            SELECT * FROM temp_moves;
        """)
        
        # TYPES 
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_types AS
            SELECT DISTINCT
                unnest.type.name,
                unnest.type.url
            FROM read_parquet('{s3_path}') AS p,
            UNNEST(p.types)
            ORDER BY unnest.type.name ASC
        """)
        
        conn.execute("""
            INSERT INTO postgres_db.gold.types (type_name, type_url)
            SELECT * FROM temp_types;
        """)
        
        # SPECIES
        
        conn.execute(f"""
            CREATE TEMP TABLE temp_species AS
            SELECT DISTINCT
                p.species.name
            FROM read_parquet('{s3_path}') AS p
            ORDER BY p.id
        """)
        
        conn.execute("""
            INSERT INTO postgres_db.gold.species (species_name)
            SELECT * FROM temp_species;
        """)
        
        # BRIDGES
        
        conn.execute(f"""
            INSERT INTO postgres_db.gold.pokemon_abilities
            SELECT
                p.id,
                a.ability_id,
                unnest.is_hidden,
                unnest.slot
            FROM 
                read_parquet('{s3_path}') AS p,
                UNNEST(p.abilities),
                postgres_db.gold.abilities AS a
            WHERE unnest.ability.name = a.ability_name             
        """)
        
        conn.execute("""
            INSERT INTO postgres_db.gold.pokemon_forms
            SELECT
                p.id,
                f.form_id
            FROM 
                postgres_db.gold.pokemon AS p,
                postgres_db.gold.forms AS f
            WHERE f.form_name LIKE '%' || p.name || '%'            
        """)
        
        conn.execute(f"""
            INSERT INTO postgres_db.gold.pokemon_items
            SELECT
                p.id,
                i.item_id
            FROM 
                read_parquet('{s3_path}') AS p,
                UNNEST(p.held_items),
                postgres_db.gold.items AS i
            WHERE unnest.item.name = i.item_name
            ORDER BY p.id ASC
        """)
        
        conn.execute(f"""
            INSERT INTO postgres_db.gold.pokemon_moves
            SELECT
                p.id AS pokemon_id,
                m.move_id AS move_id
            FROM 
                read_parquet('{s3_path}') AS p,
                UNNEST(p.moves),
                postgres_db.gold.moves AS m
            WHERE unnest.move.name = m.move_name
            ORDER BY p.id ASC
        """)
        
        conn.execute(f"""
            INSERT INTO postgres_db.gold.pokemon_types
            SELECT 
                p.id,
                t.type_id,
                unnest.slot
            FROM read_parquet('{s3_path}') AS p,
            UNNEST(p.types),
            postgres_db.gold.types AS t
            WHERE unnest.type.name = t.type_name
            ORDER BY p.id ASC
        """)
        
        conn.execute(f"""
            INSERT INTO postgres_db.gold.pokemon_species
            SELECT 
                p.id,
                s.species_id,
                p.species.url        
            FROM read_parquet('{s3_path}') AS p,
            postgres_db.gold.species AS s
            WHERE p.species.name = s.species_name
            ORDER BY p.id
        """)
        
        conn.execute(f"""
            INSERT INTO postgres_db.gold.pokemon_games (pokemon_id, game_id,game_index)
            SELECT DISTINCT
                p.id AS pokemon_id,
                g.game_id,
                unnest.game_index
            FROM 
                read_parquet('{s3_path}') AS p,
                UNNEST(p.game_indices),
                postgres_db.gold.games AS g
            WHERE unnest.version.name = g.game_name
        """)
        
        conn.close()
             
    with TaskGroup(group_id="bronze") as bronze:
        api_response = fetch_pokemons_ids_data()
        pokemons_infos = fetch_all_pokemon_data(api_response)
        bronze_bucket = generate_bronze_table_to_minio(pokemons_infos)

    with TaskGroup(group_id="silver") as silver:
        silver_bucket = generate_silver_table_to_minio(bronze_bucket)
        
    with TaskGroup(group_id="gold") as gold:
        gold_fact = populate_gold_fact_table_postgres(silver_bucket)
        gold_dimensional = populate_gold_dimensional_tables_postgres(silver_bucket)
        
        gold_fact >> gold_dimensional

    bronze >> silver >> gold


pokemon_dag = pokemon_etl_pipeline()