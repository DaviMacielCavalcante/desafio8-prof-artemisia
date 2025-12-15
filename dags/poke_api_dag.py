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

        pokemons_url = [pokemon["url"].rstrip("/").split("/") for pokemon in api_data]

        pokemons_id = [int(id[-1]) for id in pokemons_url]

        list_of_requests = [f"https://pokeapi.co/api/v2/pokemon/{id}/" for id in pokemons_id]

        with ThreadPoolExecutor(max_workers=10) as executor:
            pokemons_responses = executor.map(fetch_all_pokemons, list_of_requests)
            pokemons_responses = list(pokemons_responses)

        pokemons_responses_filtered = [r for r in pokemons_responses if r is not None]

        return pokemons_responses_filtered

    @task(task_id="generate_bronze_table_to_minio")
    def generate_bronze_table_to_minio(pokemon_dict: dict):
        from pipe.utils.etl_utils import generate_list_of_all_infos_on_pokemon
        from datetime import datetime, timezone
        from io import BytesIO
        from airflow.sdk.definitions.variable import Variable
        import polars as pl
        import pyarrow as pa
        import pyarrow.parquet as pq
        import boto3
        
        pokemon_all_infos_list = []

        pokemon_all_infos_list = [generate_list_of_all_infos_on_pokemon(pokemon) for pokemon in pokemon_dict]

        df_polars = pl.DataFrame(pokemon_all_infos_list)

        arrow_pokemon_bronze = df_polars.to_arrow()

        metadata = {
            b"source": b"https://pokeapi.co/api/v2/pokemon?limit=1328",
            b"datetime_utc": datetime.now(timezone.utc).isoformat().encode('utf-8'),
            b"layer": b"bronze",
            b"pipeline": b"pokemon_etl_pipeline",
            b"compression": b"snappy"
        }
        
        arrow_pokemon_bronze = arrow_pokemon_bronze.replace_schema_metadata(metadata)

        buffer = BytesIO()
        pq.write_table(arrow_pokemon_bronze, buffer, compression="snappy")
        buffer.seek(0)

        MINIO_USER = Variable.get("MINIO_ROOT_USER")
        MINIO_PASSWORD = Variable.get("MINIO_ROOT_PASSWORD")
        
        s3 = boto3.client(
            "s3",
            endpoint_url="http://minio:9000",
            aws_access_key_id=MINIO_USER,
            aws_secret_access_key=MINIO_PASSWORD
        )
        
        bucket_name = "pokemon-data"
        key = "bronze/pokemons_bronze.parquet"
        
        try:
            s3.create_bucket(Bucket=bucket_name)
            print(f"Bucket '{bucket_name}' criado")
        except:
            print(f"Bucket '{bucket_name}' já existe")

        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=buffer.getvalue()
        )

        return {
            "bucket": bucket_name,
            "key": key
        }
    
    with TaskGroup(group_id="extract") as extract_group:
        api_response = fetch_pokemons_ids_data()
        pokemons_infos = fetch_all_pokemon_data(api_response)
        bucket = generate_bronze_table_to_minio(pokemons_infos)

pokemon_dag = pokemon_etl_pipeline()