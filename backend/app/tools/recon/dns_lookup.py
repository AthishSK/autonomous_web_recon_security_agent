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