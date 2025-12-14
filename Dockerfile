FROM apache/airflow:3.1.1 
COPY requirements.txt .
COPY pyproject.toml .
RUN pip install -r requirements.txt