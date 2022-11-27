#!/bin/bash

# Provisioning script that gets a new EC2 instance ready to run the s3_archive_manager utility

sudo yum -y update
sudo yum -y install git
ssh-keyscan -t rsa github.com >> ~/.ssh/known_hosts
git clone https://github.com/banfelder/s3_archive_manager.git
python3 -m pip install -r s3_archive_manager/requirements.txt
sudo shutdown -h now
