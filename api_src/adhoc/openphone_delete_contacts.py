from fastapi import APIRouter, Request, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, conint
import json
import logging
from pprint import pprint
import os
import base64
import hmac
import requests
from typing import List, Optional, Union, Dict, Any
from datetime import datetime
import time
from api_src.utils.password import verify_admin_auth
from api_src.google.sheets import get_sheet_as_json

contact_ids = [
'67a3e97874352083a59684d5',
]

# delete contacts
for contact_id in contact_ids:
    url = f"https://api.openphone.com/v1/contacts/{contact_id}"
    # User OpenPhone API to send a message
    api_key = os.getenv("OPEN_PHONE_API_KEY")
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    response = requests.delete(url, headers=headers)
    try:
        pprint(response.json())
    except:
        print(response.status_code)



# Get contact by external_id
url = f"https://api.openphone.com/v1/contacts/{contact_id}"
# User OpenPhone API to send a message
api_key = os.getenv("OPEN_PHONE_API_KEY")
headers = {
    "Authorization": api_key,
    "Content-Type": "application/json",
}
# Build query parameters
params = {"externalIds": ['e3174477846'], "maxResults": 10}

response = requests.get(url, headers=headers, params=params)
pprint(response.json())