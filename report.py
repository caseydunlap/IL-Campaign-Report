import sys
import boto3
import openpyxl
import pandas as pd
import numpy as np
import pytz
import json
import urllib
import math
import time
import re
from io import BytesIO
import io
from sqlalchemy.sql import text
from sqlalchemy.types import VARCHAR
from datetime import datetime,timedelta,timezone,date,time
import requests
from sqlalchemy import create_engine
from requests.auth import HTTPBasicAuth
from decimal import Decimal
import base64
from urllib.parse import quote
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_der_private_key

def get_secrets(secret_names, region_name="us-east-1"):
    secrets = {}
    
    client = boto3.client(
        service_name='secretsmanager',
        region_name=region_name
    )
    
    for secret_name in secret_names:
        try:
            get_secret_value_response = client.get_secret_value(
                SecretId=secret_name)
        except Exception as e:
                raise e
        else:
            if 'SecretString' in get_secret_value_response:
                secrets[secret_name] = get_secret_value_response['SecretString']
            else:
                secrets[secret_name] = base64.b64decode(get_secret_value_response['SecretBinary'])

    return secrets
    
def extract_secret_value(data):
    if isinstance(data, str):
        return json.loads(data)
    return data

secrets = ['graph_secret_id','graph_client_id','graph_tenant_id','sharepoint_url_base','sharepoint_url_end',
'zoom_client_id','zoom_account_id','zoom_secret_id','zoom_webinar_user_ids','snowflake_bizops_user','snowflake_account','snowflake_salesmarketing_schema','snowflake_fivetran_db','snowflake_bizops_role','snowflake_key_pass','snowflake_bizops_wh']

fetch_secrets = get_secrets(secrets)

extracted_secrets = {key: extract_secret_value(value) for key, value in fetch_secrets.items()}

graph_secret = extracted_secrets['graph_secret_id']['graph_secret_id']
graph_client_id = extracted_secrets['graph_client_id']['graph_client_id']
graph_tenant_id = extracted_secrets['graph_tenant_id']['graph_tenant_id']
sharepoint_url_base = extracted_secrets['sharepoint_url_base']['sharepoint_url_base']
sharepoint_url_end = extracted_secrets['sharepoint_url_end']['sharepoint_url_end']
zoom_client_id = extracted_secrets['zoom_client_id']['zoom_client_id']
zoom_account_id = extracted_secrets['zoom_account_id']['zoom_account_id']
zoom_secret_id = extracted_secrets['zoom_secret_id']['zoom_secret_id']
zoom_webinar_user_id_raw = extracted_secrets['zoom_webinar_user_ids']['zoom_webinar_user_ids']
zoom_user_ids = [i for i in zoom_webinar_user_id_raw.split(',') if i.strip()]
snowflake_user = extracted_secrets['snowflake_bizops_user']['snowflake_bizops_user']
snowflake_account = extracted_secrets['snowflake_account']['snowflake_account']
snowflake_key_pass = extracted_secrets['snowflake_key_pass']['snowflake_key_pass']
snowflake_bizops_wh = extracted_secrets['snowflake_bizops_wh']['snowflake_bizops_wh']
snowflake_schema = extracted_secrets['snowflake_salesmarketing_schema']['snowflake_salesmarketing_schema']
snowflake_fivetran_db = extracted_secrets['snowflake_fivetran_db']['snowflake_fivetran_db']
snowflake_role = extracted_secrets['snowflake_bizops_role']['snowflake_bizops_role']

password = snowflake_key_pass.encode()

# AWS S3 Configuration
s3_bucket = 'aws-glue-assets-bianalytics'
s3_key = 'BIZ_OPS_ETL_USER.p8'

# Function to download file from S3
def download_from_s3(bucket, key):
    s3_client = boto3.client('s3')
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return response['Body'].read()
    except Exception as e:
        print(f"Error downloading from S3: {e}")
        return None

# Download the private key file from S3
key_data = download_from_s3(s3_bucket, s3_key)

# Try loading the private key as PEM
private_key = load_pem_private_key(key_data, password=password)

# Extract the private key bytes in PKCS8 format
private_key_bytes = private_key.private_bytes(
    encoding=serialization.Encoding.DER,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
)

#Use the Microsfot Graph API to get the Cognito Form and Provider Jumpoff lists
secret = graph_secret
client_id = graph_client_id
tenant_id = graph_tenant_id

url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'

data = {
    'grant_type': 'client_credentials',
    'client_id': client_id,
    'client_secret': secret,
    'scope':  'https://graph.microsoft.com/.default'}
response = requests.post(url, data=data)
response_json = response.json()
access_token = response_json.get('access_token')

url = f"https://graph.microsoft.com/v1.0/sites/{sharepoint_url_base}:/personal/{sharepoint_url_end}"

headers = {
    "Authorization": f"Bearer {access_token}"
}

response = requests.get(url, headers=headers)
site_data = response.json()
site_id = site_data.get("id")

headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/json"
}

response = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives", headers=headers)

drive_id = None
if response.status_code == 200:
    drives = response.json().get('value', [])
    for drive in drives:
        if drive['name']== 'OneDrive':
            drive_id = drive['id']
            break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children'

headers = {
    'Authorization': f'Bearer {access_token}'
}

response = requests.get(url, headers=headers)
items = response.json()

for item in items['value']:
    if item['name'] == 'Desktop':
        item_id = item['id']
        break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children'

response = requests.get(url, headers=headers)
children = response.json().get('value', [])

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children'

response = requests.get(url, headers=headers)
children = response.json().get('value', [])

for child in children:
    if child['name'] == 'Cognito':
        child_item_id = child['id']
        break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{child_item_id}/children'

response = requests.get(url, headers=headers)
nested_children = response.json().get('value', [])

for child in nested_children:
    if child['name'] == 'Illinois':
        nested_child_item_id = child['id']
        break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{nested_child_item_id}/children'

response = requests.get(url, headers=headers)
nested_children_final = response.json().get('value', [])

for child in nested_children_final:
    if child['name'] == 'Illinois Provider Jumpoff.xlsx':
        final_nested_child_item_id = child['id']
        break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{final_nested_child_item_id}/content'

