#!/usr/bin/env python3
"""
Utility script to prepare Google service account credentials for .env file.
Usage: python prepare_credentials.py path/to/service-account.json
E.g. python ~/portfolio/api/google/scripts/prepare_credentials.py ~/downloads/portfolio-450200-34e7805b4547.json
"""
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv('.env.development.local'))
import json
import sys
from pathlib import Path
import base64

def prepare_credentials(json_path: str) -> str:
    """
    Read a service account JSON file and convert it to a minified string
    suitable for use in a .env file.
    """
    try:
        # Read and parse the JSON file
        with open(json_path, 'r') as f:
            creds = json.load(f)
        
        # Base64 encode the entire credentials to avoid escaping issues
        json_bytes = json.dumps(creds, separators=(',', ':')).encode('utf-8')
        b64_str = base64.b64encode(json_bytes).decode('utf-8')
        
        # Ensure proper padding
        padding = 4 - (len(b64_str) % 4)
        if padding != 4:
            b64_str += '=' * padding
        
        # Create env var with the base64 string
        env_var = f'GOOGLE_SERVICE_ACCOUNT_CREDENTIALS="{b64_str}"'
        
        return env_var
    
    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(1)

def main():
    if len(sys.argv) != 2:
        print("Usage: python prepare_credentials.py path/to/service-account.json", file=sys.stderr)
        sys.exit(1)
    
    json_path = sys.argv[1]
    if not Path(json_path).is_file():
        print(f"Error: File not found: {json_path}", file=sys.stderr)
        sys.exit(1)
    
    env_var = prepare_credentials(json_path)
    
    # Debug: Show the exact content that will be written
    print("\nDebug Info:")
    print("----------------------------------------")
    print("1. Original JSON structure:")
    with open(json_path, 'r') as f:
        print(json.dumps(json.load(f), indent=2))
    
    print("\n2. Base64 format (what will be in .env):")
    print(env_var)
    
    print("\n3. Verification test:")
    try:
        # Test that we can decode it back
        b64_str = env_var.split('=', 1)[1].strip('"')
        # Add padding if needed
        padding = 4 - (len(b64_str) % 4)
        if padding != 4:
            b64_str += '=' * padding
        json_str = base64.b64decode(b64_str).decode('utf-8')
        parsed = json.loads(json_str)
        print("✓ Base64 encoding is valid")
        print("✓ JSON is valid")
        print("✓ Required fields present:", all(k in parsed for k in ['type', 'project_id', 'private_key', 'client_email']))
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        sys.exit(1)
    
    print("\nInstructions:")
    print("----------------------------------------")
    print("1. Open your .env file")
    print("2. If GOOGLE_SERVICE_ACCOUNT_CREDENTIALS exists, replace it")
    print("3. Otherwise, add this new line exactly as shown above")
    print("4. Make sure the line is not split across multiple lines")
    print("----------------------------------------")
    print("\nTo verify:")
    print("1. After adding to .env, run: python -m api.google.auth")
    print("2. It should show your service account email and scopes")

if __name__ == '__main__':
    main() 