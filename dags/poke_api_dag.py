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
    
    with TaskGroup(group_id="extract") as extract_group:
        api_response = fetch_pokemons_ids_data()
        pokemons_infos = fetch_all_pokemon_data(api_response)
        bucket = generate_bronze_table_to_minio(pokemons_infos)

pokemon_dag = pokemon_etl_pipeline()