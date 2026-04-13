import whois

def whois_lookup(domain: str) -> dict:
    """Retrieves WHOIS information for a given domain."""
    try:
        w = whois.whois(domain)
        return {
            "registrar": w.registrar,
            "creation_date": str(w.creation_date),
            "expiration_date": str(w.expiration_date),
            "name_servers": w.name_servers,
            "emails": w.emails
        }
    except Exception as e:
        return {"error": f"WHOIS lookup failed: {str(e)}"}
    

import dns.resolver

def dns_lookup(domain: str, record_type: str = 'A') -> dict:
    """Performs a DNS lookup for specific record types."""
    results = []
    try:
        answers = dns.resolver.resolve(domain, record_type)
        for rdata in answers:
            results.append(rdata.to_text())
        return {f"{record_type}_records": results}
    except Exception as e:
        return {"error": f"DNS resolution failed: {str(e)}"}

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