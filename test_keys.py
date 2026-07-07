import os
import requests
from dotenv import load_dotenv

load_dotenv()

groq_key = os.getenv("GROQ_API_KEY")
print(f"Testing Groq API Key: {groq_key[:5]}...")
try:
    headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
    data = {"model": "llama3-8b-8192", "messages": [{"role": "user", "content": "hi"}]}
    res = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=data)
    print("Groq Response Status:", res.status_code)
    if res.status_code != 200:
        print(res.text)
except Exception as e:
    print("Groq Error:", e)

mem0_key = os.getenv("MEM0_API_KEY")
print(f"Testing Mem0 API Key: {mem0_key[:5]}...")
try:
    headers = {"Authorization": f"Token {mem0_key}", "Content-Type": "application/json"}
    res = requests.post("https://api.mem0.ai/v1/memories/", headers=headers, json={"messages": [{"role": "user", "content": "hi"}], "user_id": "test_user"})
    print("Mem0 Response Status:", res.status_code)
    if res.status_code != 200 and res.status_code != 201:
        print(res.text)
except Exception as e:
    print("Mem0 Error:", e)

eleven_key = os.getenv("ELEVENLABS_API_KEY")
print(f"Testing ElevenLabs API Key: {eleven_key[:5]}...")
try:
    headers = {"xi-api-key": eleven_key, "Content-Type": "application/json"}
    res = requests.get("https://api.elevenlabs.io/v1/voices", headers=headers)
    print("ElevenLabs Response Status:", res.status_code)
    if res.status_code != 200:
        print(res.text)
except Exception as e:
    print("ElevenLabs Error:", e)
