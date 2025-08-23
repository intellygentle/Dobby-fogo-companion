# fogo_companion/llm_runner.py
import requests
import json

#  Fireworks.ai Runner Powered By Sentient Dobby
def run_fireworks_dobby(prompt: str, api_key: str) -> str:
    """
    Calls the Fireworks.ai API for the Dobby 70B model.
    """
    if not api_key:
        raise RuntimeError("No FIREWORKS_API_KEY provided.")

    url = "https://api.fireworks.ai/inference/v1/chat/completions"
    
    payload = {
        "model": "accounts/sentientfoundation/models/dobby-unhinged-llama-3-3-70b-new",
        "max_tokens": 4096,
        "top_p": 1,
        "top_k": 40,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "temperature": 0.3,  # encourages more diverse outputs
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=90)
        response.raise_for_status()  # Will raise an HTTPError for bad responses (4xx or 5xx)
        
        data = response.json()
        if data.get("choices") and len(data["choices"]) > 0:
            message = data["choices"][0].get("message", {})
            content = message.get("content", "")
            return content.strip()
        else:
            raise RuntimeError(f"Fireworks API returned unexpected data: {data}")
            
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"[Fireworks Dobby error] {e}")


# --- Main Provider Chain ---
def generate_answer(prompt: str, api_key: str) -> str:
    """
    Provider chain for Fogo Companion.
    NEW Order: Fireworks (70B)
    """
    # 1) Fireworks (Dobby 70B) - NEW PRIMARY
    try:
        print("Using Fireworks (Dobby 70B)...")
        return run_fireworks_dobby(prompt, api_key)
    except Exception as e:
        print(f"Fireworks (Dobby 70B) failed – {e}")

    raise RuntimeError("LLM providers failed.")