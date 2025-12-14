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
    
    @task(task_id="fetch_from_api")
    def fetch_pokemon_data():
        import requests
        
        url = "https://pokeapi.co/api/v2/pokemon?limit=1328"
        
        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            print(f"✓ Extraídos {len(data['results'])} pokémons")
            return data
            
        except requests.exceptions.RequestException as e:
            raise AirflowFailException(f"Erro ao acessar PokeAPI: {e}")
    
    
    @task(task_id="parse_pokemon_data")
    def parse_pokemon_list(api_data: dict):
        """Extrai IDs e nomes dos pokémons"""
        
        pokemons_list = api_data["results"]
        
        pokemons_url = [
            pokemon["url"].rstrip("/").split("/") 
            for pokemon in pokemons_list
        ]
        pokemons_id = [int(url_parts[-1]) for url_parts in pokemons_url]
        pokemons_names = [pokemon["name"] for pokemon in pokemons_list]
        
        
        return {
            'ids': pokemons_id,
            'names': pokemons_names
        }
    
    
    @task(task_id="create_duckdb_table")
    def transform_with_duckdb(parsed_data: dict):
        """Cria tabela DuckDB e converte para Arrow"""
        import duckdb
        
        ids = parsed_data['ids']
        names = parsed_data['names']
        
        conn = duckdb.connect(":memory:")
        
        conn.execute("""
            CREATE TABLE pokemons_bronze(
                id INTEGER,
                name VARCHAR(50)
            )
        """)
        
        dados = zip(ids, names)
        conn.executemany("""
            INSERT INTO pokemons_bronze (id, name)
            VALUES (?, ?)
        """, dados)
        
        arrow_table = conn.execute("""
            SELECT * FROM pokemons_bronze
        """).fetch_arrow_table()
        
        conn.close()
        
        pokemon_dict = arrow_table.to_pydict()
        
        
        return pokemon_dict
    
    
    @task(task_id="upload_parquet_to_minio")
    def upload_parquet_to_minio(pokemon_data: dict):
        """Cria Parquet e faz upload para MinIO"""
        import pyarrow as pa
        import pyarrow.parquet as pq
        from datetime import datetime, timezone
        from io import BytesIO
        import boto3
        from airflow.sdk.definitions.variable import Variable
        
        arrow_table = pa.table(pokemon_data)
        
        metadata = {
            b"source": b"https://pokeapi.co/api/v2/pokemon?limit=1328",
            b"datetime_utc": datetime.now(timezone.utc).isoformat().encode('utf-8'),
            b"layer": b"bronze",
            b"total_records": str(len(arrow_table)).encode('utf-8'),
            b"pipeline": b"pokemon_etl_pipeline",
            b"compression": b"zstd"
        }
        
        arrow_table = arrow_table.replace_schema_metadata(metadata)
        
        buffer = BytesIO()
        pq.write_table(arrow_table, buffer, compression="zstd")
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
        key = "bronze/pokemons_list.parquet"
        
        try:
            s3.create_bucket(Bucket=bucket_name)
            print(f"✓ Bucket '{bucket_name}' criado")
        except:
            print(f"Bucket '{bucket_name}' já existe")
        
        s3.put_object(
            Bucket=bucket_name,
            Key=key,
            Body=buffer.getvalue()
        )
        
        print(f"✓ Upload completo: s3://{bucket_name}/{key}")
        
        return {
            "bucket": bucket_name,
            "key": key
        }
    
    
    with TaskGroup(group_id="extract") as extract_group:
        api_response = fetch_pokemon_data()
    
    with TaskGroup(group_id="transform") as transform_group:
        parsed = parse_pokemon_list(api_response)
        transformed = transform_with_duckdb(parsed)
    
    with TaskGroup(group_id="load") as load_group:
        upload_result = upload_parquet_to_minio(transformed)


pokemon_dag = pokemon_etl_pipeline()