response = requests.get(url, headers=headers)
illinois_jumpoff = pd.read_excel(BytesIO(response.content), dtype={'Provider TAX ID': str})

url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'

data = {
    'grant_type': 'client_credentials',
    'client_id': client_id,
    'client_secret': secret,
    'scope':  'https://graph.microsoft.com/.default'}
response = requests.post(url, data=data)
response_json = response.json()
access_token = response_json.get('access_token')

url = f"https://graph.microsoft.com/v1.0/sites/{sharepoint_url_base}:/personal/{sharepoint_url_end}"
headers = {
    "Authorization": f"Bearer {access_token}"
}
response = requests.get(url, headers=headers)
site_data = response.json()
site_id = site_data.get("id")

headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/json"
}

response = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives", headers=headers)

drive_id = None
if response.status_code == 200:
    drives = response.json().get('value', [])
    for drive in drives:
        if drive['name']== 'OneDrive':
            drive_id = drive['id']
            break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children'

headers = {
    'Authorization': f'Bearer {access_token}'
}

response = requests.get(url, headers=headers)
items = response.json()

for item in items['value']:
    if item['name'] == 'Cognito Forms':
        item_id = item['id']
        break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children'


response = requests.get(url, headers=headers)
children = response.json().get('value', [])

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children'

response = requests.get(url, headers=headers)
children = response.json().get('value', [])

for child in children:
    if child['name'] == 'Illinois':
        child_item_id = child['id']
        break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{child_item_id}/children'

response = requests.get(url, headers=headers)
nested_children_final = response.json().get('value', [])

for child in nested_children_final:
    if child['name'] == 'Illinois_Stream.xlsx':
        final_nested_child_item_id = child['id']
        break

url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{final_nested_child_item_id}/content'

response = requests.get(url, headers=headers)
cognito_form = pd.read_excel(BytesIO(response.content))

#Function to format Cognito data into usable format
def reformat_df(df):
    df['FederalTaxID'] = df.groupby('Illinois_Id')['FederalTaxID'].transform(lambda x: x.ffill().bfill())
    df['NPI'] = df.groupby('Illinois_Id')['NPI'].transform(lambda x: x.ffill().bfill())
    
    df['DoesYourAgencyCurrentlyUseAnEVVSystemToCaptureTheStartTimeEndTimeAndLocationOfTheMembersService'] = df.groupby('Illinois_Id')['DoesYourAgencyCurrentlyUseAnEVVSystemToCaptureTheStartTimeEndTimeAndLocationOfTheMembersService'].transform(lambda x: x.bfill().ffill())
    
    df = df.drop_duplicates(subset=['Illinois_Id', 'FederalTaxID'])
    
    return df

cognito_form_formatted = reformat_df(cognito_form)

cognito_form_formatted = cognito_form_formatted.dropna(subset=['FederalTaxID'])

cognito_form_formatted['FederalTaxID'] = cognito_form_formatted['FederalTaxID'].astype(int).astype(str)

#Set ZOOM API Credentials
credentials = f"{zoom_client_id}:{zoom_secret_id}"
encoded_credentials = base64.b64encode(credentials.encode()).decode()

headers = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Authorization": f"Basic {encoded_credentials}"
}

#ZOOM Auth Response Body
body = {
    "grant_type": "account_credentials",
    "account_id": zoom_account_id
}

token_url = "https://zoom.us/oauth/token"

#Fetch ZOOM Auth token
response = requests.post(token_url, headers=headers, data=body)
access_token = response.json().get('access_token')

#All webinars will come from one of these two users (hhaexchangewebinar,providerexperience)
user_ids = zoom_user_ids

headers = {
    "Authorization": f"Bearer {access_token}"
}

def clean_tax_ids(column):
#Use a regular expression to remove spaces and dashes for each value in the series
    return column.apply(lambda x: re.sub(r'[-\s]', '', x))

#Function to preprocess registrants and extract custom questions
def preprocess_registrants(registrants):
    for registrant in registrants:
        #Flatten custom questions
        for question in registrant.get('custom_questions', []):
            #Use the question title as the column name and its value as the value
            column_name = question['title']
            registrant[column_name] = question['value']
        registrant.pop('custom_questions', None)
    return registrants

#Function to construct URL for effective pagination of webinar participants
def construct_url(instance_id, next_page_token=None):
    url = f"https://api.zoom.us/v2/past_webinars/{instance_id}/participants"
    if next_page_token:
        url += f"?next_page_token={next_page_token}"
    return url

#Function to construct URL for effective pagination of webinar registrants
def construct_url_pre(webinar_id, next_page_token=None):
    url = f"https://api.zoom.us/v2/webinars/{webinar_id}/registrants"
    if next_page_token:
        url += f"?next_page_token={next_page_token}"
    return url

