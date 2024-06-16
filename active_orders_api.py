import mysql.connector
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import APIKeyHeader
from datetime import datetime, timedelta
from ratelimit import limits, sleep_and_retry
from pydantic import BaseModel
from config import DB_CONFIG, API_KEY, BACK_UP_LOC
import os
import asyncio


last_backup_time = None

app = FastAPI()

api_key_header = APIKeyHeader(name="X-API-Key")

class ActiveCart(BaseModel):
    profileId: int
    createdAt: datetime
    updatedAt: datetime

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

async def perform_backup():
    global last_backup_time

    current_dir = os.cwd()
    os.chdir(BACK_UP_LOC)

    # Get the current date and format it as "Monday"
    now = datetime.now()
    date_str = now.strftime('%b%d_%-I%p')

    # Get the current year
    year = now.strftime('%Y')

    # Create the output directory with the year if it doesn't exist
    output_dir = f'{year}/{date_str}/'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f'Backup directory created: {output_dir}')

        # Create a login path file with the username and password
        with open('mysql_login.cnf', 'w') as f:
            f.write(f'[client]\nuser={DB_CONFIG['user']}\npassword={DB_CONFIG['password']}\n')

        # Get a list of all tables in the database
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute('SHOW TABLES')
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        connection.close()

        # Lets Loop through each table and perform a mysqldump
        for table in tables:
            dump_file = output_dir + table + '.sql'
            dump_cmd = f'/usr/local/bin/mysqldump --defaults-file="mysql_login.cnf" -h {DB_CONFIG['host']} -P {DB_CONFIG['port']} --skip-column-statistics --no-tablespaces --routines --events --triggers {DB_CONFIG['database']} {table} > {dump_file}'
            os.system(dump_cmd)

        print(f'Backup completed at {now}')

        # Remove the login path file
        os.remove('mysql_login.cnf')

        last_backup_time = now
    else:
        print(f'Backup already exists for {date_str}. Skipping backup.')

    os.chdir(current_dir)


@app.get("/health")
@limits(calls=10, period=60) 
def health_check():
    try:
        connection = get_db_connection()
        if connection.is_connected():
            return {"status": "OK", "database": "Connected"}
        else:
            return {"status": "Error", "database": "Not Connected"}
    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        return {"status": "Error", "database": "Not Connected"}

@app.get("/active_carts")
@sleep_and_retry
@limits(calls=2, period=60) 
def get_active_carts(api_key: str = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=400, detail="Invalid API key")

    current_date = datetime.now().date()

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT profileId, createdAt, updatedAt
            FROM ylift_api.carts
            WHERE DATE(updatedAt) = %s
        """
        cursor.execute(query, (current_date,))

        active_carts = []
        for row in cursor.fetchall():
            active_cart = ActiveCart(
                profileId=row[0],
                createdAt=row[1],
                updatedAt=row[2]
            )
            active_carts.append(active_cart)

        cursor.close()
        connection.close()

        return active_carts

    except mysql.connector.Error as error:
        print(f"Error connecting to MySQL database: {error}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/backup")
@sleep_and_retry
@limits(calls=2, period=3600)
async def backup_database():
    global last_backup_time

    current_time = datetime.now()

    if last_backup_time is None or (current_time - last_backup_time) >= timedelta(hours=2):
        asyncio.create_task(perform_backup())
        return {"message": "Backup process started"}
    else:
        return {"message": "Backup skipped. Already performed within the last 2 hours."}