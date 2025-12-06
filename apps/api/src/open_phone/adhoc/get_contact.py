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
from pprint import pprint

# using main() prevents pytest from running this file upon discovery
def main():
    # get custom fields 
    # https://api.openphone.com/v1/contact-custom-fields
    url = "https://api.openphone.com/v1/contact-custom-fields"
    api_key = os.getenv("OPEN_PHONE_API_KEY")
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    response = requests.get(url, headers=headers)
    pprint(response.json())

    # {'data': [{'key': '67a3fe231c0f12583994d994',
    #            'name': 'external_id',
    #            'type': 'string'},
    #           {'key': '67a69fc2ea4fe3a7edd09276',
    #            'name': 'Property',
    #            'type': 'string'},
    #           {'key': '67e1f12cd6d6910515ec7ca2',
    #            'name': 'Unit #',
    #            'type': 'string'},
    #           {'key': '67e97dfadd6d4a9758c1e433',
    #            'name': 'Lease End Date',
    #            'type': 'date'},
    #           {'key': '67e97df0dd6d4a9758c1e430',
    #            'name': 'Lease Start Date',
    #            'type': 'date'}]}

    contact_ids = [
    '67e9817f1540b3794e60fccb',
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
        response = requests.get(url, headers=headers)
        # pprint(response.json())
        json_response = response.json()['data']
        pprint(json_response)

    # Hide child attributes

    # customFields.value
    # string[] | nullrequired
    # ​
    # customFields.key
    # string
    # The identifying key for contact custom field.

    # Example:
    # "inbound-lead"

    # ​
    # customFields.id
    # string
    # The unique identifier for the contact custom field.

    # Example:
    # "66d0d87d534de8fd1c433cec3"

    json_response['customFields'].append({
        'key': 'Lease Start Date',
        'id': '67e97df0dd6d4a9758c1e430',
        'value': '2025-03-30T16:00:00.000+0000'
    })
    json_response['customFields'].append({
        'key': 'Lease End Date',
        'id': '67e97dfadd6d4a9758c1e433',
        'value': '2025-06-30T16:00:00.000+0000'
    })

    pprint(json_response)

    # {'data': {'createdAt': '2025-03-30T17:38:07.841Z',
    #           'createdByUserId': 'USXAiFJxgv',
    #           'customFields': [{'id': '67e9817f1540b3794e60fb27',
    #                             'key': '67a3fe231c0f12583994d994',
    #                             'name': 'external_id',
    #                             'type': 'string',
    #                             'value': 'e4013418338'},
    #                            {'id': '67e9817f1540b3794e60fbdb',
    #                             'key': '67a69fc2ea4fe3a7edd09276',
    #                             'name': 'Property',
    #                             'type': 'string',
    #                             'value': '320'},
    #                            {'id': '67e9817f1540b3794e60fc17',
    #                             'key': '67e1f12cd6d6910515ec7ca2',
    #                             'name': 'Unit #',
    #                             'type': 'string',
    #                             'value': '02'}],
    #           'defaultFields': {'company': '320',
    #                             'emails': [{'id': '67e9817f1540b3794e60fb9f',
    #                                         'name': 'Email',
    #                                         'value': 'francisaylward@gmail.com'}],
    #                             'firstName': '320-02 Francis',
    #                             'lastName': 'Aylward',
    #                             'phoneNumbers': [{'id': '67e9817f1540b3794e60fb63',
    #                                               'name': 'Phone Number',
    #                                               'value': '+14013418338'}],
    #                             'role': 'Tenant'},
    #           'externalId': None,
    #           'id': '67e9817f1540b3794e60fccb',
    #           'source': 'csv-v2',
    #           'sourceUrl': None,
    #           'updatedAt': '2025-03-30T17:38:07.841Z'}}
    # {'data': {'createdAt': '2025-03-30T17:38:07.933Z',
    #           'createdByUserId': 'USXAiFJxgv',
    #           'customFields': [{'id': '67e9817f1540b3794e60fb2d',
    #                             'key': '67a3fe231c0f12583994d994',
    #                             'name': 'external_id',
    #                             'type': 'string',
    #                             'value': 'e4127371930'},
    #                            {'id': '67e9817f1540b3794e60fbe1',
    #                             'key': '67a69fc2ea4fe3a7edd09276',
    #                             'name': 'Property',
    #                             'type': 'string',
    #                             'value': '320'},
    #                            {'id': '67e9817f1540b3794e60fc1d',
    #                             'key': '67e1f12cd6d6910515ec7ca2',
    #                             'name': 'Unit #',
    #                             'type': 'string',
    #                             'value': '01'}],
    #           'defaultFields': {'company': '320',
    #                             'emails': [{'id': '67e9817f1540b3794e60fba5',
    #                                         'name': 'Email',
    #                                         'value': 'jtindiepodcast@gmail.com'}],
    #                             'firstName': '320-01 Terrence',
    #                             'lastName': 'Bruce',
    #                             'phoneNumbers': [{'id': '67e9817f1540b3794e60fb69',
    #                                               'name': 'Phone Number',
    #                                               'value': '+14127371930'}],
    #                             'role': 'Tenant'},
    #           'externalId': None,
    #           'id': '67e9817f1540b3794e60fcd1',
    #           'source': 'csv-v2',
    #           'sourceUrl': None,
    #           'updatedAt': '2025-03-30T17:38:07.933Z'}}

    # update contact with patch
    url = f"https://api.openphone.com/v1/contacts/{contact_id}"

    json_response['source'] = 'api-patch'

    # Send PATCH request
    response = requests.patch(url, headers=headers, json=json_response)
    pprint(response.json())



if __name__ == "__main__":
    print("do nothing")
