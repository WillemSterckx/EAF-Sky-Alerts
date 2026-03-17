from google.cloud.sql.connector import Connector
import pg8000.native
from dotenv import load_dotenv
import os

load_dotenv()
db_password = os.getenv("DB_PASSWORD")

connector = Connector()

def get_conn():
    return connector.connect(
        "more-professional-week-g14:europe-west1:g14-database-1",  # INSTANCE CONNECTION NAME
        "pg8000",
        user="postgres",
        password=db_password,
        db="postgres"
    )

# Example query
conn = get_conn()
cursor = conn.cursor()
cursor.execute("SELECT * FROM weather_subscribers;")
results = cursor.fetchall()
for row in results:
    print(row)
cursor.close()
conn.close()