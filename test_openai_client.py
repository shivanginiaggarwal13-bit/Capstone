from openai import OpenAI

client = OpenAI(
  base_url = "https://integrate.api.nvidia.com/v1",
  api_key = "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"
)

completion = client.chat.completions.create(
  model="z-ai/glm-5.3-flash",
  messages=[{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"Which number is larger, 9.11 or 9.8?"}],
  temperature=0.5,
  top_p=1,
  max_tokens=1024,
  stream=False
)

print(completion.choices[0].message)