#Fetch all necessary webinar data for this session
try:
    info_session_all_webinars = []
    info_session_all_instances = []
    info_session_all_webinar_details = []
    info_session_all_webinar_details_reg = []

    for user_id in user_ids:
        base_url = f"https://api.zoom.us/v2/users/{user_id}/webinars"
        webinars_url = base_url
        next_page_token = None

        while True:
            if next_page_token:
                webinars_url = f"{base_url}?next_page_token={next_page_token}"
            
            response = requests.get(webinars_url, headers=headers)
            data = response.json()
            info_session_all_webinars.extend(data['webinars'])
            next_page_token = data.get('next_page_token')
            if not next_page_token:
                break
        
    #Store all webinar data in df, filter to only include in scope session. Isolate ids into list for use later
    df_webinars = pd.DataFrame(info_session_all_webinars)
    filtered_df = df_webinars[df_webinars['topic'] == 'IL Department on Aging Information Session Webinar ']
    webinar_id_isolated = filtered_df['id']
    webinar_ids = webinar_id_isolated.to_list()

    #Iterate through each webinar id to fetch all unique occurence uuid
    for webinar_id in webinar_ids:
        webinar_url = f"https://api.zoom.us/v2/past_webinars/{webinar_id}/instances"
        response = requests.get(webinar_url,headers=headers)
        instances_data = response.json()
        info_session_all_instances.extend(instances_data.get('webinars', []))

    info_session_occurrence_ids = [occurrence['uuid'] for occurrence in info_session_all_instances]

    #Iterate through each occurence to get participant details
    for instance in info_session_occurrence_ids:
        next_page_token = None
        while True:
            participants_url = construct_url(instance, next_page_token)
            response = requests.get(participants_url, headers=headers)
            participants_data = response.json()
            info_session_all_webinar_details.extend(participants_data.get('participants', []))
            next_page_token = participants_data.get('next_page_token')

            if not next_page_token:
                break

    #Store participant results in df
    webinars_df_participants = pd.DataFrame(info_session_all_webinar_details)

    #Extract occurence ids once again, this time extracting the id and not uuid
    for webinar_id in webinar_ids:
        webinar_url = f'https://api.zoom.us/v2/webinars/{webinar_id}?show_previous_occurrences=true'
        response = requests.get(webinar_url, headers=headers)
        webinar_data = response.json()
        occurrences = webinar_data.get('occurrences', [])
        
        occurrence_ids = [occurrence['occurrence_id'] for occurrence in occurrences]

        #Iterate through each occurence, store all registrant data
        for occurrence_id in occurrence_ids:
            next_page_token = ' '
            
            while True:
                webinars_url = f'https://api.zoom.us/v2/webinars/{webinar_id}/registrants?occurrence_id={occurrence_id}&next_page_token={next_page_token}'
                response = requests.get(webinars_url, headers=headers)
                registrant_data = response.json()
                registrants = preprocess_registrants(registrant_data.get('registrants', []))
                info_session_all_webinar_details_reg.extend(registrants)
                next_page_token = registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break
        
        #Get registrants for webinars that have not yet occurred
        for webinar_id in webinar_ids:
            next_page_token = None
                        
            while True:
                pre_webinar_url = construct_url_pre(webinar_id,next_page_token)
                response = requests.get(pre_webinar_url, headers=headers)
                pre_registrant_data = response.json()
                pre_registrants = preprocess_registrants(pre_registrant_data.get('registrants', []))
                info_session_all_webinar_details_reg.extend(pre_registrants)
                next_page_token = pre_registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

    #Store registrants in df
    webinars_df_registrants = pd.DataFrame(info_session_all_webinar_details_reg)

    #Merge registrant and partcipant dataframes
    if len(webinars_df_participants) > 0:
        info_session_merged_df = pd.merge(webinars_df_registrants,webinars_df_participants, left_on='id', right_on='registrant_id',how='left')
    else:
        info_session_merged_df = webinars_df_registrants

    #Call clean TaxID function
    info_session_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = clean_tax_ids(info_session_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except Exception as e:
    pass


#Fetch all necessary webinar data for this session
try:
    edi_all_webinars = []
    edi_all_instances = []
    edi_all_webinar_details = []
    edi_all_webinar_details_reg = []

    #Get all webinar IDs for these two users
    for user_id in user_ids:
        base_url = f"https://api.zoom.us/v2/users/{user_id}/webinars"
        webinars_url = base_url
        next_page_token = None

        while True:
            if next_page_token:
                webinars_url = f"{base_url}?next_page_token={next_page_token}"
            
            response = requests.get(webinars_url, headers=headers)
            data = response.json()
            edi_all_webinars.extend(data['webinars'])
            next_page_token = data.get('next_page_token')
            if not next_page_token:
                break

    #Store all webinar data in df, filter to only include in scope session. Isolate ids into list for use later
    df_webinars = pd.DataFrame(edi_all_webinars)
    filtered_df = df_webinars[df_webinars['topic'] == 'IL DOA EDI Webinar ']
    webinar_id_isolated = filtered_df['id']
    webinar_ids = webinar_id_isolated.to_list()

    #Iterate through each webinar id to fetch all unique occurence uuids
    for webinar_id in webinar_ids:
        webinar_url = f"https://api.zoom.us/v2/past_webinars/{webinar_id}/instances"
        response = requests.get(webinar_url,headers=headers)
        instances_data = response.json()
        edi_all_instances.extend(instances_data.get('webinars', []))

    edi_occurrence_ids = [occurrence['uuid'] for occurrence in edi_all_instances]

    #Iterate through each occurence to get participant details
    for instance in edi_occurrence_ids:
        next_page_token = None
        while True:
            participants_url = construct_url(instance, next_page_token)
            response = requests.get(participants_url, headers=headers)
            participants_data = response.json()
            edi_all_webinar_details.extend(participants_data.get('participants', []))
            next_page_token = participants_data.get('next_page_token')

            if not next_page_token:
                break

    #Store participant results in df
    edi_webinars_df_participants = pd.DataFrame(edi_all_webinar_details)

    #Extract occurence ids once again, this time extracting the id and not uuid
    for webinar_id in webinar_ids:
        webinar_url = f'https://api.zoom.us/v2/webinars/{webinar_id}?show_previous_occurrences=true'
        response = requests.get(webinar_url, headers=headers)
        webinar_data = response.json()
        occurrences = webinar_data.get('occurrences', [])
        
        occurrence_ids_2 = [occurrence['occurrence_id'] for occurrence in occurrences]

        #Iterate through each occurence, store all registrant data
        for occurrence_id in occurrence_ids_2:
            next_page_token = ' '
            
            while True:
                webinars_url = f'https://api.zoom.us/v2/webinars/{webinar_id}/registrants?occurrence_id={occurrence_id}&next_page_token={next_page_token}'
                response = requests.get(webinars_url, headers=headers)
                registrant_data = response.json()
                registrants = preprocess_registrants(registrant_data.get('registrants', []))
                edi_all_webinar_details_reg.extend(registrants)
                next_page_token = registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

        #Get registrants for webinars that have not yet occurred
        for webinar_id in webinar_ids:
            next_page_token = None
                        
            while True:
                pre_webinar_url = construct_url_pre(webinar_id,next_page_token)
                response = requests.get(pre_webinar_url, headers=headers)
                pre_registrant_data = response.json()
                pre_registrants = preprocess_registrants(pre_registrant_data.get('registrants', []))
                edi_all_webinar_details_reg.extend(pre_registrants)
                next_page_token = pre_registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

    #Store registrants in df
    edi_webinars_df_registrants = pd.DataFrame(edi_all_webinar_details_reg)

    #Merge registrant and partcipant dataframes
    if len(edi_webinars_df_participants) >0:
        edi_webinar_merged_df = pd.merge(edi_webinars_df_registrants,edi_webinars_df_participants, left_on='id', right_on='registrant_id',how='left')
    else:
        edi_webinar_merged_df = edi_webinars_df_registrants

    #Call clean TaxID function
    edi_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = clean_tax_ids(edi_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except Exception as e:
    pass

#Fetch all necessary webinar data for this session
try:
    sut_all_webinars = []
    sut_all_instances = []
    sut_all_webinar_details = []
    sut_all_webinar_details_reg = []

    #Get all webinar IDs for these two users
    for user_id in user_ids:
        base_url = f"https://api.zoom.us/v2/users/{user_id}/webinars"
        webinars_url = base_url
        next_page_token = None

        while True:
            if next_page_token:
                webinars_url = f"{base_url}?next_page_token={next_page_token}"
            
            response = requests.get(webinars_url, headers=headers)
            data = response.json()
            sut_all_webinars.extend(data['webinars'])
            next_page_token = data.get('next_page_token')
            if not next_page_token:
                break

    #Store all webinar data in df, filter to only include in scope session. Isolate ids into list for use later
    df_webinars = pd.DataFrame(sut_all_webinars)
    filtered_df = df_webinars[df_webinars['topic'] == 'IL DOA System User Training ']
    webinar_id_isolated = filtered_df['id']
    webinar_ids = webinar_id_isolated.to_list()

    #Iterate through each webinar id to fetch all unique occurence uuids
    for webinar_id in webinar_ids:
        webinar_url = f"https://api.zoom.us/v2/past_webinars/{webinar_id}/instances"
        response = requests.get(webinar_url,headers=headers)
        instances_data = response.json()
        sut_all_instances.extend(instances_data.get('webinars', []))

    sut_occurrence_ids = [occurrence['uuid'] for occurrence in sut_all_instances]

    #Iterate through each occurence to get participant details
    for instance in sut_occurrence_ids:
        next_page_token = None
        while True:
            participants_url = construct_url(instance, next_page_token)
            response = requests.get(participants_url, headers=headers)
            participants_data = response.json()
            sut_all_webinar_details.extend(participants_data.get('participants', []))
            next_page_token = participants_data.get('next_page_token')

            if not next_page_token:
                break

    #Store participant results in df
    sut_df_participants = pd.DataFrame(sut_all_webinar_details)

    #Extract occurence ids once again, this time extracting the id and not uuid
    for webinar_id in webinar_ids:
        webinar_url = f'https://api.zoom.us/v2/webinars/{webinar_id}?show_previous_occurrences=true'
        response = requests.get(webinar_url, headers=headers)
        webinar_data = response.json()
        occurrences = webinar_data.get('occurrences', [])
        
        occurrence_ids_2 = [occurrence['occurrence_id'] for occurrence in occurrences]

        #Iterate through each occurence, store all registrant data
        for occurrence_id in occurrence_ids_2:
            next_page_token = ' '
            
            while True:
                webinars_url = f'https://api.zoom.us/v2/webinars/{webinar_id}/registrants?occurrence_id={occurrence_id}&next_page_token={next_page_token}'
                response = requests.get(webinars_url, headers=headers)
                registrant_data = response.json()
                registrants = preprocess_registrants(registrant_data.get('registrants', []))
                sut_all_webinar_details_reg.extend(registrants)
                next_page_token = registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

        #Get registrants for webinars that have not yet occurred
        for webinar_id in webinar_ids:
            next_page_token = None
                        
            while True:
                pre_webinar_url = construct_url_pre(webinar_id,next_page_token)
                response = requests.get(pre_webinar_url, headers=headers)
                pre_registrant_data = response.json()
                pre_registrants = preprocess_registrants(pre_registrant_data.get('registrants', []))
                sut_all_webinar_details_reg.extend(pre_registrants)
                next_page_token = pre_registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

    #Store registrants in df
    sut_webinars_df_registrants = pd.DataFrame(sut_all_webinar_details_reg)

    #Merge registrant and partcipant dataframes
    if len(sut_df_participants) > 0:
        sut_webinar_merged_df = pd.merge(sut_webinars_df_registrants,sut_df_participants, left_on='id', right_on='registrant_id',how='left')
    else:
        sut_webinar_merged_df = sut_webinars_df_registrants

    #Call clean TaxID function
    sut_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = clean_tax_ids(sut_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except Exception as e:
    pass

#Fetch all necessary webinar data for this session
try:
    gs_all_webinars = []
    gs_all_instances = []
    gs_all_webinar_details = []
    gs_all_webinar_details_reg = []

    #Get all webinar IDs for these two users
    for user_id in user_ids:
        base_url = f"https://api.zoom.us/v2/users/{user_id}/webinars"
        webinars_url = base_url
        next_page_token = None

        while True:
            if next_page_token:
                webinars_url = f"{base_url}?next_page_token={next_page_token}"
            
            response = requests.get(webinars_url, headers=headers)
            data = response.json()
            gs_all_webinars.extend(data['webinars'])
            next_page_token = data.get('next_page_token')
            if not next_page_token:
                break

    #Store all webinar data in df, filter to only include in scope session. Isolate ids into list for use later
    df_webinars = pd.DataFrame(gs_all_webinars)
    filtered_df = df_webinars[df_webinars['topic'] == 'IL DOA Getting Started Webinar ']
    webinar_id_isolated = filtered_df['id']
    webinar_ids = webinar_id_isolated.to_list()

    #Iterate through each webinar id to fetch all unique occurence uuids
    for webinar_id in webinar_ids:
        webinar_url = f"https://api.zoom.us/v2/past_webinars/{webinar_id}/instances"
        response = requests.get(webinar_url,headers=headers)
        instances_data = response.json()
        gs_all_instances.extend(instances_data.get('webinars', []))

    gs_occurrence_ids = [occurrence['uuid'] for occurrence in gs_all_instances]

    #Iterate through each occurence to get participant details
    for instance in gs_occurrence_ids:
        next_page_token = None
        while True:
            participants_url = construct_url(instance, next_page_token)
            response = requests.get(participants_url, headers=headers)
            participants_data = response.json()
            gs_all_webinar_details.extend(participants_data.get('participants', []))
            next_page_token = participants_data.get('next_page_token')

            if not next_page_token:
                break

    #Store participant results in df
    gs_webinars_df_participants = pd.DataFrame(gs_all_webinar_details)

    #Extract occurence ids once again, this time extracting the id and not uuid
    for webinar_id in webinar_ids:
        webinar_url = f'https://api.zoom.us/v2/webinars/{webinar_id}?show_previous_occurrences=true'
        response = requests.get(webinar_url, headers=headers)
        webinar_data = response.json()
        occurrences = webinar_data.get('occurrences', [])
        
        occurrence_ids_2 = [occurrence['occurrence_id'] for occurrence in occurrences]

            #Iterate through each occurence, store all registrant data
        for occurrence_id in occurrence_ids_2:
            next_page_token = ' '
            
            while True:
                webinars_url = f'https://api.zoom.us/v2/webinars/{webinar_id}/registrants?occurrence_id={occurrence_id}&next_page_token={next_page_token}'
                response = requests.get(webinars_url, headers=headers)
                registrant_data = response.json()
                registrants = preprocess_registrants(registrant_data.get('registrants', []))
                gs_all_webinar_details_reg.extend(registrants)
                next_page_token = registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

        #Get registrants for webinars that have not yet occurred
        for webinar_id in webinar_ids:
            next_page_token = None
                        
            while True:
                pre_webinar_url = construct_url_pre(webinar_id,next_page_token)
                response = requests.get(pre_webinar_url, headers=headers)
                pre_registrant_data = response.json()
                pre_registrants = preprocess_registrants(pre_registrant_data.get('registrants', []))
                gs_all_webinar_details_reg.extend(pre_registrants)
                next_page_token = pre_registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

    #Store registrants in df
    gs_webinars_df_registrants = pd.DataFrame(gs_all_webinar_details_reg)

    #Merge registrant and partcipant dataframes
    if len(gs_webinars_df_participants)>0:
        gs_webinar_merged_df = pd.merge(gs_webinars_df_registrants,gs_webinars_df_participants, left_on='id', right_on='registrant_id',how='left')
    else:
        gs_webinar_merged_df = gs_webinars_df_registrants

    #Call clean TaxID function
    gs_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = clean_tax_ids(gs_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except Exception as e:
    pass

#Fetch all necessary webinar data for this session
try:
    openhours_all_webinars = []
    openhours_all_instances = []
    openhours_all_webinar_details = []
    openhours_all_webinar_details_reg = []

    #Get all webinar IDs for these two users
    for user_id in user_ids:
        base_url = f"https://api.zoom.us/v2/users/{user_id}/webinars"
        webinars_url = base_url
        next_page_token = None

        while True:
            if next_page_token:
                webinars_url = f"{base_url}?next_page_token={next_page_token}"
            
            response = requests.get(webinars_url, headers=headers)
            data = response.json()
            openhours_all_webinars.extend(data['webinars'])
            next_page_token = data.get('next_page_token')
            if not next_page_token:
                break

    #Store all webinar data in df, filter to only include in scope session. Isolate ids into list for use later
    df_webinars = pd.DataFrame(openhours_all_webinars)
    filtered_df = df_webinars[df_webinars['topic'] == 'IL DOA  HHAeXchange Open Hours']
    webinar_id_isolated = filtered_df['id']
    webinar_ids = webinar_id_isolated.to_list()

    #Iterate through each webinar id to fetch all unique occurence uuids
    for webinar_id in webinar_ids:
        webinar_url = f"https://api.zoom.us/v2/past_webinars/{webinar_id}/instances"
        response = requests.get(webinar_url,headers=headers)
        instances_data = response.json()
        openhours_all_instances.extend(instances_data.get('webinars', []))

    openhours_occurrence_ids = [occurrence['uuid'] for occurrence in openhours_all_instances]

    #Iterate through each occurence to get participant details
    for instance in openhours_occurrence_ids:
        next_page_token = None
        while True:
            participants_url = construct_url(instance, next_page_token)
            response = requests.get(participants_url, headers=headers)
            participants_data = response.json()
            openhours_all_webinar_details.extend(participants_data.get('participants', []))
            next_page_token = participants_data.get('next_page_token')

            if not next_page_token:
                break

    #Store participant results in df
    openhours_webinars_df_participants = pd.DataFrame(openhours_all_webinar_details)

    #Extract occurence ids once again, this time extracting the id and not uuid
    for webinar_id in webinar_ids:
        webinar_url = f'https://api.zoom.us/v2/webinars/{webinar_id}?show_previous_occurrences=true'
        response = requests.get(webinar_url, headers=headers)
        webinar_data = response.json()
        occurrences = webinar_data.get('occurrences', [])
        
        occurrence_ids_2 = [occurrence['occurrence_id'] for occurrence in occurrences]

        #Iterate through each occurence, store all registrant data
        for occurrence_id in occurrence_ids_2:
            next_page_token = ' '
            
            while True:
                webinars_url = f'https://api.zoom.us/v2/webinars/{webinar_id}/registrants?occurrence_id={occurrence_id}&next_page_token={next_page_token}'
                response = requests.get(webinars_url, headers=headers)
                registrant_data = response.json()
                registrants = preprocess_registrants(registrant_data.get('registrants', []))
                openhours_all_webinar_details_reg.extend(registrants)
                next_page_token = registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

        #Get registrants for webinars that have not yet occurred
        for webinar_id in webinar_ids:
            next_page_token = None
                        
            while True:
                pre_webinar_url = construct_url_pre(webinar_id,next_page_token)
                response = requests.get(pre_webinar_url, headers=headers)
                pre_registrant_data = response.json()
                pre_registrants = preprocess_registrants(pre_registrant_data.get('registrants', []))
                openhours_all_webinar_details_reg.extend(pre_registrants)
                next_page_token = pre_registrant_data.get('next_page_token', '')

                if not next_page_token:
                    break

    #Store registrants in df
    openhours_webinars_df_registrants = pd.DataFrame(openhours_all_webinar_details_reg)

    #Merge registrant and partcipant dataframes
    if len(openhours_webinars_df_participants) > 0:
        openhours_webinar_merged_df = pd.merge(openhours_webinars_df_registrants,openhours_webinars_df_participants, left_on='id', right_on='registrant_id',how='left')
    else:
        openhours_webinar_merged_df = openhours_webinars_df_registrants

    #Call clean TaxID function
    openhours_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = clean_tax_ids(openhours_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except Exception as e:
    pass

#Add column in cases where no attendance is yet available
if 'status_y' not in info_session_merged_df.columns:
    info_session_merged_df['status_y'] = np.nan

if 'status_y' not in edi_webinar_merged_df.columns:
    edi_webinar_merged_df['status_y'] = np.nan

if 'status_y' not in sut_webinar_merged_df.columns:
    sut_webinar_merged_df['status_y'] = np.nan

if 'status_y' not in gs_webinar_merged_df.columns:
    gs_webinar_merged_df['status_y'] = np.nan

if 'status_y' not in openhours_webinar_merged_df.columns:
    gs_webinar_merged_df['status_y'] = np.nan

ctx = snowflake.connector.connect(
    user=snowflake_user,
    account=snowflake_account,
    private_key=private_key_bytes,
    role=snowflake_role,
    warehouse=snowflake_bizops_wh)
    
cs = ctx.cursor()
script = """
select
tax_id,
marketing_engagement_type
from "PC_FIVETRAN_DB"."HUBSPOT"."MARKETING_ENGAGEMENTS"
where EVENT_NAME = 'IL Department on Aging Information Session Webinar' and tax_id is not null
"""
payload = cs.execute(script)
mark_engagement = pd.DataFrame.from_records(iter(payload), columns=[x[0] for x in payload.description])

in_person_info_session_reg = mark_engagement[mark_engagement['MARKETING_ENGAGEMENT_TYPE'] == 'event-registration']
in_person_info_session_attendee = mark_engagement[mark_engagement['MARKETING_ENGAGEMENT_TYPE'] == 'event-attendance']

script = """
select
"Federal Tax Number" as TAX_ID,
"Platform Type" as PLATFORM_TAG
from "ANALYTICS"."BI"."DIMPROVIDER"
"""

payload = cs.execute(script)
portals = pd.DataFrame.from_records(iter(payload), columns=[x[0] for x in payload.description])

#Distinguish between attendees and registress for each webinar
try:
    info_session_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = info_session_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'].astype(str).str.strip()
    info_session_in_meeting_df = info_session_merged_df[info_session_merged_df['status_y'] == 'in_meeting']
    illinois_jumpoff['ATTENDED_INFO_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(info_session_in_meeting_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
    illinois_jumpoff['REGISTERED_INFO_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(info_session_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except (NameError, KeyError):
    illinois_jumpoff['ATTENDED_INFO_SESSION'] = False
    illinois_jumpoff['REGISTERED_INFO_SESSION'] = False

try:
    edi_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = edi_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'].astype(str).str.strip()
    edi_webinar_in_meeting_df = edi_webinar_merged_df[edi_webinar_merged_df['status_y'] == 'in_meeting']
    illinois_jumpoff['ATTENDED_EDI_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(edi_webinar_in_meeting_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
    illinois_jumpoff['REGISTERED_EDI_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(edi_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except (NameError, KeyError):
    illinois_jumpoff['ATTENDED_EDI_SESSION'] = False
    illinois_jumpoff['REGISTERED_EDI_SESSION'] = False

try:
    sut_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = sut_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'].astype(str).str.strip()
    sut_webinar_in_meeting_df = sut_webinar_merged_df[sut_webinar_merged_df['status_y'] == 'in_meeting']
    illinois_jumpoff['ATTENDED_SUT_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(sut_webinar_in_meeting_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
    illinois_jumpoff['REGISTERED_SUT_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(sut_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except (NameError, KeyError):
    illinois_jumpoff['ATTENDED_SUT_SESSION'] = False
    illinois_jumpoff['REGISTERED_SUT_SESSION'] = False

try:
    gs_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = gs_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'].astype(str).str.strip()
    gs_webinar_in_meeting_df = gs_webinar_merged_df[gs_webinar_merged_df['status_y'] == 'in_meeting']
    illinois_jumpoff['ATTENDED_GS_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(gs_webinar_in_meeting_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
    illinois_jumpoff['REGISTERED_GS_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(gs_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except (NameError, KeyError):
    illinois_jumpoff['ATTENDED_GS_SESSION'] = False
    illinois_jumpoff['REGISTERED_GS_SESSION'] = False

try:
    openhours_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'] = openhours_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'].astype(str).str.strip()
    openhours_webinar_in_meeting_df = openhours_webinar_merged_df[openhours_webinar_merged_df['status_y'] == 'in_meeting']
    illinois_jumpoff['ATTENDED_OH_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(openhours_webinar_in_meeting_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
    illinois_jumpoff['REGISTERED_OH_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(openhours_webinar_merged_df['Please enter your Tax ID number (without dashes) for attendance purposes.'])
except (NameError, KeyError):
    illinois_jumpoff['ATTENDED_OH_SESSION'] = False
    illinois_jumpoff['REGISTERED_OH_SESSION'] = False

try:
    illinois_jumpoff['ATTENDED_IN_PERSON_INFO_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(in_person_info_session_attendee['TAX_ID'])
    illinois_jumpoff['REGISTERED_IN_PERSON_INFO_SESSION'] = illinois_jumpoff['Provider TAX ID'].isin(in_person_info_session_reg['TAX_ID'])
except (NameError, KeyError):
    illinois_jumpoff['ATTENDED_IN_PERSON_INFO_SESSION'] = False
    illinois_jumpoff['REGISTERED_IN_PERSON_INFO_SESSION'] = False

# #Get latest LMS data



# script = """
# select * from "PC_FIVETRAN_DB"."DOCEBO"."CUSTOM_LEARNING_PLAN"
# where ((learning_plan_name = 'Michigan Home Health Provider Learning Plan') or (learning_plan_name = 'Michigan Home Help Provider Learning Plan'))
# """
# payload = cs.execute(script)
# docebo_df = pd.DataFrame.from_records(iter(payload), columns=[x[0] for x in payload.description])

# docebo_df = docebo_df.dropna(subset=['AGENCY_TAX_ID'])

# health_docebo_df = docebo_df[docebo_df['LEARNING_PLAN_NAME'] == 'Michigan Home Health Provider Learning Plan']

# Merge michigan_jumpoff with health_docebo_df
#merged_health_df = pd.merge(michigan_jumpoff, health_docebo_df, left_on='Provider TAX ID', right_on='AGENCY_TAX_ID', how='left', suffixes=('_michigan', '_health'))

#Check append LMS status for each provider
illinois_jumpoff = illinois_jumpoff[['Provider TAX ID', 'Provider Name',
    'Provider Address 1', 'Provider City', 'Provider State',
    'Provider Zip Code', 'Provider Contact Name', 'Provider Email Address',
    'Provider Phone Number ', 'Wave', 'ATTENDED_INFO_SESSION',
    'REGISTERED_INFO_SESSION', 'ATTENDED_EDI_SESSION',
    'REGISTERED_EDI_SESSION', 'ATTENDED_SUT_SESSION',
    'REGISTERED_SUT_SESSION', 'ATTENDED_GS_SESSION',
    'REGISTERED_GS_SESSION', 'ATTENDED_OH_SESSION', 'REGISTERED_OH_SESSION','ATTENDED_IN_PERSON_INFO_SESSION','REGISTERED_IN_PERSON_INFO_SESSION']]

#lms_update_df['LEARNING_PLAN_ENROLLMENT_STATUS'] = lms_update_df['LEARNING_PLAN_ENROLLMENT_STATUS'].fillna('Not Registered')

final_merged_df = pd.merge(illinois_jumpoff, cognito_form_formatted, left_on='Provider TAX ID',right_on='FederalTaxID',how='left')

final_merged_df =  final_merged_df[['Provider TAX ID', 'Provider Name',
    'Provider Address 1', 'Provider City', 'Provider State',
    'Provider Zip Code', 'Provider Contact Name', 'Provider Email Address',
    'Provider Phone Number ', 'Wave', 'ATTENDED_INFO_SESSION',
    'REGISTERED_INFO_SESSION', 'ATTENDED_EDI_SESSION',
    'REGISTERED_EDI_SESSION', 'ATTENDED_SUT_SESSION',
    'REGISTERED_SUT_SESSION', 'ATTENDED_GS_SESSION',
    'REGISTERED_GS_SESSION', 'ATTENDED_OH_SESSION', 'REGISTERED_OH_SESSION',
    'ATTENDED_IN_PERSON_INFO_SESSION','REGISTERED_IN_PERSON_INFO_SESSION',
    'DoesYourAgencyCurrentlyUseAnEVVSystemToCaptureTheStartTimeEndTimeAndLocationOfTheMembersService']]

import_list = final_merged_df.rename(columns={'Provider TAX ID' : 'PROVIDER_TAX_ID', 'Provider Name' : 'PROVIDER_NAME',
    'Provider Address 1':'PROVIDER_ADDRESS_1', 'Provider City' : 'PROVIDER_CITY', 'Provider State' : 'PROVIDER_STATE',
    'Provider Zip Code' : 'PROVIDER_ZIP_CODE', 'Provider Contact Name' : 'PROVIDER_CONTACT_NAME', 'Provider Email Address' : 'PROVIDER_EMAIL_ADDRESS',
    'Provider Phone Number ' : 'PROVIDER_PHONE_NUMBER', 'Wave' : 'WAVE','DoesYourAgencyCurrentlyUseAnEVVSystemToCaptureTheStartTimeEndTimeAndLocationOfTheMembersService' : 'EVV_SYSTEM_CHOICE',})

import_list['EVV_SYSTEM_CHOICE'] = import_list['EVV_SYSTEM_CHOICE'].fillna('Missing Cognito Form')

import_list = import_list.applymap(str)

#Build time series dataset
now = datetime.now()
current_day = f"{now.day:02d}"
current_year = now.year
current_month = f"{now.month:02d}"

date = f"{current_month}/{current_day}/{current_year}"

time_series_dataframe = pd.DataFrame({'EVENT_DATE': [date]})

import_list['EVENT_DATE'] = date

import_list['PORTAL_CREATED'] = import_list['PROVIDER_TAX_ID'].isin(portals['TAX_ID']).astype(str)

import_list = import_list.merge(portals[['TAX_ID', 'PLATFORM_TAG']], left_on='PROVIDER_TAX_ID', right_on='TAX_ID', how='left')

import_list['PORTAL_TYPE'] = import_list['PLATFORM_TAG']

import_list.drop(columns=['TAX_ID','PLATFORM_TAG'], inplace=True)

time_series_dataframe['PROVIDER_COUNT'] = import_list['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['DOA_PROVIDER_COUNT'] = import_list.groupby(['PROVIDER_TAX_ID', 'WAVE']).ngroups
time_series_dataframe['COMPLETED_ONBOARDING_FORM'] = import_list[import_list['EVV_SYSTEM_CHOICE'] != 'Missing Cognito Form']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['REGISTERED_INFO_SESSION'] = import_list[import_list['REGISTERED_INFO_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['ATTENDED_INFO_SESSION'] = import_list[import_list['ATTENDED_INFO_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['REGISTERED_EDI_SESSION'] = import_list[import_list['REGISTERED_EDI_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['ATTENDED_EDI_SESSION'] = import_list[import_list['ATTENDED_EDI_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['REGISTERED_SUT_SESSION'] = import_list[import_list['REGISTERED_SUT_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['ATTENDED_SUT_SESSION'] = import_list[import_list['ATTENDED_SUT_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['REGISTERED_GS_SESSION'] = import_list[import_list['REGISTERED_GS_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['ATTENDED_GS_SESSION'] = import_list[import_list['ATTENDED_GS_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['REGISTERED_OH_SESSION'] = import_list[import_list['REGISTERED_OH_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['ATTENDED_OH_SESSION'] = import_list[import_list['ATTENDED_OH_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['YES_INTEGRATE_EDI'] = import_list[import_list['EVV_SYSTEM_CHOICE'] == 'Yes - I currently have my own EVV system and would like to integrate with HHAX (EDI)']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['YES_USE_HHAX'] = import_list[import_list['EVV_SYSTEM_CHOICE'] == 'Yes - I currently have my own EVV system but would like to use HHAX (Free EVV)']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['NO_EVV_SYSTEM'] = import_list[import_list['EVV_SYSTEM_CHOICE'] == 'No - I currently do not have my own EVV system and would like to use HHAX (Free EVV)']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['ATTENDED_IN_PERSON_INFO_SESSION'] = import_list[import_list['ATTENDED_IN_PERSON_INFO_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['REGISTERED_IN_PERSON_INFO_SESSION'] = import_list[import_list['REGISTERED_IN_PERSON_INFO_SESSION'] != 'False']['PROVIDER_TAX_ID'].nunique()
doa_portals_created = import_list[(import_list['WAVE'] == 'DOA') & (import_list['PORTAL_CREATED'] == 'True')]['PROVIDER_TAX_ID'].nunique()
time_series_dataframe['DOA_PORTALS_CREATED'] = doa_portals_created
time_series_dataframe['PORTALS_CREATED'] = import_list[import_list['PORTAL_CREATED'] == 'True']['PROVIDER_TAX_ID'].nunique()
# time_series_dataframe['LMS_NOTREGISTERED'] = import_list[import_list['LEARNING_PLAN_ENROLLMENT_STATUS'] == 'Not Registered']['PROVIDER_TAX_ID'].nunique()
# time_series_dataframe['LMS_ENROLLED'] = import_list[import_list['LEARNING_PLAN_ENROLLMENT_STATUS'] == 'Enrolled']['PROVIDER_TAX_ID'].nunique()
# time_series_dataframe['LMS_INPROGRESS'] = import_list[import_list['LEARNING_PLAN_ENROLLMENT_STATUS'] == 'In Progress']['PROVIDER_TAX_ID'].nunique()
# time_series_dataframe['LMS_COMPLETED'] = import_list[import_list['LEARNING_PLAN_ENROLLMENT_STATUS'] == 'Completed']['PROVIDER_TAX_ID'].nunique()

#Load trend data into Snowflake
time_series_dataframe['EVENT_DATE'] = pd.to_datetime(time_series_dataframe['EVENT_DATE'])
for col in time_series_dataframe.columns:
    if col != 'EVENT_DATE':
        time_series_dataframe[col] = pd.to_numeric(time_series_dataframe[col], errors='coerce').astype('Int64')
        
# Construct the SQLAlchemy connection string
connection_string = f"snowflake://{snowflake_user}@{snowflake_account}/{snowflake_fivetran_db}/CAMPAIGN_REPORTING?warehouse={snowflake_bizops_wh}&role={snowflake_role}&authenticator=externalbrowser"

# Instantiate SQLAlchemy engine with the private key
engine = create_engine(
    connection_string,
    connect_args={
        "private_key": private_key_bytes
    }
)

chunk_size = 10000
chunks = [x for x in range(0, len(time_series_dataframe), chunk_size)] + [len(time_series_dataframe)]
table_name = 'illinoistrend' 

import_list = import_list.drop_duplicates(subset=['WAVE', 'PROVIDER_TAX_ID'])

for i in range(len(chunks) - 1):
    time_series_dataframe[chunks[i]:chunks[i + 1]].to_sql(table_name, engine, if_exists='append', index=False)

import_list.drop(['EVENT_DATE'],axis=1,inplace=True)

import_list['EVENT_DATE'] = date

#Load row by row data into Snowflake
chunk_size = 1000
chunks = [x for x in range(0, len(import_list), chunk_size)] + [len(import_list)]
table_name = 'illinois' 

for i in range(len(chunks) - 1):
    import_list[chunks[i]:chunks[i + 1]].to_sql(table_name, engine, if_exists='append', index=False)

url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'
data = {
    'grant_type': 'client_credentials',
    'client_id': client_id,
    'client_secret': secret,
    'scope':  'https://graph.microsoft.com/.default'
}

response = requests.post(url, data=data)
response_json = response.json()

access_token = response_json.get('access_token')
hostname = 'hhaexchange.sharepoint.com'
site_relative_path = 'sites/AllEmployees'

url = f"https://graph.microsoft.com/v1.0/sites/hhaexchange.sharepoint.com:/sites/AllEmployees"
headers = {
    "Authorization": f"Bearer {access_token}"
}
response = requests.get(url, headers=headers)
site_data = response.json()
site_id = site_data.get("id")

headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/json"
}

response = requests.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives", headers=headers)

drive_id = None

# Check if the request was successful
if response.status_code == 200:
    drives = response.json().get('value', [])
    for drive in drives:
        # Check if drive name is "Documents" and store its ID
        if drive['name'] == 'Documents':
            drive_id = drive['id']
            break  # Exit the loop as we found the drive ID

current_date = now.strftime("%Y-%m-%d")
file_name = f'Illinois Campaign Report - {current_date}.xlsx'

destination_path = f'Campaign Reports/Illinois/{file_name}'

# Full endpoint to the folder
upload_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{destination_path}:/content"

# Create an Excel file in memory
output = io.BytesIO()

with pd.ExcelWriter(output, engine='openpyxl') as writer:
    import_list.to_excel(writer, index=False, sheet_name='Illinois')

# Move the cursor to the beginning of the stream
output.seek(0)

headers = {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
}

response = requests.put(upload_url, headers=headers, data=output)
