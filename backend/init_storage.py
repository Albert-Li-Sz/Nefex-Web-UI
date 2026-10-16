"""Idempotent bucket initialization for the Docker installation."""

import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def main():
    client = boto3.client(
        's3',
        endpoint_url=os.environ['S3_ENDPOINT_URL'],
        aws_access_key_id=os.environ['S3_ACCESS_KEY_ID'],
        aws_secret_access_key=os.environ['S3_SECRET_ACCESS_KEY'],
        region_name=os.getenv('S3_REGION_NAME', 'us-east-1'),
        config=Config(s3={'addressing_style': 'path'}, retries={'max_attempts': 5}),
    )
    bucket = os.environ['S3_BUCKET_NAME']
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode'] != 404:
            raise
        try:
            client.create_bucket(Bucket=bucket)
        except ClientError as create_error:
            if create_error.response['Error']['Code'] != 'BucketAlreadyOwnedByYou':
                raise
    print(f'Object storage bucket ready: {bucket}')


if __name__ == '__main__':
    main()
