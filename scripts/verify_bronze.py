import os

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

load_dotenv()

engine = create_engine(
    URL.create(
        "postgresql+psycopg2",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "evgrid"),
    )
)

print(pd.read_sql("SELECT COUNT(*) AS charging_station_rows FROM bronze.charging_stations", engine))
print(pd.read_sql("SELECT * FROM bronze.charging_stations LIMIT 5", engine))
