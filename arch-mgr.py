#!/usr/bin/env python3

import hashlib
from pathlib import Path

import boto3
import fire
import tomli

import cloud_watch_logger


logger = None

def transition_object_to_archive(key,
                                 expected_md5_sum = None,
                                 ingest_bucket = None,
                                 archive_bucket = None,
                                 archive_storage_class = None,
                                 remove_from_ingest_bucket = None):

    class CopyCallbackManager():

        def __init__(self):
            self.reset()

        def increment_byte_counter(self, byte_count):
            self.byte_counter += byte_count
            logger.log(str(self.byte_counter) + " bytes copied so far.")

        def reset(self):
            self.byte_counter = 0

    ingest_bucket = ingest_bucket or configuration['ingest_bucket']
    archive_bucket = archive_bucket or configuration['archive_bucket']
    archive_storage_class = archive_storage_class or configuration['archive_storage_class']
    remove_from_ingest_bucket = remove_from_ingest_bucket or configuration['remove_from_ingest_bucket']

    if not key:
        raise ValueError('key not specified')
    if not ingest_bucket:
        raise ValueError('ingest_bucket not specified')
    if not archive_bucket:
        raise ValueError('archive_bucket not specified')
    if remove_from_ingest_bucket is None:
        raise ValueError('remove_from_ingest_bucket not specified')

    logger.log("Entering transition_object_to_archive")

    logger.log("KEY: " + key)
    logger.log("INGEST_BUCKET: " + ingest_bucket)
    logger.log("ARCHIVE_BUCKET: " + archive_bucket)
    logger.log("ARCHIVE_STORAGE_CLASS: " + archive_storage_class)
    logger.log("REMOVE_FROM_INGEST_BUCKET: " + str(remove_from_ingest_bucket))

    if not expected_md5_sum:
        logger.log("Checking ingested object metadata for md5sum")
        metadata = s3.head_object(Bucket=ingest_bucket, Key=key)['Metadata']
        if 'md5sum' in metadata:
            expected_md5_sum = metadata['md5sum']
    
    if expected_md5_sum:
        logger.log("EXPECTED_MD5_SUM: " + expected_md5_sum)
    else:
        raise ValueError('expected_md5_sum not specified (must be specified or read from ingest object metadata)')    

    hexdigest = compute_object_md5_sum(key = key, bucket = ingest_bucket)
    if hexdigest != expected_md5_sum:
        raise ValueError('object does not have expected md5 checksum')

    logger.log("Starting copy to archive bucket")
    copy_callback_manager = CopyCallbackManager()
    s3.copy({'Bucket': ingest_bucket, 'Key': key},
            archive_bucket, key,
            ExtraArgs = {'Metadata': {'md5sum': hexdigest},
                         'MetadataDirective': 'REPLACE',
                         'StorageClass': archive_storage_class},
            Callback = lambda byte_count: copy_callback_manager.increment_byte_counter(byte_count) 
            )
    logger.log("Ending copy to archive bucket")

    if remove_from_ingest_bucket:
        logger.log("Starting remove from ingest bucket")
        s3.delete_object(Bucket=ingest_bucket, Key=key)
        logger.log("Ending remove from ingest bucket")

    logger.log("Exiting transition_object_to_archive")

def compute_object_md5_sum(key = None,
                           bucket = None):

    logger.log("Entering compute_object_md5_sum START")

    bucket = bucket or configuration['ingest_bucket']

    if not bucket:
        raise ValueError('bucket not specified')
    if not key:
        raise ValueError('key not specified')

    chunk_size = 8 * 1024 * 1024  # 8 MByte
    logger.log("CHUNKSIZE: " + str(chunk_size / 1024 / 1024) + " MB")

    logger.log("BUCKET: " + bucket)
    logger.log("KEY: " + key)

    body = s3.get_object(Bucket=bucket, Key=key)['Body']

    object_hash = hashlib.md5()
    chunk_count = 0

    for chunk in body.iter_chunks(chunk_size = chunk_size):
        object_hash.update(chunk)
        chunk_count += 1
        if chunk_count % 100 == 0:
            logger.log("CHUNK " + str(chunk_count) + " PROCESSED")
    hexdigest = object_hash.hexdigest()

    logger.log("CHUNK_COUNT: " + str(chunk_count))
    logger.log("MD5: " + hexdigest)

    logger.log("Exiting compute_object_md5_sum DONE")

    return(hexdigest)

def get_configuration():

    def augment_config(current_config, new_info_path):
        if new_info_path.is_file():
            with open(new_info_path, mode="rb") as fp:
                # This is will not work if we ever support nested attributes in the config
                current_config = {**current_config, **tomli.load(fp)}
        return current_config

    default_config = {
        "ingest_bucket": None,
        "archive_bucket": None,
        "archive_storage_class": "STANDARD",
        "remove_from_ingest_bucket": False,
    }

    config = default_config
    config = augment_config(config, Path.home() / "arch-mgr.cfg")
    config = augment_config(config, Path.cwd() / "arch-mgr.cfg")

    return config


if __name__ == "__main__":

    s3 = boto3.client('s3')
    configuration = get_configuration()

    aws_region = None
    cloudwatch_log_group = None
    if ("cloudwatch_log_group" in configuration) and ("aws_region" in configuration):
        cloudwatch_log_group = configuration['cloudwatch_log_group']
        aws_region = configuration['aws_region']

    with cloud_watch_logger.CloudWatchLogger(log_group_name=cloudwatch_log_group,
                                             region=aws_region,
                                             app_name='s3_archive_manager',
                                             enable_exception_logging = True) as l:
        logger = l
        fire.Fire()
