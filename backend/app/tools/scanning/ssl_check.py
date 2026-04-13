import ssl, socket, datetime

async def run_ssl_check(host: str) -> dict:
    try:
        ctx = ssl.create_default_context()
        conn = ctx.wrap_socket(socket.socket(), server_hostname=host)
        conn.settimeout(8)
        conn.connect((host, 443))
        cert = conn.getpeercert()
        conn.close()

        expiry_str  = cert["notAfter"]
        expiry_date = datetime.datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
        days_left   = (expiry_date - datetime.datetime.utcnow()).days

        return {
            "host":          host,
            "issuer":        dict(x[0] for x in cert.get("issuer", [])),
            "subject":       dict(x[0] for x in cert.get("subject", [])),
            "expiry":        expiry_str,
            "days_until_expiry": days_left,
            "expired":       days_left < 0,
            "expiring_soon": 0 <= days_left <= 30,
            "san":           cert.get("subjectAltName", []),
        }
    except ssl.SSLCertVerificationError as e:
        return {"host": host, "error": "cert_invalid", "detail": str(e)}
    except Exception as e:
        return {"host": host, "error": str(e)}