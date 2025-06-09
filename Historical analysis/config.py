# config.py

# Replace with your actual API key
API_KEY = "9dcbb5e84ceaf493ef59318aebc7ad17"

SUBGRAPH_URL = (
    f"https://gateway.thegraph.com/api/{API_KEY}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}
