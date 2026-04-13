import requests

def subdomain_enum(domain: str) -> dict:
    """Finds subdomains using crt.sh Certificate Transparency logs."""
    url = f"https://crt.sh/?q=%.{domain}&output=json"
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            # Extract names and deduplicate
            subdomains = set()
            for entry in data:
                name = entry.get('name_value')
                if name and '*' not in name: # ignore wildcards
                    subdomains.add(name.strip().lower())
            return {"subdomains": list(subdomains)}
        return {"error": "Failed to fetch from crt.sh"}
    except Exception as e:
        return {"error": f"Subdomain enum failed: {str(e)}"}