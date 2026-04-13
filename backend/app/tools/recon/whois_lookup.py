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