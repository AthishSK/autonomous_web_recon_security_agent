import ssl
import socket
from datetime import datetime

def ssl_check(hostname: str, port: int = 443) -> dict:
    """Checks the SSL/TLS certificate of the target."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                
                # Format dates
                not_before = datetime.strptime(cert['notBefore'], '%b %d %H:%M:%S %Y %Z')
                not_after = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z')
                
                return {
                    "issuer": dict(x[0] for x in cert['issuer']),
                    "subject": dict(x[0] for x in cert['subject']),
                    "valid_from": str(not_before),
                    "valid_until": str(not_after),
                    "days_remaining": (not_after - datetime.utcnow()).days
                }
    except Exception as e:
        return {"error": f"SSL check failed: {str(e)}"}