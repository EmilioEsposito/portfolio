# API vs API_SRC Directory

The `/api` directory hosts the FastAPI server.

It also is treated specially by Vercel. Any python file under `/api` will be treated as its own Serverless Function, which dramatically slows down the build time. 

Therefore, everything except for the `index.py` file should be in the `api_src` directory.
