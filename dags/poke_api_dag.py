from datetime import datetime, timedelta
from airflow.providers.http.operators.http import HttpOperator
from airflow.sdk import asset, Asset, Context
from airflow.exceptions import AirflowBadRequest, AirflowFailException
from pprint import pprint

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
def pokemons_list(fetch_pokemons_endpoint: Asset, context: Context) -> dict[str]:
     
    response = context['ti'].xcom_pull(
         dag_id=fetch_pokemons_endpoint.name,
         task_ids=fetch_pokemons_endpoint.name,
         include_prior_dates=True
    )

    return response["results"]


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

   
