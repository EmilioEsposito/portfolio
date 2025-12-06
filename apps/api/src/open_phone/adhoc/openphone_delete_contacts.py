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
from apps.api.src.utils.password import verify_admin_auth
from apps.api.src.google.sheets import get_sheet_as_json

# using main() prevents pytest from running this file upon discovery
def main():
    contact_ids = [
    '67a6a26b4837c897bb929ddb',
    '67a6a2614837c897bb929db3',
    '67a6a26e4837c897bb929deb',
    '67a6a29d32d3048139dae246',
    '67a6a2674837c897bb929dcb',
    '67a6a28a4837c897bb929e3e',
    '67a6a2874837c897bb929e36',
    '67a6a2bc32d3048139dae2b6',
    '67a6a27b4837c897bb929e23',
    '67a6a2a732d3048139dae26e',
    '67a6a2a632d3048139dae266',
    '67a6a2914837c897bb929e56',
    '67a6a2c332d3048139dae2ce',
    '67a6a2c132d3048139dae2c6',
    '67a6a2be32d3048139dae2be',
    '67a6a2604837c897bb929dab',
    '67a6a28032d3048139dae20e',
    '67a6a28432d3048139dae21e',
    '67a6a2ba32d3048139dae2ae',
    '67a6a2654837c897bb929dc3',
    '67a6a2a432d3048139dae25e',
    '67a6a27d4837c897bb929e2e',
    '67a6a29332d3048139dae226',
    '67a6a2c932d3048139dae2e6',
    '67a6a2d04837c897bb929e5e',
    '67a6a2694837c897bb929dd3',
    '67a6a2ae32d3048139dae286',
    '67a6a2b332d3048139dae296',
    '67a6a2704837c897bb929df3',
    '67a6a29f32d3048139dae24e',
    '67a6a2774837c897bb929e13',
    '67a6a2c832d3048139dae2de',
    '67a6a2754837c897bb929e0b',
    '67a6a2ce32d3048139dae2f6',
    '67a6a2744837c897bb929e03',
    '67a6a28232d3048139dae216',
    '67a6a2724837c897bb929dfb',
    '67a6a2b032d3048139dae28e',
    '67a6a2634837c897bb929dbb',
    '67a6a2ac32d3048139dae27e',
    '67a6a2aa32d3048139dae276',
    '67a6a29932d3048139dae236',
    '67a6a2b532d3048139dae29e',
    '67a6a29b32d3048139dae23e',
    '67a6a28c4837c897bb929e46',
    '67a6a25e4837c897bb929da3',
    '67a6a26d4837c897bb929de3',
    '67a6a2cc32d3048139dae2ee',
    '67a6a25c4837c897bb929d9b',
    '6823e6f2e7bc891303a85595',
    '6823e5ddd52f37a873ef6d1c',
    ]
    contact_id = contact_ids[0]
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
            print(response.status_code)
            # pprint(response.json())
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