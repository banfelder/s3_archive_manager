# S3 Archive Manager

A utility for verifying the MD5 checksum of objects in an S3 bucket, and then transitioning them to another archive class with the MD5 checksum annotated in new object's metadata.

__CAUTION__:
This utility will read potentially very large files from an S3 bucket.
It is intended to be run on an EC2 instance in the same AWS region as the S3 buckets it reads and writes from.
If it is run elsewhere, processing of large S3 objects can result in expensive data egress charges from AWS.

## **Overview**

The `transition_object_to_archive` command will...

1. Compute the MD5 checksum of an object found in an S3 archive ingest bucket.
1. Verify that the computed MD5 checksum matches an expected MD5 checksum; an error will be raised and no further processing will take place if the computer and expected checksums do not match.
1. Copy the S3 object to an S3 archive bucket; the copied object will have the MD5 checksum included in its metadata.
1. Optionally remove the object from the ingest bucket.

The `transition_all_objects_to_archive` command will do the above for all objects in the ingest bucket that have an expected MD5 checksum in their metadata.

All activity is logged to an AWS CloudWatch log group if it is specified.

## **Suggested Setup**

This section outlines a reasonable way to set up archiving of a smaller number of large files on AWS.
The strategy presented here is used by the author to archive backup copies of raw genomic sequence data for a scientific laboratory, and is being used to handle about 100 .tgz files per year, each ranging in size from 15 GB to 60 GB.

### **Assumptions**

* Everything takes place in the same AWS region, which is your default AWS region.
This procedure has been vetted for the us-east-1 region only.
This procedure has *not* been vetted for multi-region deployments.

* You will be setting up the environment using the AWS CLI.
While this could all be done from the AWS Console, use of the CLI is more consistent and less error prone.
You will of course need to have the AWS CLI set up and configured to work with your AWS account (see the AWS CLI documentation as needed).

* You have the needed privileges to perform the setup.
You will need to be able to create and manipulate S3 buckets, CloudWatch Logs log groups, EC2 instances, and IAM roles.

### **Preparation**

* Determine the following before you get started:

| Shell Variable | Notes |
| ---------------|-------|
| AWS_ACCOUNT_ID | the ID number of your AWS account
| AWS_REGION | the AWS Region where your buckets and logs will be created (only 'us-east-1' has been vetted)
| PROJECT_NAME | a tag that will be applied to all AWS resources that are part of this effort; this is up to you. The names of non-user-facing resources will be derived from this name.
| ARCHIVE_INGEST_BUCKET_NAME | the name of the bucket to which you will upload files using the AWS CLI
| ARCHIVE_BUCKET_NAME | the name of the bucket that will hold archived data for the long term
| ARCHIVE_STORAGE_CLASS | the storage class of objects that are archived (one of STANDARD, DEEP_ARCHIVE, or other AWS S3 storage classes)
| CLOUDWATCH_LOG_GROUP_NAME | the name of the Log Group is AWS CloudWatch where activity will be logged; it might be the same (or related to) the project name

* `cp setup_env-example.sh setup_env.sh`
* Edit `setup_env.sh` for your environment.
* `source setup_env.sh`

### **Create an Archive Ingest Bucket**

Create a new bucket and make sure it is not accessible publicly. As this is just a temporary landing spot for incoming data, this bucket is not versioned.

```bash
aws s3api create-bucket --bucket ${ARCHIVE_INGEST_BUCKET_NAME}
aws s3api put-public-access-block --bucket ${ARCHIVE_INGEST_BUCKET_NAME} --public-access-block-configuration=BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-tagging --bucket ${ARCHIVE_INGEST_BUCKET_NAME} --tagging "TagSet=[{Key=project,Value=${PROJECT_NAME}}]"
```

### **Create an Archive Bucket**

Create a new bucket and make sure it is not accessible publicly.

```bash
aws s3api create-bucket --bucket ${ARCHIVE_BUCKET_NAME}
aws s3api put-public-access-block --bucket ${ARCHIVE_BUCKET_NAME} --public-access-block-configuration=BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-tagging --bucket ${ARCHIVE_BUCKET_NAME} --tagging "TagSet=[{Key=project,Value=${PROJECT_NAME}}]"
```
This bucket is versioned to protect against accidental manipulations.
You will need access to the root login of your AWS account to remove versioned objects from this bucket, so consider skipping this step if your work is experimental.

```bash
aws s3api put-bucket-versioning --bucket ${ARCHIVE_BUCKET_NAME} --versioning-configuration Status=Enabled
```

### **Create Log Group**

```bash
aws logs create-log-group --log-group-name ${CLOUDWATCH_LOG_GROUP_NAME} --tags="project=${PROJECT_NAME}"
```

### **Create Application Policy**

