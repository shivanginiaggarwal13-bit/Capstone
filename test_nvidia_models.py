import requests

invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"

headers = {
    "Authorization": "Bearer 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",
    "Content-Type": "application/json",
}

models_to_test = [
    "google/gemma-3-12b-it",
    "google/gemma-4-31b-it",
    "mistralai/mistral-large-2-instruct",
    "meta/llama-3.2-11b-vision-instruct",
]

for model in models_to_test:
    payload = {
        "model": model,
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}]
    }
    response = requests.post(invoke_url, headers=headers, json=payload)
    print(f"{model}: {response.status_code}")
    if response.status_code != 200:
        print(f"  Error: {response.text[:200]}")
