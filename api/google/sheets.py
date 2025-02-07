"""
Utilities for interacting with Google Sheets API.
"""

from googleapiclient.discovery import build
import os
from typing import List, Any, Dict
from fastapi import HTTPException

from api.google.auth import get_service_credentials

def get_sheets_service():
    """
    Creates and returns an authorized Sheets API service instance.
    Uses the shared service account credentials.
    """
    try:
        credentials = get_service_credentials()
        service = build('sheets', 'v4', credentials=credentials)
        return service
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize Google Sheets service: {str(e)}"
        )

def read_sheet(spreadsheet_id: str, range_name: str) -> List[List[Any]]:
    """
    Reads data from a Google Sheet.
    
    Args:
        spreadsheet_id: The ID of the spreadsheet (from the URL)
        range_name: The A1 notation of the range to read
        
    Returns:
        List of rows, where each row is a list of values
    """
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()
        
        return result.get('values', [])
    
    except Exception as e:
        message = f"Failed to read Google Sheet."
        message += f"\nDid you share it with account: portfolio-app-service-account@portfolio-450200.iam.gserviceaccount.com?"
        message += f"\nError: {str(e)}"
        raise HTTPException(
            status_code=500,
            detail=message
        )

def get_sheet_as_json(spreadsheet_id: str, sheet_name: str = 'Sheet1') -> List[Dict[str, Any]]:
    """
    Reads a Google Sheet and converts it to a list of dictionaries.
    Assumes the first row contains headers which will be used as dictionary keys.
    
    Args:
        spreadsheet_id: The ID of the spreadsheet (from the URL)
        sheet_name: The name of the sheet to read from
        
    Returns:
        List of dictionaries where each dictionary represents a row,
        with keys from the header row and corresponding values.
        Empty cells are represented as empty strings.
    
    Example:
        For a sheet with headers "Name", "Age", "City" and two data rows:
        >>> get_sheet_as_json('spreadsheet_id')
        [
            {"Name": "John", "Age": "30", "City": "New York"},
            {"Name": "Jane", "Age": "25", "City": "San Francisco"}
        ]
    """
    try:
        # Read the entire sheet
        range_name = f'{sheet_name}'  # Adjust range as needed
        data = read_sheet(spreadsheet_id, range_name)
        
        if not data:
            return []
        
        # First row contains headers
        headers = data[0]
        
        # Convert remaining rows to dictionaries
        result = []
        for row in data[1:]:
            # Pad row with empty strings if it's shorter than headers
            row_padded = row + [''] * (len(headers) - len(row))
            row_dict = dict(zip(headers, row_padded))
            result.append(row_dict)
        
        return result
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert sheet to JSON: {str(e)}"
        ) 


def test_get_sheet_as_json():
    spreadsheet_id = '1Gi0Wrkwm-gfCnAxycuTzHMjdebkB5cDt8wwimdYOr_M'
    sheet_name = 'OpenPhone'
    print(get_sheet_as_json(spreadsheet_id, sheet_name))