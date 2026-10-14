import requests

invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"

headers = {
    "Authorization": "Bearer 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",
    "Content-Type": "application/json",
}

payload = {
  "model": "google/gemma-3-12b-it",
  "max_tokens": 1024,
  "temperature": 0.5,
  "messages": [
    {
      "role": "user",
      "content": "What is 17 * 23?"
    }
  ]
}

try:
    response = requests.post(invoke_url, headers=headers, json=payload)
    print(f"Status: {response.status_code}")
    print(response.text)
except Exception as e:
    print(f"Error: {e}")
