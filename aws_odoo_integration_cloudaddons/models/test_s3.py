import boto3
from moto import mock_s3

@mock_s3
def test():
    s3 = boto3.client('s3', region_name='us-east-1')
    s3.create_bucket(Bucket='mybucket')
    s3.put_object(Bucket='mybucket', Key='Odoo/', Body=b'')
    s3.put_object(Bucket='mybucket', Key='Odoo/Customers/', Body=b'')
    
    res = s3.list_objects_v2(Bucket='mybucket', Prefix='Odoo/', Delimiter='/')
    print("Contents:")
    for c in res.get('Contents', []):
        print(" -", c['Key'])
    print("CommonPrefixes:")
    for p in res.get('CommonPrefixes', []):
        print(" -", p['Prefix'])

test()
