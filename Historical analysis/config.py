import os
from dotenv import load_dotenv
load_dotenv() 
API_KEY = os.getenv("GRAPH_API_KEY")
if not API_KEY:
    raise ValueError("GRAPH_API_KEY is not set in .env")

SUBGRAPH_URL = f"https://gateway.thegraph.com/api/{API_KEY}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}