Create a policy document...
```bash
cat > ${PROJECT_NAME}-application-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "logs:PutLogEvents"
            ],
            "Resource": "arn:aws:logs:*:*:log-group:${CLOUDWATCH_LOG_GROUP_NAME}:*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogStream"
            ],
            "Resource": "arn:aws:logs:*:*:log-group:${CLOUDWATCH_LOG_GROUP_NAME}:*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:DeleteObject"
            ],
            "Resource": "arn:aws:s3:::${ARCHIVE_INGEST_BUCKET_NAME}/*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:ListBucket"
            ],
            "Resource": "arn:aws:s3:::${ARCHIVE_INGEST_BUCKET_NAME}"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:PutObject"
            ],
            "Resource": "arn:aws:s3:::${ARCHIVE_BUCKET_NAME}/*"
        }
    ]
}
EOF
```

...and then create a policy based on that document.
```bash
aws iam create-policy --policy-name ${PROJECT_NAME}-application-policy --policy-document file://${PROJECT_NAME}-application-policy.json --tags="Key=project,Value=${PROJECT_NAME}"
```

### **Create Role Trust Policy**

```bash
cat > ${PROJECT_NAME}-role-trust-policy.json << EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Service": [
                    "ec2.amazonaws.com"
                ]
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
EOF
```

### **Create IAM Role**

```bash
aws iam create-role --role-name ${PROJECT_NAME}-role --assume-role-policy-document file://${PROJECT_NAME}-role-trust-policy.json --tags="Key=project,Value=${PROJECT_NAME}" 
aws iam attach-role-policy --role-name ${PROJECT_NAME}-role --policy-arn arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${PROJECT_NAME}-application-policy
```

### **Create Instance Profile**

```bash
aws iam create-instance-profile --instance-profile-name ${PROJECT_NAME}-profile --tags="Key=project,Value=${PROJECT_NAME}"
aws iam add-role-to-instance-profile --role-name ${PROJECT_NAME}-role --instance-profile-name ${PROJECT_NAME}-profile
```


### **Create EC2 Security Group**

```bash
aws ec2 create-security-group --group-name "${PROJECT_NAME}-secgrp" --description "Security Group for ${PROJECT_NAME}" --tag-specifications="ResourceType=security-group,Tags=[{Key=project,Value=${PROJECT_NAME}}]"
aws ec2 authorize-security-group-ingress --group-name "${PROJECT_NAME}-secgrp" --protocol tcp --port 22 --cidr 0.0.0.0/0
```
### **Create the EC2 Instance Provisioning Script**

```bash
cat > ${PROJECT_NAME}-provision.sh << EOF
#!/bin/bash
yum -y update
yum -y install git
sudo -u ec2-user ssh-keyscan -t rsa github.com >> ~ec2-user/.ssh/known_hosts
sudo -u ec2-user git clone https://github.com/banfelder/s3_archive_manager.git ~ec2-user/s3_archive_manager
sudo -u ec2-user python3 -m pip install -r ~ec2-user/s3_archive_manager/requirements.txt

echo "aws_region = \"${AWS_REGION}\"" > ~ec2-user/arch-mgr.cfg
echo "ingest_bucket = \"${ARCHIVE_INGEST_BUCKET_NAME}\"" >> ~ec2-user/arch-mgr.cfg
echo "archive_bucket = \"${ARCHIVE_BUCKET_NAME}\"" >> ~ec2-user/arch-mgr.cfg
echo "archive_storage_class = \"${ARCHIVE_STORAGE_CLASS}\"" >> ~ec2-user/arch-mgr.cfg
echo "remove_from_ingest_bucket = true" >> ~ec2-user/arch-mgr.cfg
echo "cloudwatch_log_group = \"${CLOUDWATCH_LOG_GROUP_NAME}\"" >> ~ec2-user/arch-mgr.cfg
chown ec2-user: ~ec2-user/arch-mgr.cfg

shutdown -h now
EOF
```

### **Create an EC2 Instance**

Create an EC2 instance and provision it.
The instance will shutdown itself once it is provisioned; this should take just a couple of minutes.
You should not use the instance to transition object to archive until the provision process is completed and in the instance has stopped.

```bash
aws ec2 run-instances --image-id ami-0b0dcb5067f052a63 --instance-type t2.micro --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=i-${PROJECT_NAME}},{Key=project,Value=${PROJECT_NAME}}]" --iam-instance-profile="Name=${PROJECT_NAME}-profile" --security-groups="${PROJECT_NAME}-secgrp" --user-data file://${PROJECT_NAME}-provision.sh
INSTANCE_ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=i-${PROJECT_NAME} Name=instance-state-name,Values=stopped,pending,running,shutting-down,stopping,stopped --output text --query 'Reservations[*].Instances[*].InstanceId' )
```

## Using

### **Upload File to Ingest Bucket**

Upload files to the ingest bucket, including a locally computed MD5 checksums.

```bash
FILEPATH="${HOME}/my_file.tgz"
aws s3 cp ${FILEPATH} s3://${ARCHIVE_INGEST_BUCKET_NAME} --metadata="md5sum=$(md5sum $FILEPATH | cut -f1 -d' ')"
```

### **Migrate Uploaded Files to Archive**

Start the instance, connect to it as `ec2-user`, and run the `transition_all_objects_to_archive` command.
Shutdown the instance when your done.

```bash
python3 s3_archive_manager/arch-mgr.py transition_all_objects_to_archive
sudo shutdown -h now
```
