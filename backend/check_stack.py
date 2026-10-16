"""Exercise the live Docker installation without requiring a model API key."""

import json
import os
import sys
import urllib.parse
import urllib.request
import uuid


def check_health():
    for path in ('/health', '/health/db'):
        with urllib.request.urlopen(f'http://localhost:8080{path}', timeout=15) as response:
            assert json.load(response)['status'], f'{path} did not report ready'


def check_postgres():
    import psycopg

    with psycopg.connect(os.environ['DATABASE_URL']) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            assert cursor.fetchone(), 'pgvector extension is missing'
            cursor.execute('CREATE TEMP TABLE installation_check (embedding vector(3))')
            cursor.execute("INSERT INTO installation_check VALUES ('[1,0,0]'), ('[0,1,0]')")
            cursor.execute("SELECT embedding <=> '[1,0,0]'::vector FROM installation_check ORDER BY 1 LIMIT 1")
            assert cursor.fetchone()[0] == 0, 'vector search returned an unexpected distance'
        connection.rollback()


def check_storage():
    import boto3
    from botocore.config import Config

    client = boto3.client(
        's3',
        endpoint_url=os.environ['S3_ENDPOINT_URL'],
        aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'],
        region_name=os.environ['S3_REGION_NAME'],
        config=Config(s3={'addressing_style': 'path'}),
    )
    bucket = os.environ['S3_BUCKET_NAME']
    key = f'installation-check/{uuid.uuid4()}.txt'
    try:
        client.put_object(Bucket=bucket, Key=key, Body=b'storage round trip')
        response = client.get_object(Bucket=bucket, Key=key)
        with response['Body'] as body:
            assert body.read() == b'storage round trip', 'object storage content differs'
    finally:
        client.delete_object(Bucket=bucket, Key=key)


def check_redis():
    import redis

    with redis.Redis.from_url(os.environ['REDIS_URL']) as client:
        key = f'installation-check:{uuid.uuid4()}'
        try:
            assert client.set(key, 'ok', ex=60)
            assert client.get(key) == b'ok', 'Redis value differs'
            with client.pubsub() as pubsub:
                pubsub.subscribe(key)
                assert pubsub.get_message(timeout=5)['type'] == 'subscribe'
                client.publish(key, 'ok')
                assert pubsub.get_message(timeout=5)['data'] == b'ok', 'Redis pub/sub failed'
        finally:
            client.delete(key)


def check_search():
    params = urllib.parse.urlencode({'q': 'Python programming language', 'format': 'json'})
    with urllib.request.urlopen(f'{os.environ["SEARXNG_QUERY_URL"]}?{params}', timeout=60) as response:
        payload = json.load(response)
    assert any(result.get('url') for result in payload.get('results', [])), (
        'SearXNG returned no usable upstream results. Check outbound connectivity and search engines; '
        f'unresponsive engines: {payload.get("unresponsive_engines", [])}'
    )


def check_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        with playwright.chromium.launch(args=['--no-sandbox']) as browser:
            page = browser.new_page()
            page.set_content('<title>Installation check</title><p>Browser retrieval ready</p>')
            assert page.title() == 'Installation check'


def main():
    failed = False
    for label, check in (
        ('Application and database health', check_health),
        ('PostgreSQL and pgvector similarity search', check_postgres),
        ('MinIO upload/download/delete', check_storage),
        ('Redis read/write and pub/sub', check_redis),
        ('SearXNG JSON API and upstream search results', check_search),
        ('Chromium web retrieval runtime', check_browser),
    ):
        try:
            check()
            print(f'PASS {label}', flush=True)
        except Exception as error:
            failed = True
            print(f'FAIL {label}: {error}', file=sys.stderr, flush=True)
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
