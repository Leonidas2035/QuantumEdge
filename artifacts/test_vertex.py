import os
import json
from urllib import request, error
import google.auth
import google.auth.transport.requests

def main():
    try:
        credentials, project_id = google.auth.default()
        req = google.auth.transport.requests.Request()
        credentials.refresh(req)
        token = credentials.token
        print(f"Project ID: {project_id}")
        print(f"Token length: {len(token) if token else 0}")
        
        region = "us-central1"
        model = "gemini-1.5-flash"
        url = f"https://{region}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{region}/publishers/google/models/{model}:generateContent"
        
        payload_dict = {
            "contents": [{
                "role": "user",
                "parts": [{"text": "Hello, respond with a JSON object containing a greeting field."}]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "greeting": {"type": "STRING"}
                    },
                    "required": ["greeting"]
                }
            }
        }
        payload = json.dumps(payload_dict).encode("utf-8")
        
        req = request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            },
            method="POST"
        )
        
        with request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            parsed = json.loads(body)
            print("Response:")
            print(json.dumps(parsed, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
