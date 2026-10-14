import requests

headers = {
    "Authorization": "Bearer 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",
    "Accept": "application/json"
}

response = requests.get("https://integrate.api.nvidia.com/v1/models", headers=headers)
print(f"Status: {response.status_code}")
models = response.json()

print("Available models:")
for m in models.get("data", []):
    print(f"  {m['id']}")
