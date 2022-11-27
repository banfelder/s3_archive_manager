#!/usr/bin/env python3

import hashlib
from pathlib import Path

import boto3
import fire
import tomli


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
            print(str(self.byte_counter) + " bytes copied so far.")

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

    print(">> copy_object_to_archive START")

    print("KEY: " + key)
    print("INGEST_BUCKET: " + ingest_bucket)
    print("ARCHIVE_BUCKET: " + archive_bucket)
    print("ARCHIVE_STORAGE_CLASS: " + archive_storage_class)
    print("REMOVE_FROM_INGEST_BUCKET: " + str(remove_from_ingest_bucket))

    if not expected_md5_sum:
        print("CHECKING ingested object metadata for md5sum")
        metadata = s3.head_object(Bucket=ingest_bucket, Key=key)['Metadata']
        if 'md5sum' in metadata:
            expected_md5_sum = metadata['md5sum']
    
    if expected_md5_sum:
        print("EXPECTED_MD5_SUM: " + expected_md5_sum)
    else:
        raise ValueError('expected_md5_sum not specified (must be specified or read from ingest object metadata)')    

    hexdigest = compute_object_md5_sum(key = key, bucket = ingest_bucket)
    if hexdigest != expected_md5_sum:
        raise ValueError('object does not have expected md5 checksum')

    print("BEGIN copy to archive bucket")
    copy_callback_manager = CopyCallbackManager()
    s3.copy({'Bucket': ingest_bucket, 'Key': key},
            archive_bucket, key,
            ExtraArgs = {'Metadata': {'md5sum': hexdigest},
                         'MetadataDirective': 'REPLACE',
                         'StorageClass': archive_storage_class},
            Callback = lambda byte_count: copy_callback_manager.increment_byte_counter(byte_count) 
            )
    print("END copy to archive bucket")

    if remove_from_ingest_bucket:
        print("BEGIN remove from ingest bucket")
        s3.delete_object(Bucket=ingest_bucket, Key=key)
        print("END remove from ingest bucket")

    print(">> copy_object_to_archive DONE")

def compute_object_md5_sum(key = None,
                           bucket = None):

    print(">> compute_object_md5_sum START")

    bucket = bucket or configuration['ingest_bucket']

    if not bucket:
        raise ValueError('bucket not specified')
    if not key:
        raise ValueError('key not specified')

    chunk_size = 8 * 1024 * 1024  # 8 MByte
    print("CHUNKSIZE: " + str(chunk_size / 1024 / 1024) + " MB")

    print("BUCKET: " + bucket)
    print("KEY: " + key)

    body = s3.get_object(Bucket=bucket, Key=key)['Body']

    object_hash = hashlib.md5()
    chunk_count = 0

    for chunk in body.iter_chunks(chunk_size = chunk_size):
        object_hash.update(chunk)
        chunk_count += 1
        if chunk_count % 100 == 0:
            print("CHUNK " + str(chunk_count) + " PROCESSED")
    hexdigest = object_hash.hexdigest()

    print("CHUNK_COUNT: " + str(chunk_count))
    print("MD5: " + hexdigest)

    print(">> compute_object_md5_sum DONE")

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

    fire.Fire()
