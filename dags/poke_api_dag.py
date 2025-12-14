from datetime import datetime, timedelta
from airflow.providers.http.operators.http import HttpOperator
from airflow.sdk import asset, Asset, Context
from airflow.exceptions import AirflowBadRequest, AirflowFailException

@asset(
    schedule="@daily",
    uri="https://pokeapi.co/api/v2/pokemon?limit=1328"
)
def fetch_pokemons_endpoint(self) -> dict[str]:
    import requests

    try:
        response = requests.get(self.uri)

        response.raise_for_status()

        return response.json()
    
    except Exception as e:

        if isinstance(e, AirflowBadRequest):
                raise AirflowBadRequest(f"Erro ao fazer a requisição para o endpoint de pokémons da PokeAPI: {e}")
        else:
            raise AirflowFailException(f"Erro inesperado: {e}")
        

@asset(schedule=fetch_pokemons_endpoint)
def generate_pokemons_list_table(fetch_pokemons_endpoint: Asset, context: Context) -> dict[str]:
    import duckdb
     
    response = context['ti'].xcom_pull(
         dag_id=fetch_pokemons_endpoint.name,
         task_ids=fetch_pokemons_endpoint.name
    )

    pokemons_list = response["results"]
    pokemons_url = [pokemon["url"].rstrip("/").split("/") for pokemon in pokemons_list]
    pokemons_id = [int(id[-1]) for id in pokemons_url]
    pokemons_names = [pokemon["name"] for pokemon in pokemons_list]

    conn = duckdb.connect(":memory:")

    conn.execute("""
    CREATE TABLE pokemons_bronze(
    id INTEGER,
    name VARCHAR(50))""")

    dados_tabela_pokemons_bronze = zip(pokemons_id, pokemons_names)
    conn.executemany("""
    INSERT INTO pokemons_bronze (id, name)
    VALUES (?, ?)
""", dados_tabela_pokemons_bronze)
    
    arrow_pokemon_bronze = conn.execute("SELECT * FROM pokemons_bronze").fetch_arrow_table()

    conn.close()

    return arrow_pokemon_bronze.to_pydict()


@asset(
    schedule=generate_pokemons_list_table
)
def save_pokemons_list_as_parquet(generate_pokemons_list_table: Asset, context: Context) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from datetime import datetime as dt, timezone
    import boto3
    from io import BytesIO
    from airflow.sdk.definitions.variable import Variable

    MINIO_USER = Variable.get("MINIO_ROOT_USER")
    MINIO_PASSWORD = Variable.get("MINIO_ROOT_PASSWORD")

    pokemon_list_dict = context['ti'].xcom_pull(
         dag_id=generate_pokemons_list_table.name,
         task_ids=generate_pokemons_list_table.name
    )

    arrow_pokemon_table = pa.table(pokemon_list_dict[0])

    metadata = {
    b"source": b"https://pokeapi.co/api/v2/pokemon?limit=1328",
    b"datetime_utc": bytes(str(dt.now(timezone.utc)), "utf-8"),
    b"layer": "bronze",
    b"python_version": b"3.12",
    b"duckdb_version": b"1.4.3",
    b"pyarrow_version": b"22.0.0",
    b"requests_version": b"2.32.5"
    }

    arrow_pokemon_table = arrow_pokemon_table.replace_schema_metadata(metadata)


    buffer = BytesIO()
    pq.write_table(arrow_pokemon_table, buffer,compression="snappy")
    buffer.seek(0)

    s3 = boto3.client(
         "s3",
         endpoint_url="http://minio:9000",
         aws_access_key_id = MINIO_USER,
         aws_secret_access_key = MINIO_PASSWORD
    )

    try:
        s3.create_bucket(Bucket="pokemon-data")
    except:
        print("Bucket já existe, prosseguindo...")

    s3.put_object(
        Bucket="pokemon-data",
        Key="bronze/pokemons_list.parquet",
        Body=buffer.getvalue()
    )

    return {
        "status": "success",
        "bucket": "pokemon-data",
        "key": "bronze/pokemons_list.parquet"
    }